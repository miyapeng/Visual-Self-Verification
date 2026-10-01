#!/usr/bin/env python3
"""Download and freeze the public SWE-bench Multimodal dev split.

The output deliberately separates agent-visible inputs from evaluator-only
oracle data.  It also downloads image assets by content hash so experiments do
not silently depend on mutable external URLs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import pyarrow.parquet as pq
import requests
from huggingface_hub import HfApi, hf_hub_download


DEFAULT_REPO_ID = "SWE-bench/SWE-bench_Multimodal"
# Current upstream head on 2026-08-12.  A commit prefix is intentional: the
# Hub API resolves it to a full immutable commit before downloading anything.
DEFAULT_REVISION = "3548373"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "data" / "swe_mm" / "dev"

# These original Cloudup links have expired TLS/origin service, but the GitHub
# issue pages still expose their content-addressed Camo copies.  Keeping the
# mapping explicit makes the provenance auditable instead of silently swapping
# sources.  The original URL remains the identity in image_manifest.jsonl.
KNOWN_MIRRORS = {
    "https://cldup.com/T9ECZ-HxdS.png": "https://camo.githubusercontent.com/294aa0fef8d536742b14ef163e6df7f5acd9dc836d73d3cf0451bb1dee3806b4/68747470733a2f2f636c6475702e636f6d2f543945435a2d487864532e706e67",
    "https://cldup.com/lfJaNoj9ci.png": "https://camo.githubusercontent.com/ccd86ceb70a4c2a20ec8513d22a0fbca9c87d97833924f596d8fd589a419a676/68747470733a2f2f636c6475702e636f6d2f6c664a614e6f6a3963692e706e67",
    "https://cldup.com/vOjXnIxw8r.png": "https://camo.githubusercontent.com/25a8b7c92b5a3cab8e451c741d6725d684b0db378cb80bb91bfb1bd952429d25/68747470733a2f2f636c6475702e636f6d2f764f6a586e49787738722e706e67",
    "https://cldup.com/7TLyDhlufk.png": "https://camo.githubusercontent.com/883d62d3e5c443faaa2a4e6982f54d402dd0f3b201787256829bf534976310c8/68747470733a2f2f636c6475702e636f6d2f37544c7944686c75666b2e706e67",
    "https://cldup.com/06u0fqzCrV.png": "https://camo.githubusercontent.com/f263a5017d2dd1fcaca394f450d1d0d2dbf87a2f0391bf78380d3382368aa9c4/68747470733a2f2f636c6475702e636f6d2f3036753066717a4372562e706e67",
    "https://cldup.com/A0Nsu9q2Uw.png": "https://camo.githubusercontent.com/f95f8230c8e88bc88ebb5b7f75cd410aa9d5df2f941224909b0ff61dfe0a019c/68747470733a2f2f636c6475702e636f6d2f41304e737539713255772e706e67",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    atomic_write(
        path,
        (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode(),
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n" for row in rows
    )
    atomic_write(path, payload.encode())


def parse_assets(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def collect_urls(value: Any, context: tuple[str, ...] = ()) -> list[tuple[str, tuple[str, ...]]]:
    found: list[tuple[str, tuple[str, ...]]] = []
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            found.append((value, context))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(collect_urls(item, context + (str(index),)))
    elif isinstance(value, dict):
        for key, item in value.items():
            found.extend(collect_urls(item, context + (str(key),)))
    return found


def context_is_agent_visible(context: tuple[str, ...]) -> bool:
    return bool(context) and context[0] == "problem_statement"


def extension_for(url: str, content_type: str) -> str:
    content_type = content_type.split(";", 1)[0].strip().lower()
    extension = mimetypes.guess_extension(content_type) if content_type else None
    if extension == ".jpe":
        extension = ".jpg"
    if extension:
        return extension
    suffix = Path(urlparse(url).path).suffix.lower()
    if 1 < len(suffix) <= 8:
        return suffix
    return ".bin"


def download_asset(
    session: requests.Session,
    url: str,
    output_dir: Path,
    timeout: float,
    retries: int,
) -> dict[str, Any]:
    url_key = hashlib.sha256(url.encode()).hexdigest()
    resume_record = output_dir / ".by_url" / f"{url_key}.json"
    if resume_record.exists():
        record = json.loads(resume_record.read_text())
        local_path = output_dir.parent / record["local_path"]
        if local_path.exists() and sha256_file(local_path) == record["sha256"]:
            return record

    last_error = "unknown error"
    candidates = [KNOWN_MIRRORS[url], url] if url in KNOWN_MIRRORS else [url]
    for attempt in range(1, retries + 1):
        candidate = candidates[min(attempt - 1, len(candidates) - 1)]
        try:
            response = session.get(candidate, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
            content = response.content
            if not content:
                raise ValueError("empty response")
            content_type = response.headers.get("content-type", "")
            digest = sha256_bytes(content)
            extension = extension_for(url, content_type)
            destination = output_dir / digest[:2] / f"{digest}{extension}"
            if not destination.exists():
                atomic_write(destination, content)
            record = {
                "url": url,
                "resolved_url": response.url,
                "sha256": digest,
                "bytes": len(content),
                "content_type": content_type,
                "local_path": destination.relative_to(output_dir.parent).as_posix(),
            }
            write_json(resume_record, record)
            return record
        except Exception as exc:  # network errors need to be checkpointed per URL
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(min(2 ** (attempt - 1), 8))
    return {"url": url, "error": last_error}


def git_head(path: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-id", default=DEFAULT_REPO_ID)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--swebench-root", type=Path, default=Path(__file__).resolve().parents[3] / "Benchmarks" / "SWE-bench")
    parser.add_argument("--skip-images", action="store_true")
    parser.add_argument("--image-timeout", type=float, default=60.0)
    parser.add_argument("--image-retries", type=int, default=3)
    parser.add_argument("--image-workers", type=int, default=16)
    args = parser.parse_args()

    output = args.output.resolve()
    raw_root = output / "evaluator_private" / "hf_snapshot"
    agent_root = output / "agent_visible"
    image_root = output / "images"
    for directory in (raw_root, agent_root, image_root):
        directory.mkdir(parents=True, exist_ok=True)

    api = HfApi()
    info = api.dataset_info(args.repo_id, revision=args.revision, files_metadata=True)
    full_revision = info.sha
    repo_files = api.list_repo_files(args.repo_id, repo_type="dataset", revision=full_revision)
    dev_files = sorted(
        name for name in repo_files if name.endswith(".parquet") and "/dev-" in f"/{name}"
    )
    if not dev_files:
        raise RuntimeError(f"No dev parquet files found in {args.repo_id}@{full_revision}")

    downloaded: list[Path] = []
    for filename in dev_files:
        path = Path(
            hf_hub_download(
                args.repo_id,
                filename=filename,
                repo_type="dataset",
                revision=full_revision,
                local_dir=raw_root,
            )
        )
        downloaded.append(path)
    for filename in ("README.md", ".gitattributes"):
        if filename in repo_files:
            hf_hub_download(
                args.repo_id,
                filename=filename,
                repo_type="dataset",
                revision=full_revision,
                local_dir=raw_root,
            )

    rows: list[dict[str, Any]] = []
    schema = None
    for parquet_path in downloaded:
        table = pq.read_table(parquet_path)
        schema = table.schema if schema is None else schema
        rows.extend(table.to_pylist())
    rows.sort(key=lambda row: row["instance_id"])
    instance_ids = [row["instance_id"] for row in rows]
    if len(instance_ids) != len(set(instance_ids)):
        raise RuntimeError("Duplicate instance_id values found")

    url_uses: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        assets = parse_assets(row.get("image_assets"))
        for url, context in collect_urls(assets):
            url_uses[url].append(
                {
                    "instance_id": row["instance_id"],
                    "context": list(context),
                    "agent_visible": context_is_agent_visible(context),
                }
            )

    asset_records: dict[str, dict[str, Any]] = {}
    if not args.skip_images:
        def fetch(url: str) -> tuple[str, dict[str, Any]]:
            # A Session is intentionally local to each task: requests.Session
            # is not guaranteed to be thread-safe.
            session = requests.Session()
            session.headers["User-Agent"] = "MultimodalCode-SWEMM-Freezer/1.0"
            return url, download_asset(
                session, url, image_root, args.image_timeout, args.image_retries
            )

        urls = sorted(url_uses)
        with ThreadPoolExecutor(max_workers=max(1, args.image_workers)) as executor:
            futures = {executor.submit(fetch, url): url for url in urls}
            for index, future in enumerate(as_completed(futures), 1):
                url = futures[future]
                try:
                    _, asset_records[url] = future.result()
                except Exception as exc:
                    asset_records[url] = {"url": url, "error": f"{type(exc).__name__}: {exc}"}
                status = "ok" if "sha256" in asset_records[url] else "failed"
                print(f"[image {index}/{len(urls)}] {status}: {url}", flush=True)

    image_manifest: list[dict[str, Any]] = []
    for url in sorted(url_uses):
        image_manifest.append({**asset_records.get(url, {"url": url, "status": "skipped"}), "uses": url_uses[url]})
    write_jsonl(output / "image_manifest.jsonl", image_manifest)

    full_rows: list[dict[str, Any]] = []
    agent_rows: list[dict[str, Any]] = []
    local_images_by_instance: dict[str, list[dict[str, str]]] = defaultdict(list)
    for record in image_manifest:
        if "error" in record or "local_path" not in record:
            continue
        for use in record["uses"]:
            if use["agent_visible"]:
                local_images_by_instance[use["instance_id"]].append(
                    {
                        "source_url": record["url"],
                        "local_path": record["local_path"],
                        "sha256": record["sha256"],
                    }
                )
    for row in rows:
        full = dict(row)
        full["image_assets"] = parse_assets(full.get("image_assets"))
        full_rows.append(full)
        agent_rows.append(
            {
                "repo": row.get("repo"),
                "instance_id": row["instance_id"],
                "base_commit": row.get("base_commit"),
                "problem_statement": row.get("problem_statement"),
                "version": row.get("version"),
                "images": sorted(local_images_by_instance[row["instance_id"]], key=lambda item: item["source_url"]),
            }
        )

    write_jsonl(output / "evaluator_private" / "instances.full.jsonl", full_rows)
    write_jsonl(agent_root / "instances.jsonl", agent_rows)
    atomic_write(output / "instance_ids.txt", ("\n".join(instance_ids) + "\n").encode())

    failures = [record for record in image_manifest if "error" in record]
    repo_counts = Counter(row.get("repo", "") for row in rows)
    freeze = {
        "format_version": 1,
        "complete": not failures and not args.skip_images,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": {
            "repo_id": args.repo_id,
            "requested_revision": args.revision,
            "resolved_revision": full_revision,
            "split": "dev",
            "parquet_files": [path.relative_to(output).as_posix() for path in downloaded],
        },
        "evaluator": {
            "path": str(args.swebench_root.resolve()),
            "git_commit": git_head(args.swebench_root),
        },
        "dataset": {
            "instances": len(rows),
            "fields": list(schema.names if schema is not None else []),
            "repositories": dict(sorted(repo_counts.items())),
        },
        "images": {
            "unique_urls": len(url_uses),
            "downloaded": sum("sha256" in record for record in image_manifest),
            "failed": len(failures),
            "skipped": args.skip_images,
        },
        "environment": {
            "python": sys.version.split()[0],
            "datasets_are_oracle_separated": True,
        },
    }
    write_json(output / "freeze_manifest.json", freeze)

    checksum_targets = [
        path for path in output.rglob("*")
        if path.is_file()
        and path.name not in {"checksums.sha256", "freeze_manifest.json"}
        and ".cache" not in path.parts
        and ".by_url" not in path.parts
        and not path.name.endswith(".part")
    ]
    checksum_lines = [
        f"{sha256_file(path)}  {path.relative_to(output).as_posix()}"
        for path in sorted(checksum_targets)
    ]
    atomic_write(output / "checksums.sha256", ("\n".join(checksum_lines) + "\n").encode())

    print(json.dumps(freeze, ensure_ascii=False, indent=2), flush=True)
    if failures:
        print(f"ERROR: {len(failures)} image assets failed; rerun to resume", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
