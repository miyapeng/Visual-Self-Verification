#!/usr/bin/env python3
"""Download and freeze the ChartMimic New Version (dataset-iclr) release."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY = "ChartMimic/ChartMimic"
REVISION = "0c46ca5103b03fea520fb6e5b2d87c8a0372aaf1"
ARCHIVE_NAME = "dataset-iclr.tar.gz"
ARCHIVE_SIZE = 388_886_108
ARCHIVE_SHA256 = "a9b774a165e2e534f02b269285736cb2eed6bdb433d603a66e835fd768336866"
EXPECTED_SPLITS = {
    "direct_1800": 1800,
    "direct_600": 600,
    "customized_1800": 1800,
    "customized_600": 600,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def archive_is_valid(path: Path) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == ARCHIVE_SIZE
        and sha256(path) == ARCHIVE_SHA256
    )


def download(archive_dir: Path) -> Path:
    destination = archive_dir / ARCHIVE_NAME
    if archive_is_valid(destination):
        print(f"[download] verified, skip: {destination}", flush=True)
        return destination

    partial = destination.with_suffix(destination.suffix + ".part")
    url = (
        f"https://huggingface.co/datasets/{REPOSITORY}/resolve/"
        f"{REVISION}/{ARCHIVE_NAME}"
    )
    print(f"[download] {url}", flush=True)
    subprocess.run(
        [
            "curl",
            "--location",
            "--fail",
            "--show-error",
            "--retry",
            "30",
            "--retry-all-errors",
            "--retry-delay",
            "5",
            "--continue-at",
            "-",
            "--output",
            str(partial),
            url,
        ],
        check=True,
    )
    actual_size = partial.stat().st_size
    actual_sha256 = sha256(partial)
    if actual_size != ARCHIVE_SIZE or actual_sha256 != ARCHIVE_SHA256:
        raise RuntimeError(
            f"Integrity failure: size={actual_size}, sha256={actual_sha256}"
        )
    partial.replace(destination)
    print(f"[download] frozen: {destination}", flush=True)
    return destination


def validate_archive(archive: Path) -> None:
    with tarfile.open(archive, "r:gz") as handle:
        for member in handle.getmembers():
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise RuntimeError(f"Unsafe archive member: {member.name}")
            if member.issym() or member.islnk():
                link_path = Path(member.linkname)
                if link_path.is_absolute() or ".." in link_path.parts:
                    raise RuntimeError(
                        f"Unsafe archive link: {member.name} -> {member.linkname}"
                    )


def extracted_is_complete(extracted: Path) -> bool:
    try:
        for split_name, expected_count in EXPECTED_SPLITS.items():
            split_dir = locate_split(extracted, split_name)
            for suffix in ("*.py", "*.pdf", "*.png"):
                if len(list(split_dir.rglob(suffix))) != expected_count:
                    return False
    except (OSError, RuntimeError):
        return False
    return True


def make_tree_readable(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        mode = path.stat().st_mode
        path.chmod(mode | (0o555 if path.is_dir() else 0o444))


def extract(archive: Path, extracted: Path) -> None:
    marker = extracted / f".{ARCHIVE_NAME}.complete"
    if (
        marker.is_file()
        and marker.read_text().strip() == ARCHIVE_SHA256
        and extracted_is_complete(extracted)
    ):
        print(f"[extract] verified marker, skip: {extracted}", flush=True)
        return
    if extracted_is_complete(extracted):
        marker.write_text(ARCHIVE_SHA256 + "\n", encoding="utf-8")
        print(f"[extract] verified existing data: {extracted}", flush=True)
        return
    existing = [path for path in extracted.iterdir() if path != marker]
    if existing:
        raise RuntimeError(
            f"Refusing to extract over an incomplete tree: {extracted}. "
            "Move it aside and rerun."
        )
    print(f"[extract] validating {archive}", flush=True)
    validate_archive(archive)
    print("[extract] staging on local storage", flush=True)
    with tempfile.TemporaryDirectory(prefix="chartmimic-") as temporary:
        staged = Path(temporary)
        subprocess.run(
            [
                "tar",
                "--extract",
                "--gzip",
                f"--file={archive}",
                f"--directory={staged}",
                "--no-same-owner",
                "--no-same-permissions",
            ],
            check=True,
        )
        make_tree_readable(staged)
        if not extracted_is_complete(staged):
            raise RuntimeError("Staged extraction did not contain all expected files")
        for split_name in EXPECTED_SPLITS:
            shutil.copytree(staged / split_name, extracted / split_name)
    make_tree_readable(extracted)
    if not extracted_is_complete(extracted):
        raise RuntimeError("Copied extraction did not contain all expected files")
    marker.write_text(ARCHIVE_SHA256 + "\n", encoding="utf-8")


def locate_split(extracted: Path, split_name: str) -> Path:
    direct = extracted / split_name
    if direct.is_dir():
        return direct
    matches = [path for path in extracted.rglob(split_name) if path.is_dir()]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one directory named {split_name!r}, found {len(matches)}"
        )
    return matches[0]


def freeze_metadata(root: Path, extracted: Path) -> None:
    splits: dict[str, dict[str, object]] = {}
    all_ids: list[str] = []
    for split_name, expected_count in EXPECTED_SPLITS.items():
        split_dir = locate_split(extracted, split_name)
        python_files = sorted(split_dir.rglob("*.py"))
        ids = [path.relative_to(split_dir).with_suffix("").as_posix() for path in python_files]
        if len(ids) != expected_count:
            raise RuntimeError(
                f"{split_name}: expected {expected_count} Python files, found {len(ids)}"
            )
        duplicate_ids = sorted({item for item in ids if ids.count(item) > 1})
        if duplicate_ids:
            raise RuntimeError(f"{split_name}: duplicate IDs: {duplicate_ids[:10]}")
        qualified_ids = [f"{split_name}/{item}" for item in ids]
        all_ids.extend(qualified_ids)
        splits[split_name] = {
            "count": len(ids),
            "directory": split_dir.relative_to(root).as_posix(),
            "instance_ids": qualified_ids,
            "pdf_count": len(list(split_dir.rglob("*.pdf"))),
            "png_count": len(list(split_dir.rglob("*.png"))),
            "python_count": len(python_files),
        }

    evaluation_ids = (
        splits["direct_600"]["instance_ids"]
        + splits["customized_600"]["instance_ids"]
    )
    (root / "instance_ids.txt").write_text(
        "\n".join(all_ids) + "\n", encoding="utf-8"
    )
    (root / "evaluation_instance_ids.txt").write_text(
        "\n".join(evaluation_ids) + "\n", encoding="utf-8"
    )
    (root / "splits.json").write_text(
        json.dumps(splits, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "checksums.sha256").write_text(
        f"{ARCHIVE_SHA256}  archives/{ARCHIVE_NAME}\n", encoding="utf-8"
    )
    manifest = {
        "archive": {
            "path": f"archives/{ARCHIVE_NAME}",
            "sha256": ARCHIVE_SHA256,
            "size": ARCHIVE_SIZE,
        },
        "benchmark": "ChartMimic",
        "code_repository": "https://github.com/ChartMimic/ChartMimic",
        "dataset_repository": REPOSITORY,
        "dataset_source": f"https://huggingface.co/datasets/{REPOSITORY}",
        "dataset_revision": REVISION,
        "evaluation_count": len(evaluation_ids),
        "evaluation_splits": ["direct_600", "customized_600"],
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "license": "Apache-2.0",
        "split_counts": {
            name: details["count"] for name, details in splits.items()
        },
        "total_count": len(all_ids),
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"[done] frozen {len(all_ids)} records; "
        f"evaluation records: {len(evaluation_ids)}",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "data/chartmimic",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    archive_dir = root / "archives"
    extracted = root / "extracted"
    archive_dir.mkdir(parents=True, exist_ok=True)
    extracted.mkdir(parents=True, exist_ok=True)

    archive = download(archive_dir)
    extract(archive, extracted)
    freeze_metadata(root, extracted)


if __name__ == "__main__":
    main()
