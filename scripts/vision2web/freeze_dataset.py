#!/usr/bin/env python3
"""Download and freeze the complete Vision2Web dataset at a fixed revision."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path


REVISION = "8f03299d92b9bd852e93852d0c21e8a4848ab661"
REPOSITORY = "zai-org/Vision2Web"
FILES = {
    "frontend.tar.gz": {
        "size": 2_633_451_348,
        "sha256": "121543b2f2f72d95384e19a08a60fa492399ce024a18389f834aa024bf6a5a3f",
    },
    "webpage.tar.gz": {
        "size": 431_238_708,
        "sha256": "d98e4ba6d47e17f5fa52842fe96320353663d4431e2bc6aec2767df64d62f09d",
    },
    "website.tar.gz": {
        "size": 1_043_509_357,
        "sha256": "de1bfa35c86046586539a3540c4f3ec1ef8c117aaa69871551fb815acd53b388",
    },
}
EXPECTED_COUNTS = {"webpage": 100, "frontend": 66, "website": 27}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def valid_archive(path: Path, metadata: dict[str, object]) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == metadata["size"]
        and sha256(path) == metadata["sha256"]
    )


def download(archives: Path, name: str, metadata: dict[str, object]) -> Path:
    destination = archives / name
    if valid_archive(destination, metadata):
        print(f"[download] verified, skip: {destination}", flush=True)
        return destination

    partial = destination.with_suffix(destination.suffix + ".part")
    url = (
        f"https://huggingface.co/datasets/{REPOSITORY}/resolve/"
        f"{REVISION}/archives/{name}"
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
    if actual_size != metadata["size"] or actual_sha256 != metadata["sha256"]:
        raise RuntimeError(
            f"Integrity failure for {name}: size={actual_size}, sha256={actual_sha256}"
        )
    partial.replace(destination)
    print(f"[download] frozen: {destination}", flush=True)
    return destination


def validate_tar_members(archive: Path) -> None:
    with tarfile.open(archive, "r:gz") as handle:
        for member in handle.getmembers():
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"Unsafe member in {archive}: {member.name}")
            if member.issym() or member.islnk():
                target = Path(member.linkname)
                if target.is_absolute() or ".." in target.parts:
                    raise RuntimeError(
                        f"Unsafe link in {archive}: {member.name} -> {member.linkname}"
                    )


def extract(archive: Path, extracted: Path) -> None:
    marker = extracted / f".{archive.name}.complete"
    if marker.is_file() and marker.read_text().strip() == sha256(archive):
        print(f"[extract] verified marker, skip: {archive.name}", flush=True)
        return
    print(f"[extract] validating: {archive.name}", flush=True)
    validate_tar_members(archive)
    print(f"[extract] extracting: {archive.name}", flush=True)
    subprocess.run(["tar", "-xzf", str(archive), "-C", str(extracted)], check=True)
    marker.write_text(sha256(archive) + "\n", encoding="utf-8")


def discover_projects(extracted: Path) -> dict[str, list[str]]:
    projects: dict[str, list[str]] = {}
    for task_type in EXPECTED_COUNTS:
        task_root = extracted / task_type
        projects[task_type] = sorted(
            path.name
            for path in task_root.iterdir()
            if path.is_dir()
            and (path / "prototypes").is_dir()
            and (path / "workflow.json").is_file()
        ) if task_root.is_dir() else []
    return projects


def freeze_metadata(root: Path, extracted: Path) -> None:
    projects = discover_projects(extracted)
    counts = {task: len(names) for task, names in projects.items()}
    if counts != EXPECTED_COUNTS:
        raise RuntimeError(f"Unexpected project counts: {counts}; expected {EXPECTED_COUNTS}")

    identifiers = [
        f"{task_type}/{name}"
        for task_type in ("webpage", "frontend", "website")
        for name in projects[task_type]
    ]
    (root / "instance_ids.txt").write_text(
        "\n".join(identifiers) + "\n", encoding="utf-8"
    )
    checksums = [
        f"{metadata['sha256']}  archives/{name}"
        for name, metadata in FILES.items()
    ]
    (root / "checksums.sha256").write_text(
        "\n".join(checksums) + "\n", encoding="utf-8"
    )
    manifest = {
        "benchmark": "Vision2Web",
        "repository": REPOSITORY,
        "source": f"https://huggingface.co/datasets/{REPOSITORY}",
        "revision": REVISION,
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "license_from_upstream_readme": "CC-BY-NC-SA-4.0",
        "archives": {
            name: {
                **metadata,
                "path": f"archives/{name}",
            }
            for name, metadata in FILES.items()
        },
        "task_counts": counts,
        "total_tasks": len(identifiers),
        "instance_ids_file": "instance_ids.txt",
        "extracted_directory": "extracted",
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"[done] frozen {len(identifiers)} Vision2Web tasks in {root}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "data/vision2web",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    archives = root / "archives"
    extracted = root / "extracted"
    archives.mkdir(parents=True, exist_ok=True)
    extracted.mkdir(parents=True, exist_ok=True)

    downloaded = [
        download(archives, name, metadata) for name, metadata in FILES.items()
    ]
    for archive in downloaded:
        extract(archive, extracted)
    freeze_metadata(root, extracted)


if __name__ == "__main__":
    main()
