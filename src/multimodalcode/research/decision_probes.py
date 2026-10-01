from __future__ import annotations

import base64
import hashlib
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .context import (
    ConservedFrontierPolicy,
    ContextBudget,
    ContextPolicy,
    ContextRecord,
    FullHistoryPolicy,
    GuardedFrontierPolicy,
    RecentPolicy,
    SummaryPolicy,
)


FORBIDDEN_PROMPT_FIELDS = (
    "detector_label",
    "manual_label",
    "external_outcome",
    "ground_truth_instruction",
    "oracle_slots",
    "evaluation_checklist",
)
DECISIONS = {"submit", "revise", "retry_verification", "escalate"}


@dataclass(frozen=True)
class ProbeCase:
    benchmark: str
    task: str
    stratum: str
    source_trajectory: str
    public_request: str
    public_image_paths: list[str]
    current_state: str
    records: list[ContextRecord]
    manual_label: dict[str, Any]
    detector_label: str
    external_outcome: Any


class StatusLedgerNoRulePolicy(ConservedFrontierPolicy):
    """Information-matched ablation without the completion decision rule."""

    name = "status_ledger_no_rule"

    def prepare(
        self,
        records: Sequence[ContextRecord],
        current_version: str,
        budget: ContextBudget,
    ) -> tuple[list[ContextRecord], dict[str, Any]]:
        ordered, metadata = self._build(records, current_version)
        if not ordered:
            return [], {**metadata, "completion_rule": False}
        ledger = ordered[0]
        states = dict(ledger.metadata.get("obligation_states") or {})
        lines = [
            "[Current requirement-state ledger]",
            "State vocabulary: SUPPORTED=current pass; REFUTED=current failure; "
            "BLOCKED=inconclusive current check; RECHECK=no current-state result.",
        ]
        lines.extend(f"- {key}: {states[key]}" for key in sorted(states))
        neutral = replace(
            ledger,
            record_id=ledger.record_id.replace("conserved-ledger:", "status-ledger:"),
            text="\n".join(lines),
            metadata={**ledger.metadata, "completion_rule": False},
        )
        if neutral.estimated_text_tokens > budget.max_text_tokens:
            raise ValueError(
                "Context budget cannot encode the status ledger: "
                f"need {neutral.estimated_text_tokens} text tokens, "
                f"have {budget.max_text_tokens}"
            )
        return [neutral, *ordered[1:]], {**metadata, "completion_rule": False}


class SummaryWithCompletionRulePolicy(SummaryPolicy):
    """Rule-only factorial control: generic summary plus the same stop rule."""

    name = "summary_with_completion_rule"

    def order(
        self, records: Sequence[ContextRecord], current_version: str
    ) -> list[ContextRecord]:
        summaries = super().order(records, current_version)
        if not summaries:
            return []
        summary = summaries[0]
        rule = (
            "[Completion rule]\n"
            "Submit only when every public requirement is supported by "
            "requirement-matched evidence from the current program state. "
            "Concrete current counterevidence calls for revision; stale or missing "
            "evidence calls for re-verification; environment-blocked evidence calls "
            "for retry or escalation rather than an unsupported code change.\n"
        )
        return [
            replace(
                summary,
                record_id=summary.record_id.replace("summary:", "rule-summary:"),
                text=rule + summary.text,
                metadata={**summary.metadata, "completion_rule": True},
            )
        ]


POLICY_FACTORIES: Mapping[str, Any] = {
    "full": FullHistoryPolicy,
    "recent": lambda: RecentPolicy(k=8),
    "summary": SummaryPolicy,
    "summary_with_completion_rule": SummaryWithCompletionRulePolicy,
    "guarded_frontier": GuardedFrontierPolicy,
    "status_ledger_no_rule": StatusLedgerNoRulePolicy,
    "conserved_frontier": ConservedFrontierPolicy,
}


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def event_categories(event: Mapping[str, Any]) -> set[str]:
    return {
        str(category)
        for action in event.get("executed_action") or []
        for category in action.get("categories") or []
    }


