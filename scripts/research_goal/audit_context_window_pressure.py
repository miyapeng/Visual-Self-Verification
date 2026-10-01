#!/usr/bin/env python3
"""Audit context pressure in the three active benchmark settings.

The audit is retrospective and read-only.  It distinguishes a server-rejected
request from harness-managed condensation and from a saved text prefix that
would not fit a smaller released deployment.  Image-token usage is not
recoverable from all saved formats and is never guessed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


HARD_CONTEXT_RE = re.compile(
    r"maximum context length|context window exceeded|prompt.{0,40}too long|too many tokens",
    re.IGNORECASE | re.DOTALL,
)
INPUT_LENGTH_RE = re.compile(r"Input length \((\d+)\)")
AT_LEAST_INPUT_RE = re.compile(r"at least (\d+) input tokens")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_digest(paths: Iterable[Path], root: Path) -> str:
    rows = [
        [str(path.relative_to(root)), _sha256(path)]
        for path in sorted(paths)
    ]
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _token_count(tokenizer: Any, messages: Sequence[Mapping[str, Any]], *, add_generation_prompt: bool) -> int:
    encoded = tokenizer.apply_chat_template(
        list(messages),
        tokenize=True,
        add_generation_prompt=add_generation_prompt,
    )
    if isinstance(encoded, Mapping):
        encoded = encoded["input_ids"]
    return len(encoded)


def _last_assistant_request_prefix(messages: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Return the largest append-only prefix that actually prompted an assistant."""
    assistant_indices = [
        index for index, message in enumerate(messages)
        if message.get("role") == "assistant"
    ]
    if not assistant_indices:
        raise ValueError("History contains no assistant response")
    return list(messages[: assistant_indices[-1]])


def _hard_context_cases(results: Iterable[Path], root: Path) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    result_paths = sorted(results)
    for path in result_paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        if not HARD_CONTEXT_RE.search(text):
            continue
        input_match = INPUT_LENGTH_RE.search(text) or AT_LEAST_INPUT_RE.search(text)
        cases.append(
            {
                "case_id": path.parent.name,
                "input_tokens_reported": int(input_match.group(1)) if input_match else None,
                "source": str(path.relative_to(root)),
                "source_sha256": _sha256(path),
            }
        )
    return {
        "observed_results": len(result_paths),
        "hard_context_case_count": len(cases),
        "hard_context_cases": cases,
        "result_tree_sha256": _tree_digest(result_paths, root),
    }


def _audit_swe(project: Path) -> dict[str, Any]:
    base = project / "runs" / "swe_mm_official"
    configurations = {}
    for model, label, complete in (
        ("Qwen3.5-9B", "qwen35-9b-mini-official-dev-s2", True),
        ("Qwen3-VL-30B-A3B", "qwen3vl30-mini-official-dev-s2", False),
    ):
        root = base / label / "agents"
        audit = _hard_context_cases(root.rglob("result.json"), root)
        audit.update(
            {
                "run_label": label,
                "complete_dev_run": complete,
                "configured_max_model_len": 262_144,
            }
        )
        configurations[model] = audit
    return {
        "benchmark": "SWE-bench Multimodal dev",
        "dev_denominator": 102,
        "configurations": configurations,
        "interpretation": "hard server rejection; the incomplete Qwen3-VL run is not a final baseline",
    }


def _audit_vision(project: Path) -> dict[str, Any]:
    root = (
        project
        / "runs"
        / "vision2web_generation"
        / "qwen35-9b-openhands-official-v2"
        / "agents"
    )
    trajectories = sorted(root.rglob("trajectory.json"))
    condensation_counts: list[tuple[Path, int]] = []
    hard_cases: list[str] = []
    for path in trajectories:
        text = path.read_text(encoding="utf-8", errors="replace")
        count = text.count('"kind": "Condensation"')
        if count:
            condensation_counts.append((path, count))
        if HARD_CONTEXT_RE.search(text):
            hard_cases.append(path.parent.name)
    return {
        "benchmark": "Vision2Web",
        "scope": "full Vision2Web Level 1/2/3",
        "task_denominator": 193,
        "saved_trajectories": len(trajectories),
        "configured_max_model_len": 262_144,
        "hard_context_case_count": len(set(hard_cases)),
        "hard_context_cases": sorted(set(hard_cases)),
        "condensation_case_count": len(condensation_counts),
        "condensation_event_count": sum(count for _, count in condensation_counts),
        "trajectory_tree_sha256": _tree_digest(trajectories, root),
        "interpretation": (
            "OpenHands condensation is context management, not proof that vLLM rejected a request; "
            "zero hard errors therefore does not mean zero context pressure"
        ),
    }


def _load_frontalk_messages(path: Path) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case_id, message = json.loads(line)
        grouped[str(case_id)].append(message)
    return grouped


