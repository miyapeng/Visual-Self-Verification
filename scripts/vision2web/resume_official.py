#!/usr/bin/env python3
"""Freeze a missing-case selection, then reuse the official full-run controller.

Historical outputs are read-only. A timeout is retained only when explicitly
listed in preserve_statuses; it is never relabeled as a successful result.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import shlex
import subprocess
import sys
from collections import Counter
from pathlib import Path

import submit_self_verify as controller


ROOT = controller.PROJECT_ROOT


def selection(root: Path, model: str, config: dict, cases: list[str]) -> list[dict]:
    sources = [root / label for label in config["sources"]]
    for source in sources:
        if not source.is_dir():
            raise FileNotFoundError(source)
    rows = []
    for case_id in cases:
        attempts = []
        for source in sources:
            folder = source / "agents" / model / "vision2web/claude_code/official" / controller.safe(case_id)
            result_path = folder / "result.json"
            if result_path.is_file():
                result = json.loads(result_path.read_text())
                if (result.get("case_id"), result.get("model")) != (case_id, model):
                    raise ValueError(f"Mismatched result: {result_path}")
                if result.get("vision2web_mode") != "official":
                    raise ValueError(f"Not an official-mode result: {result_path}")
                attempts.append({"path": str(result_path), "status": result["status"]})
            elif (folder / "generation-summary.json").is_file():
                attempts.append({"path": str(folder / "generation-summary.json"), "status": "missing_result"})
        preserve = any(a["status"] in config["preserve_statuses"] for a in attempts)
        rows.append({"case_id": case_id, "selected": not preserve, "prior_attempts": attempts})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())[args.model]
    label = config["run_label"]
    if label in config["sources"] or Path(label).name != label:
        raise ValueError("A new, single-directory run label is required")
    output_root = ROOT / "runs/vision2web_generation"
    run_root = output_root / label
    run_root.mkdir(parents=True, exist_ok=True)
    # Keep this descriptor open while the existing controller is running.
    with (run_root / "resume.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error(f"Another continuation controller holds {run_root / 'resume.lock'}")
        rows = selection(output_root, args.model, config, controller.selected_cases({}, "full"))
        selected = [r["case_id"] for r in rows if r["selected"]]
        command = [
            sys.executable, "-u", str(ROOT / "scripts/vision2web/submit_self_verify.py"),
            "--framework", "claude_code", "--phase", "full", "--modes", "official",
            "--model", args.model, "--run-label", label,
            "--server-run", config["server_run"], "--max-active", str(config["max_active"]),
            "--poll-seconds", "60", "--case-ids", *selected,
        ]
        manifest_path = run_root / "resume-selection.json"
        payload = {"model": args.model, "config": config, "cases": rows, "command": command}
        if manifest_path.exists():
            saved = json.loads(manifest_path.read_text())
            if any(saved.get(k) != payload[k] for k in ("model", "config", "cases")):
                raise ValueError("Frozen resume selection changed; use a new run label")
        else:
            payload["created_at_utc"] = controller.utc_now()
            controller.write_json(manifest_path, payload)
        print(json.dumps({
            "model": args.model, "selected": len(selected), "preserved": len(rows) - len(selected),
            "selected_prior_statuses": dict(Counter(
                a["status"] for r in rows if r["selected"] for a in r["prior_attempts"]
            )), "max_active": config["max_active"], "manifest": str(manifest_path),
        }), flush=True)
        if args.prepare_only or not selected:
            return 0
        print("[controller] " + shlex.join(command), flush=True)
        return subprocess.run(command, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