def events_before_terminal_submit(
    events: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    submit_sequences = [
        int(event["sequence"])
        for event in events
        if "submit" in event_categories(event)
    ]
    boundary = min(submit_sequences, default=10**18)
    return [event for event in events if int(event["sequence"]) < boundary]


def joint_state(event: Mapping[str, Any]) -> str:
    program = str((event.get("program_version") or {}).get("id", "v?"))
    verification = str((event.get("verification_version") or {}).get("id", "t?"))
    return f"{program}@{verification}"


def _safe_visible_text(value: Any, *, limit: int = 12_000) -> str:
    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            if item.get("type") == "image_url":
                url = str((item.get("image_url") or {}).get("url", ""))
                digest = hashlib.sha256(url.encode()).hexdigest()[:16]
                return f"[image evidence {digest}]"
            return {str(key): clean(val) for key, val in item.items()}
        if isinstance(item, list):
            return [clean(part) for part in item]
        return item

    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(clean(value), ensure_ascii=False, sort_keys=True)
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    prefix = encoded[:limit]
    while prefix:
        try:
            return prefix.decode("utf-8") + f"\n[record truncated: {len(encoded)-limit} bytes]"
        except UnicodeDecodeError:
            prefix = prefix[:-1]
    return "[record omitted: invalid UTF-8]"


def _raw_row(source_ref: str, cache: dict[Path, dict[str, Any]]) -> dict[str, Any] | None:
    if "#" not in source_ref:
        return None
    raw_path, fragment = source_ref.rsplit("#", 1)
    path = Path(raw_path)
    if not path.is_file():
        return None
    data = cache.setdefault(path, json.loads(path.read_text(encoding="utf-8")))
    if fragment.startswith("trajectory/"):
        index = int(fragment.rsplit("/", 1)[-1])
        trajectory = data.get("trajectory") or []
        return trajectory[index] if 0 <= index < len(trajectory) else None
    if fragment.startswith("events/"):
        sequence = int(fragment.rsplit("/", 1)[-1])
        return next(
            (
                row
                for row in data.get("events") or []
                if int(row.get("sequence", -1)) == sequence
            ),
            None,
        )
    return None


def _row_text(row: Mapping[str, Any] | None, event: Mapping[str, Any]) -> str:
    if row:
        if "content" in row:
            role = str(row.get("role") or "message")
            return f"[{role}] {_safe_visible_text(row.get('content'))}"
        actor = str(row.get("actor") or row.get("kind") or "event")
        parts = [f"[{actor}] {_safe_visible_text(row.get('text', ''))}"]
        tools = row.get("tools") or []
        if tools:
            parts.append("[tools] " + _safe_visible_text(tools))
        if row.get("exit_status") is not None:
            parts.append(f"[exit_status] {row.get('exit_status')}")
        if row.get("error_code") is not None:
            parts.append(f"[error_code] {row.get('error_code')}")
        return "\n".join(parts)
    observation = event.get("observation") or {}
    edit = event.get("edit") or {}
    actions = event.get("executed_action") or []
    visible = observation.get("content", {}).get("snippet")
    if not visible:
        visible = edit.get("content", {}).get("snippet")
    if not visible and actions:
        visible = [action.get("tool") for action in actions]
    return "[normalized event] " + _safe_visible_text(visible or "no visible payload")


def _screenshot_paths(row: Mapping[str, Any] | None) -> list[Path]:
    if not row:
        return []
    debug = row.get("debug_info") or {}
    paths: list[Path] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "screenshot_path" and child:
                    candidate = Path(str(child))
                    if candidate.is_file():
                        paths.append(candidate.resolve())
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(debug)
    return paths


def _image_pixels(path: Path) -> int:
    try:
        header = path.read_bytes()[:24]
        if header[:8] == b"\x89PNG\r\n\x1a\n" and len(header) >= 24:
            return int.from_bytes(header[16:20], "big") * int.from_bytes(
                header[20:24], "big"
            )
    except OSError:
        return 0
    return 0


def build_context_records(events: Sequence[dict[str, Any]]) -> list[ContextRecord]:
    """Map policy-visible events to records without importing evaluator labels.

    This retrospective decision probe has no public per-requirement checklist,
    so it deliberately uses one coarse ``task-operational`` obligation. The
    end-to-end experiment must instead use requirement-decomposed checks.
    """

    visible_events = events_before_terminal_submit(events)
    if not visible_events:
        return []
    cache: dict[Path, dict[str, Any]] = {}
    raw_rows = [_raw_row(str(event.get("source_ref", "")), cache) for event in visible_events]
    screenshot_candidates: list[tuple[int, Path]] = []
    for index, row in enumerate(raw_rows):
        paths = _screenshot_paths(row)
        if paths:
            screenshot_candidates.append((index, paths[-1]))
    # Released InteractWeb reuses visual_step_N filenames. Only the last visual
    # record is guaranteed to still point at its own pixels after the run.
    final_image_index = screenshot_candidates[-1][0] if screenshot_candidates else None
    final_image_path = screenshot_candidates[-1][1] if screenshot_candidates else None

    initial_state = joint_state(visible_events[0])
    records: list[ContextRecord] = [
        ContextRecord(
            record_id="obligation:task-operational:initial",
            kind="verification_residual",
            text=(
                "[public obligation] The current implementation has not yet been "
                "supported by a requirement-matched executable check."
            ),
            code_version=initial_state,
            sequence=-1,
            checklist_id="task-operational",
            status="blocked",
            metadata={"probe_granularity": "coarse_task_level"},
        )
    ]
    previous_categories: set[str] = set()
    seen_failure = False
    for index, (event, row) in enumerate(zip(visible_events, raw_rows)):
        sequence = int(event["sequence"])
        observation = event.get("observation") or {}
        status = str(observation.get("status") or "")
        categories = event_categories(event)
        follows_check = "executable_check" in previous_categories
        kind = "trajectory_event"
        obligation_status: str | None = None
        if status == "check_pass":
            kind = "verified_obligation"
            obligation_status = "pass"
        elif status == "concrete_failure":
            kind = "verification_residual"
            obligation_status = "fail"
        elif status in {"inconclusive", "environment_noise"} or (
            follows_check and status == "neutral"
        ):
            kind = "verification_residual"
            obligation_status = "blocked"

        record_image = final_image_path if index == final_image_index else None
        text = (
            f"[trajectory event {sequence}; state {joint_state(event)}]\n"
            + _row_text(row, event)
        )
        is_first_failure = obligation_status == "fail" and not seen_failure
        if obligation_status == "fail":
            seen_failure = True
        records.append(
            ContextRecord(
                record_id=f"event:{sequence}",
                kind=kind,
                text=text,
                code_version=joint_state(event),
                sequence=sequence,
                checklist_id=("task-operational" if obligation_status else None),
                status=obligation_status,
                image_path=str(record_image) if record_image else None,
                image_pixels=_image_pixels(record_image) if record_image else 0,
                is_first_failure=is_first_failure,
                metadata={
                    "source_ref": event.get("source_ref"),
                    "observation_status": status or None,
                    "follows_executable_check": follows_check,
                    "first_failing_action": sequence if obligation_status == "fail" else None,
                    "probe_granularity": "coarse_task_level",
                },
            )
        )
        previous_categories = categories
    return records


def _message_text_and_images(
    content: Any,
    *,
    image_dir: Path,
) -> tuple[str, list[str]]:
    if isinstance(content, str):
        return content, []
    text_parts: list[str] = []
    images: list[str] = []
    for part in content or []:
        if not isinstance(part, dict):
            text_parts.append(_safe_visible_text(part))
            continue
        if part.get("type") == "text":
            text_parts.append(str(part.get("text") or ""))
        elif part.get("type") == "image_url":
            url = str((part.get("image_url") or {}).get("url") or "")
            match = re.fullmatch(r"data:([^;,]+);base64,(.*)", url, flags=re.DOTALL)
            if match:
                payload = base64.b64decode(match.group(2), validate=True)
                digest = hashlib.sha256(payload).hexdigest()
                suffix = ".png" if "png" in match.group(1).lower() else ".jpg"
                image_dir.mkdir(parents=True, exist_ok=True)
                target = image_dir / f"{digest}{suffix}"
                if not target.exists():
                    target.write_bytes(payload)
                images.append(str(target.resolve()))
                text_parts.append(f"[public task image {digest[:16]}]")
    return "\n".join(text_parts), images


def extract_public_input(
    source_trajectory: str | Path,
    *,
    benchmark: str,
    image_dir: str | Path,
) -> tuple[str, list[str]]:
    source = Path(source_trajectory)
    data = json.loads(source.read_text(encoding="utf-8"))
    image_root = Path(image_dir)
    if benchmark == "interactweb":
        row = next(
            item for item in data.get("trajectory") or [] if item.get("role") == "user"
        )
        return _message_text_and_images(row.get("content"), image_dir=image_root)
    if benchmark == "swe_mm":
        raw_path = Path(str(data.get("raw_trajectory") or ""))
        if raw_path.is_file():
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            row = next(item for item in raw.get("messages") or [] if item.get("role") == "user")
            text, images = _message_text_and_images(
                row.get("content"), image_dir=image_root
            )
        else:
            row = next(item for item in data.get("events") or [] if item.get("actor") == "user")
            text, images = str(row.get("text") or ""), []
        match = re.search(r"<pr_description>\s*(.*?)\s*</pr_description>", text, re.DOTALL)
        if match:
            text = match.group(1)
            text = re.sub(
                r"^Consider the following PR description:\s*", "", text, count=1
            )
            if images:
                markers = [
                    f"[public task image {hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]}]"
                    for path in images
                ]
                text = text.rstrip() + "\n\n" + "\n".join(markers)
        return text.strip(), images
    raise ValueError(f"Unsupported decision-probe benchmark: {benchmark}")


def build_probe_cases(
    *,
    audit_events_path: str | Path,
    manual_annotations_path: str | Path,
    review_packets_path: str | Path,
    image_dir: str | Path,
) -> list[ProbeCase]:
    annotations = {
        (row["benchmark"], row["task"]): row
        for row in read_jsonl(manual_annotations_path)
    }
    packets = {
        (row["case"]["benchmark"], row["case"]["task"]): row
        for row in read_jsonl(review_packets_path)
    }
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in read_jsonl(audit_events_path):
        key = (event["benchmark"], event["task"])
        if key in annotations:
            grouped[key].append(event)
    cases: list[ProbeCase] = []
    for key in sorted(annotations):
        annotation = annotations[key]
        packet = packets[key]
        events = sorted(grouped[key], key=lambda event: int(event["sequence"]))
        visible = events_before_terminal_submit(events)
        if not visible:
            raise ValueError(f"No pre-submit events for {key}")
        source = str(packet["case"]["source_trajectory"])
        public_request, public_images = extract_public_input(
            source,
            benchmark=key[0],
            image_dir=Path(image_dir) / key[0] / key[1],
        )
        cases.append(
            ProbeCase(
                benchmark=key[0],
                task=key[1],
                stratum=str(annotation["stratum"]),
                source_trajectory=source,
                public_request=public_request,
                public_image_paths=public_images,
                current_state=joint_state(visible[-1]),
                records=build_context_records(events),
                manual_label=dict(annotation.get("manual_label") or {}),
                detector_label=str(annotation.get("detector_label") or ""),
                external_outcome=annotation.get("external_outcome"),
            )
        )
    return cases


def make_policy(name: str) -> ContextPolicy:
    try:
        return POLICY_FACTORIES[name]()
    except KeyError as exc:
        raise ValueError(f"Unknown decision-probe policy: {name}") from exc


def render_decision_prompt(
    case: ProbeCase,
    *,
    policy_name: str,
    budget: ContextBudget,
) -> tuple[str, list[str], dict[str, Any]]:
    selection = make_policy(policy_name).select(
        case.records,
        current_version=case.current_state,
        budget=budget,
    )
    prompt = f"""You are at the final decision point of a coding task.

Public task:
{case.public_request}

Current program-and-verification state: {case.current_state}

Policy-visible trajectory context:
{selection.render_text() or '[no retained trajectory records]'}

Choose the next action using only the public task and visible context:
- submit: the current implementation has enough current evidence to be delivered.
- revise: concrete program counterevidence identifies a code repair to make.
- retry_verification: evidence is stale, missing, inconclusive, or environment-blocked.
- escalate: progress cannot continue safely without unavailable information or capability.

Return exactly one JSON object and no markdown:
{{"decision":"submit|revise|retry_verification|escalate","evidence_ids":["visible record id"],"reason":"brief evidence-grounded reason"}}
"""
    lowered = prompt.lower()
    leaked = [field for field in FORBIDDEN_PROMPT_FIELDS if field.lower() in lowered]
    if leaked:
        raise RuntimeError(f"Evaluator-only field leaked into decision prompt: {leaked}")
    images = [*case.public_image_paths, *selection.image_paths]
    metadata = {
        "schema": "multimodalcode-decision-probe-request-1",
        "benchmark": case.benchmark,
        "task": case.task,
        "policy": policy_name,
        "current_state": case.current_state,
        "selected_record_ids": [record.record_id for record in selection.records],
        "omitted_record_ids": selection.omitted_ids,
        "selected_estimated_text_tokens": selection.estimated_text_tokens,
        "selected_image_count": selection.image_count,
        "public_image_count": len(case.public_image_paths),
        "total_image_count": len(images),
        "selection": selection.to_dict(),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "image_sha256": [hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in images],
        "budget": asdict(budget),
    }
    return prompt, images, metadata


def parse_decision_response(response: str) -> dict[str, Any]:
    text = response.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    parse_recovery: str | None = None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as original_error:
        start = text.find("{")
        decoder = json.JSONDecoder()
        if start < 0:
            raise ValueError("Decision response has no JSON object")
        try:
            payload, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            # A deterministic decode may exhaust the output cap inside the
            # free-text reason after it has already emitted both scored fields.
            # Recover only those syntactically complete leading fields; never
            # infer or repair a missing decision/evidence list.
            prefix = text[start:]
            decision_match = re.match(
                r'\{\s*"decision"\s*:\s*"([^"]+)"\s*,', prefix
            )
            evidence_match = re.search(
                r'"evidence_ids"\s*:\s*(\[[^\]]*\])\s*,', prefix
            )
            if not decision_match or not evidence_match:
                raise original_error
            try:
                evidence_ids = json.loads(evidence_match.group(1))
            except json.JSONDecodeError:
                raise original_error
            payload = {
                "decision": decision_match.group(1),
                "evidence_ids": evidence_ids,
                "reason": "[truncated after scored fields; see raw response]",
            }
            parse_recovery = "truncated_after_complete_decision_and_evidence_ids"
    if not isinstance(payload, dict):
        raise ValueError("Decision response must be a JSON object")
    decision = str(payload.get("decision") or "")
    if decision not in DECISIONS:
        raise ValueError(f"Invalid decision: {decision!r}")
    evidence_ids = payload.get("evidence_ids") or []
    if not isinstance(evidence_ids, list) or not all(
        isinstance(value, str) for value in evidence_ids
    ):
        raise ValueError("evidence_ids must be a list of strings")
    parsed = {
        "decision": decision,
        "evidence_ids": evidence_ids,
        "reason": str(payload.get("reason") or ""),
    }
    if parse_recovery:
        parsed["parse_recovery"] = parse_recovery
    return parsed


def probe_case_public_dict(case: ProbeCase) -> dict[str, Any]:
    """Serializable build artifact; scoring labels intentionally stay separate."""
    return {
        "schema": "multimodalcode-decision-probe-case-1",
        "benchmark": case.benchmark,
        "task": case.task,
        "source_trajectory": case.source_trajectory,
        "public_request": case.public_request,
        "public_image_paths": case.public_image_paths,
        "current_state": case.current_state,
        "records": [asdict(record) for record in case.records],
    }


def probe_case_label_dict(case: ProbeCase) -> dict[str, Any]:
    return {
        "schema": "multimodalcode-decision-probe-label-1",
        "benchmark": case.benchmark,
        "task": case.task,
        "stratum": case.stratum,
        "detector_label": case.detector_label,
        "manual_label": case.manual_label,
        "external_outcome": case.external_outcome,
    }


def write_jsonl(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
