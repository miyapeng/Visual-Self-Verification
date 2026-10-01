#!/usr/bin/env python3
"""Mirror frozen SWE-MM evaluation images into a private OCI registry.

This script deliberately uses skopeo instead of Docker.  It therefore runs in
the restricted development container and does not require a Docker daemon or
privileged mode.  Registry credentials are handled by ``skopeo login`` and are
never accepted as command-line arguments here.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = (
    PROJECT_ROOT
    / "data/swe_mm/dev/evaluator_private/instances.full.jsonl"
)
DEFAULT_SKOPEO = PROJECT_ROOT / ".envs/imgsync/bin/skopeo"
DEFAULT_REGISTRIES_CONF = Path(__file__).with_name("registries.conf")
DEFAULT_STATE = PROJECT_ROOT / "data/swe_mm/dev/private_images.jsonl"
DEFAULT_REGISTRY = "registry.pjlab.org.cn/ccr-t-llm-frontier"


@dataclass(frozen=True)
class ImageSpec:
    instance_id: str
    official_source: str
    transfer_source: str
    verification_source: str | None
    target: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--registry", default=DEFAULT_REGISTRY)
    parser.add_argument(
        "--repository",
        default="swe-mm",
        help="All cases are stored as tags in this single repository",
    )
    parser.add_argument(
        "--source-registry",
        default="docker.1ms.run",
        help="Docker Hub-compatible registry used for layer transfer",
    )
    parser.add_argument(
        "--verification-registry",
        default="dockerproxy.net",
        help="Independent mirror whose manifest digest must match the source",
    )
    parser.add_argument("--instance-id", action="append", default=[])
    parser.add_argument(
        "--all", action="store_true", help="Mirror every image in the dataset"
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Mirror only instances not already recorded in --state",
    )
    parser.add_argument("--skopeo", type=Path, default=DEFAULT_SKOPEO)
    parser.add_argument("--authfile", type=Path)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--retry-times", type=int, default=5)
    parser.add_argument(
        "--num-shards",
        type=int,
        default=1,
        help="Split the selected instances into this many deterministic shards",
    )
    parser.add_argument(
        "--shard-index",
        type=int,
        default=0,
        help="Zero-based shard to process (used with --num-shards)",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    required = {"instance_id", "image"}
    for number, row in enumerate(rows, 1):
        missing = required - row.keys()
        if missing:
            raise ValueError(f"{path}:{number}: missing fields {sorted(missing)}")
    return rows


def tag_for(instance_id: str) -> str:
    tag = re.sub(r"[^a-z0-9_.-]+", "-", instance_id.lower()).strip("-.")
    if not tag or len(tag) > 128:
        raise ValueError(f"Cannot form an OCI tag from: {instance_id!r}")
    return tag


def source_ref(image: str, registry: str = "docker.io") -> str:
    image = image.removeprefix("docker://")
    first_component = image.split("/", 1)[0]
    if (
        "." not in first_component
        and ":" not in first_component
        and first_component != "localhost"
    ):
        return f"docker://{registry.rstrip('/')}/{image}"
    return f"docker://{image}"


def make_specs(
    rows: list[dict[str, Any]],
    registry: str,
    repository: str,
    source_registry: str,
    verification_registry: str | None,
) -> list[ImageSpec]:
    prefix = registry.removeprefix("docker://").rstrip("/")
    repo = repository.strip("/")
    return [
        ImageSpec(
            instance_id=row["instance_id"],
            official_source=source_ref(row["image"]),
            transfer_source=source_ref(row["image"], source_registry),
            verification_source=(
                source_ref(row["image"], verification_registry)
                if verification_registry
                else None
            ),
            target=f"docker://{prefix}/{repo}:{tag_for(row['instance_id'])}",
        )
        for row in rows
    ]


def skopeo_env() -> dict[str, str]:
    env = os.environ.copy()
    env["CONTAINERS_REGISTRIES_CONF"] = str(DEFAULT_REGISTRIES_CONF)
    bypass = {
        "registry.pjlab.org.cn",
        "xceph-inside.pjlab.org.cn",
        "localhost",
        "127.0.0.1",
    }
    for name in ("NO_PROXY", "no_proxy"):
        bypass.update(value for value in env.get(name, "").split(",") if value)
        env[name] = ",".join(sorted(bypass))
    return env


def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        env=skopeo_env(),
    )


def inspect_digest(
    skopeo: Path,
    ref: str,
    authfile: Path | None,
    *,
    attempts: int = 1,
) -> str | None:
    command = [str(skopeo), "inspect", "--format", "{{.Digest}}"]
    if authfile:
        command.extend(["--authfile", str(authfile)])
    command.append(ref)
    for attempt in range(1, attempts + 1):
        try:
            result = run(command, capture=True)
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or "").strip().splitlines()
            if detail:
                print(f"  inspect failed ({ref}): {detail[-1]}", file=sys.stderr)
            if attempt < attempts:
                delay = min(2 ** (attempt - 1), 15)
                print(
                    f"  inspect retry {attempt + 1}/{attempts} in {delay}s",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            return None
        return result.stdout.strip() or None
    return None


def upsert_state(path: Path, record: dict[str, Any]) -> None:
    """Keep exactly one current canonical image mapping per instance."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        records: dict[str, dict[str, Any]] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    previous = json.loads(line)
                    records[previous["instance_id"]] = previous
        records[record["instance_id"]] = record
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                for instance_id in sorted(records):
                    handle.write(
                        json.dumps(
                            records[instance_id], ensure_ascii=False, sort_keys=True
                        )
                        + "\n"
                    )
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def completed_instance_ids(path: Path) -> set[str]:
    """Return instances with a successfully verified private-image record."""
    if not path.exists():
        return set()
    completed: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("status") in {"mirrored", "already_present"}:
            completed.add(record["instance_id"])
    return completed


