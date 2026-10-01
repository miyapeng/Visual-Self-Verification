#!/usr/bin/env python3
"""Evaluate one SWE-MM patch from inside its official instance image.

This runner intentionally does not use Docker.  ClusterX starts the instance
image as the outer job container, and this script operates on /testbed.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_SWEBENCH_ROOT = PROJECT_ROOT / "evaluate" / "swe_mm" / "upstream"
sys.dont_write_bytecode = True
sys.path.insert(0, str(OFFICIAL_SWEBENCH_ROOT))

import swebench
from swebench.harness.constants import TESTS_TIMEOUT
from swebench.harness.grading import get_eval_report
from swebench.harness.utils import make_test_spec


LOADED_SWEBENCH = Path(swebench.__file__).resolve()
if not LOADED_SWEBENCH.is_relative_to(OFFICIAL_SWEBENCH_ROOT.resolve()):
    raise RuntimeError(
        f"SWE-bench evaluator import drift: expected {OFFICIAL_SWEBENCH_ROOT}, "
        f"loaded {LOADED_SWEBENCH}"
    )


DEFAULT_DATASET = Path(
    "/data/miyapeng/mmcode/MultimodalCode/data/swe_mm/dev/"
    "evaluator_private/instances.full.jsonl"
)
DEFAULT_OUTPUT_ROOT = Path(
    "/data/miyapeng/mmcode/MultimodalCode/runs/swe_mm_direct_smoke"
)

# Keep this in the same order as the frozen upstream evaluator at
# evaluate/swe_mm/upstream/swebench/harness/run_evaluation.py.  The clean
# ClusterX transport starts directly in the instance image, so these commands
# operate on /testbed instead of through docker exec.
GIT_APPLY_COMMANDS = (
    ("git", "apply", "--verbose"),
    ("git", "apply", "--verbose", "--3way"),
    ("git", "apply", "--verbose", "--reject"),
    ("patch", "--batch", "--forward", "--fuzz=5", "-p1", "-i"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_instance(dataset: Path, instance_id: str) -> dict:
    with dataset.open() as handle:
        for line in handle:
            row = json.loads(line)
            if row["instance_id"] == instance_id:
                return row
    raise ValueError(f"Instance not found in {dataset}: {instance_id}")


def run_checked(command: list[str], *, cwd: Path, input_text: str | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n{result.stdout}"
        )
    return result.stdout


def restore_pristine_tree(testbed: Path, base_commit: str) -> None:
    """Restore the pristine instance state without deleting image dependencies."""
    run_checked(["git", "reset", "--hard", base_commit], cwd=testbed)
    run_checked(["git", "clean", "-fd"], cwd=testbed)


def apply_prediction_patch(
    testbed: Path, base_commit: str, patch_file: Path
) -> tuple[str, list[dict]]:
    """Apply a prediction using the frozen official evaluator's fallback order."""
    attempts: list[dict] = []
    for command_prefix in GIT_APPLY_COMMANDS:
        restore_pristine_tree(testbed, base_commit)
        command = [*command_prefix, str(patch_file)]
        result = subprocess.run(
            command,
            cwd=testbed,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        attempts.append({
            "command": command,
            "returncode": result.returncode,
            "output": result.stdout,
        })
        if result.returncode == 0:
            return " ".join(command_prefix), attempts

    # Match the official harness: a fallback may fully apply a patch while
    # returning non-zero.  A reverse dry-run distinguishes that state.
    reverse = subprocess.run(
        ["git", "apply", "--check", "--reverse", str(patch_file)],
        cwd=testbed,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    attempts.append({
        "command": ["git", "apply", "--check", "--reverse", str(patch_file)],
        "returncode": reverse.returncode,
        "output": reverse.stdout,
    })
    if reverse.returncode == 0:
        return "verified-already-applied", attempts

    detail = "\n\n".join(
        f"$ {' '.join(row['command'])}\nexit={row['returncode']}\n{row['output']}"
        for row in attempts
    )
    raise RuntimeError(f"Official patch application chain failed:\n{detail}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--testbed", type=Path, default=Path("/testbed"))
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Official SWE-bench per-instance test timeout (seconds)",
    )
    parser.add_argument(
        "--patch",
        type=Path,
        help="Patch to evaluate. Omit to use the frozen gold patch for smoke testing.",
    )
    args = parser.parse_args()

    row = load_instance(args.dataset, args.instance_id)
    patch = args.patch.read_text() if args.patch else row["patch"]
    patch_source = str(args.patch) if args.patch else "gold"
    output_dir = args.output_root / args.instance_id
    output_dir.mkdir(parents=True, exist_ok=True)
    test_log = output_dir / "test_output.txt"
    report_path = output_dir / "report.json"
    metadata_path = output_dir / "metadata.json"
    eval_script_path = output_dir / "eval.sh"
    staged_patch_path = output_dir / "prediction.patch.diff"
    staged_patch_path.write_text(patch, encoding="utf-8")

    metadata = {
        "instance_id": args.instance_id,
        "expected_image": row["image"],
        "base_commit": row["base_commit"],
        "patch_source": patch_source,
        "evaluator_source": str(LOADED_SWEBENCH),
        "evaluator_commit": "7e578260da58400f307e435e43d1d2ab29d686f6",
        "hostname": platform.node(),
        "started_at_utc": utc_now(),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")

    if not args.testbed.is_dir():
        raise RuntimeError(f"Official image repository is missing: {args.testbed}")

    print(f"[direct-eval] instance={args.instance_id}", flush=True)
    print(f"[direct-eval] expected_image={row['image']}", flush=True)
    print(f"[direct-eval] patch_source={patch_source}", flush=True)

    actual_commit = run_checked(
        ["git", "rev-parse", "HEAD"], cwd=args.testbed
    ).strip()
    print(f"[direct-eval] image_commit={actual_commit}", flush=True)
    if actual_commit != row["base_commit"]:
        raise RuntimeError(
            f"Wrong instance image/base commit: expected {row['base_commit']}, got {actual_commit}"
        )

    apply_method, apply_attempts = apply_prediction_patch(
        args.testbed, row["base_commit"], staged_patch_path
    )
    metadata.update({
        "patch_applied": True,
        "patch_apply_method": apply_method,
        "patch_apply_attempts": apply_attempts,
    })
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")

    eval_script_path.write_text(row["eval_script"])
    eval_script_path.chmod(0o755)

    timed_out = False
    with test_log.open("w") as log_handle:
        # Run the evaluator in its own process group.  Killing only the outer
        # shell on timeout can leave npm/jasmine descendants alive with the log
        # descriptor open, causing subprocess.run() to wait forever while
        # collecting output.  ClusterX replaces the official Docker container
        # boundary, so terminating this per-case process group is the direct
        # equivalent of the upstream harness stopping the timed-out container.
        process = subprocess.Popen(
            ["bash", str(eval_script_path)],
            cwd=args.testbed,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
            start_new_session=True,
        )
        try:
            test_exit_code = process.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            test_exit_code = 124
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            log_handle.write(f"\n{TESTS_TIMEOUT}\n")
            log_handle.flush()

    prediction = {
        "instance_id": args.instance_id,
        "model_name_or_path": "gold-smoke" if args.patch is None else "direct-patch",
        "model_patch": patch,
    }
    report = get_eval_report(
        make_test_spec(row), prediction, str(test_log), include_tests_status=True
    )
    instance_report = report[args.instance_id]
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    metadata.update(
        {
            "finished_at_utc": utc_now(),
            "test_exit_code": test_exit_code,
            "timed_out": timed_out,
            "resolved": bool(instance_report.get("resolved")),
        }
    )
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")

    print(f"[direct-eval] test_exit_code={test_exit_code}", flush=True)
    print(f"[direct-eval] resolved={instance_report.get('resolved', False)}", flush=True)
    print(f"[direct-eval] report={report_path}", flush=True)
    return 0 if instance_report.get("resolved") else 1


if __name__ == "__main__":
    raise SystemExit(main())