def _audit_frontalk(project: Path, tokenizer: Any) -> dict[str, Any]:
    root = (
        project
        / "runs"
        / "native_benchmarks"
        / "qwen35-9b-local-support-frontalk-text-full-20260818"
    )
    messages_path = root / "frontalk" / "messages.jsonl"
    grouped = _load_frontalk_messages(messages_path)
    prompt_rows = []
    completed_rows = []
    omitted = 0
    for case_id, messages in grouped.items():
        omitted += sum(message.get("content") == "(omitted)" for message in messages)
        prefix = _last_assistant_request_prefix(messages)
        prompt_rows.append(
            [_token_count(tokenizer, prefix, add_generation_prompt=True), case_id]
        )
        completed_rows.append(
            [_token_count(tokenizer, messages, add_generation_prompt=True), case_id]
        )
    max_prompt = max(prompt_rows)
    max_completed = max(completed_rows)
    log_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (root / "job.log", root / "vllm.log")
        if path.is_file()
    )
    return {
        "benchmark": "FronTalk",
        "dialogues": len(grouped),
        "assistant_requests": sum(
            sum(message.get("role") == "assistant" for message in messages)
            for messages in grouped.values()
        ),
        "configured_max_model_len": 262_144,
        "requested_max_output_tokens": 8_192,
        "max_actual_prompt_tokens": max_prompt[0],
        "max_actual_prompt_case": max_prompt[1],
        "max_prompt_plus_requested_output_tokens": max_prompt[0] + 8_192,
        "max_completed_history_with_next_assistant_marker_tokens": max_completed[0],
        "max_completed_history_case": max_completed[1],
        "omitted_history_messages": omitted,
        "hard_context_error_in_logs": bool(HARD_CONTEXT_RE.search(log_text)),
        "messages_sha256": _sha256(messages_path),
        "interpretation": "append-only ten-turn text history remains below both 128K and 262K",
    }


def _audit_interact(project: Path, tokenizer: Any) -> dict[str, Any]:
    root = (
        project
        / "runs"
        / "native_benchmarks"
        / "qwen35-9b-interactweb-full-20260820"
    )
    terminal_records = sorted(root.rglob("pending_evaluation.json"))
    rows = []
    histories = []
    for pending in terminal_records:
        history = pending.with_name("history.json")
        if not history.is_file():
            raise FileNotFoundError(f"Missing terminal history: {history}")
        histories.append(history)
        payload = json.loads(history.read_text(encoding="utf-8"))
        prefix = _last_assistant_request_prefix(payload["messages"])
        rows.append(
            {
                "case_id": history.parent.name,
                "max_saved_builder_text_prefix_tokens": _token_count(
                    tokenizer, prefix, add_generation_prompt=True
                ),
            }
        )
    largest = max(rows, key=lambda row: row["max_saved_builder_text_prefix_tokens"])
    observed_len = 262_144
    released_len = 128_000
    released_output = 16_000
    run_logs = sorted(root.rglob("job.log")) + sorted(root.rglob("vllm.log"))
    hard_log_cases = []
    for path in run_logs:
        if HARD_CONTEXT_RE.search(path.read_text(encoding="utf-8", errors="replace")):
            hard_log_cases.append(str(path.relative_to(root)))
    return {
        "benchmark": "InteractWeb-Bench",
        "terminal_histories": len(rows),
        "observed_max_model_len": observed_len,
        "released_max_model_len": released_len,
        "released_requested_max_output_tokens": released_output,
        "hard_context_error_in_observed_262k_logs": bool(hard_log_cases),
        "hard_context_log_paths": hard_log_cases,
        "max_saved_builder_text_prefix_tokens": largest[
            "max_saved_builder_text_prefix_tokens"
        ],
        "max_saved_builder_text_prefix_case": largest["case_id"],
        "cases_over_released_128k_text_prefix": sum(
            row["max_saved_builder_text_prefix_tokens"] > released_len for row in rows
        ),
        "cases_over_131072_text_prefix": sum(
            row["max_saved_builder_text_prefix_tokens"] > 131_072 for row in rows
        ),
        "cases_without_full_released_16k_output_headroom": sum(
            row["max_saved_builder_text_prefix_tokens"] > released_len - released_output
            for row in rows
        ),
        "history_tree_sha256": _tree_digest(histories, root),
        "interpretation": (
            "the observed 262K diagnostic did not hard-fail, but saved text prefixes are not "
            "equivalent to the released 128K deployment; image-token usage and counterfactual "
            "trajectory changes under released model roles are not recoverable"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        default=str(Path(__file__).resolve().parents[2]),
    )
    parser.add_argument("--tokenizer", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    project = Path(args.project_root).resolve()
    output = Path(args.output_dir).resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"Refusing non-empty output directory: {output}")
    output.mkdir(parents=True, exist_ok=True)

    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, trust_remote_code=True)
    result = {
        "schema": "multimodalcode-context-window-pressure-audit-1",
        "scope": "saved local generation trajectories; official evaluators excluded",
        "tokenizer": str(Path(args.tokenizer).resolve()),
        "tokenizer_class": type(tokenizer).__name__,
        "image_token_accounting": "unavailable; never estimated",
        "benchmarks": {
            "swe_mm": _audit_swe(project),
            "vision2web": _audit_vision(project),
            "frontalk": _audit_frontalk(project, tokenizer),
        },
        "archived_exclusions": [
            "InteractWeb-Bench is outside the current paper scope.",
        ],
    }
    target = output / "summary.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