def main() -> int:
    args = parse_args()
    selection_modes = int(args.all) + int(args.resume) + int(bool(args.instance_id))
    if selection_modes != 1:
        raise SystemExit(
            "Select exactly one of --all, --resume, or at least one --instance-id"
        )
    if not args.skopeo.is_file():
        raise SystemExit(f"skopeo executable not found: {args.skopeo}")
    if args.num_shards < 1:
        raise SystemExit("--num-shards must be at least 1")
    if not 0 <= args.shard_index < args.num_shards:
        raise SystemExit("--shard-index must be in [0, --num-shards)")

    specs = make_specs(
        load_rows(args.dataset),
        args.registry,
        args.repository,
        args.source_registry,
        args.verification_registry or None,
    )
    by_id = {spec.instance_id: spec for spec in specs}
    if args.all:
        selected = specs
    elif args.resume:
        completed = completed_instance_ids(args.state)
        selected = [spec for spec in specs if spec.instance_id not in completed]
    else:
        unknown = sorted(set(args.instance_id) - by_id.keys())
        if unknown:
            raise SystemExit(f"Unknown instance IDs: {', '.join(unknown)}")
        selected = [by_id[instance_id] for instance_id in args.instance_id]
    selected = selected[args.shard_index :: args.num_shards]

    print(
        f"[plan] {len(selected)} image(s) -> "
        f"{args.registry}/{args.repository}:<instance>"
    )
    failures = 0
    for index, spec in enumerate(selected, 1):
        print(f"[{index}/{len(selected)}] {spec.instance_id}")
        print(f"  official: {spec.official_source}")
        print(f"  transfer: {spec.transfer_source}")
        if spec.verification_source:
            print(f"  verify: {spec.verification_source}")
        print(f"  target: {spec.target}")
        if args.dry_run:
            continue

        source_digest = inspect_digest(
            args.skopeo,
            spec.transfer_source,
            args.authfile,
            attempts=args.retry_times,
        )
        if not source_digest:
            print("  ERROR: cannot inspect source", file=sys.stderr)
            failures += 1
            continue
        if spec.verification_source:
            verification_digest = inspect_digest(
                args.skopeo,
                spec.verification_source,
                args.authfile,
                attempts=args.retry_times,
            )
            if verification_digest != source_digest:
                print(
                    "  ERROR: mirror digest mismatch: "
                    f"source={source_digest}, verification={verification_digest}",
                    file=sys.stderr,
                )
                failures += 1
                continue
        target_digest = inspect_digest(args.skopeo, spec.target, args.authfile)
        if target_digest == source_digest:
            print(f"  skip: already mirrored ({source_digest})")
            upsert_state(
                args.state,
                {
                    "instance_id": spec.instance_id,
                    "source": spec.official_source.removeprefix("docker://"),
                    "transfer_source": spec.transfer_source.removeprefix("docker://"),
                    "verification_source": (
                        spec.verification_source.removeprefix("docker://")
                        if spec.verification_source
                        else None
                    ),
                    "target": spec.target.removeprefix("docker://"),
                    "source_digest": source_digest,
                    "target_digest": target_digest,
                    "status": "already_present",
                },
            )
            continue

        command = [
            str(args.skopeo),
            "copy",
            "--retry-times",
            str(args.retry_times),
            "--preserve-digests",
        ]
        if args.authfile:
            command.extend(["--authfile", str(args.authfile)])
        command.extend([spec.transfer_source, spec.target])
        try:
            run(command)
            target_digest = inspect_digest(
                args.skopeo,
                spec.target,
                args.authfile,
                attempts=args.retry_times,
            )
            if target_digest != source_digest:
                raise RuntimeError(
                    f"digest mismatch: source={source_digest}, target={target_digest}"
                )
        except (subprocess.CalledProcessError, RuntimeError) as error:
            print(f"  ERROR: {error}", file=sys.stderr)
            failures += 1
            continue

        print(f"  done: {target_digest}")
        upsert_state(
            args.state,
            {
                "instance_id": spec.instance_id,
                "source": spec.official_source.removeprefix("docker://"),
                "transfer_source": spec.transfer_source.removeprefix("docker://"),
                "verification_source": (
                    spec.verification_source.removeprefix("docker://")
                    if spec.verification_source
                    else None
                ),
                "target": spec.target.removeprefix("docker://"),
                "source_digest": source_digest,
                "target_digest": target_digest,
                "status": "mirrored",
            },
        )

    if failures:
        print(f"[failed] {failures}/{len(selected)} image(s)", file=sys.stderr)
        return 1
    print(f"[complete] {len(selected)} image(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
