from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..backends import ModelBackend
from ..io import read_json, write_json
from ..schema import GenerationRequest


@dataclass(frozen=True)
class ContextBudget:
    max_text_tokens: int = 4096
    max_images: int = 4
    max_image_pixels: int = 8_000_000


@dataclass(frozen=True)
class ContextRecord:
    record_id: str
    kind: str
    text: str
    code_version: str
    sequence: int
    checklist_id: Optional[str] = None
    status: Optional[str] = None
    image_path: Optional[str] = None
    image_pixels: int = 0
    is_first_failure: bool = False
    is_regression: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def estimated_text_tokens(self) -> int:
        # Conservative dependency-free estimate used identically for all policies.
        return max(1, (len(self.text.encode("utf-8")) + 3) // 4)


@dataclass(frozen=True)
class ContextSelection:
    policy: str
    records: List[ContextRecord]
    omitted_ids: List[str]
    estimated_text_tokens: int
    image_count: int
    image_pixels: int
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy": self.policy,
            "records": [asdict(record) for record in self.records],
            "omitted_ids": self.omitted_ids,
            "estimated_text_tokens": self.estimated_text_tokens,
            "image_count": self.image_count,
            "image_pixels": self.image_pixels,
            "metadata": self.metadata,
        }

    def render_text(self) -> str:
        return "\n\n".join(record.text for record in self.records)

    @property
    def image_paths(self) -> List[str]:
        return [record.image_path for record in self.records if record.image_path]


class ContextPolicy:
    name = "base"

    def order(self, records: Sequence[ContextRecord], current_version: str) -> List[ContextRecord]:
        raise NotImplementedError

    def prepare(
        self,
        records: Sequence[ContextRecord],
        current_version: str,
        budget: ContextBudget,
    ) -> tuple[List[ContextRecord], Dict[str, Any]]:
        return self.order(records, current_version), {}

    def select(
        self,
        records: Sequence[ContextRecord],
        *,
        current_version: str,
        budget: ContextBudget,
    ) -> ContextSelection:
        ordered, selection_metadata = self.prepare(records, current_version, budget)
        selection_metadata = {
            "selection_code_version": current_version,
            **selection_metadata,
        }
        selected: List[ContextRecord] = []
        omitted: List[str] = []
        text_tokens = image_count = image_pixels = 0
        for record in ordered:
            record_tokens = record.estimated_text_tokens
            record_images = int(bool(record.image_path))
            if (
                text_tokens + record_tokens > budget.max_text_tokens
                or image_count + record_images > budget.max_images
                or image_pixels + record.image_pixels > budget.max_image_pixels
            ):
                omitted.append(record.record_id)
                continue
            selected.append(record)
            text_tokens += record_tokens
            image_count += record_images
            image_pixels += record.image_pixels
        selected.sort(key=lambda record: record.sequence)
        selected_ids = {record.record_id for record in selected}
        omitted.extend(
            record.record_id
            for record in records
            if record.record_id not in selected_ids and record.record_id not in omitted
        )
        return ContextSelection(
            policy=self.name,
            records=selected,
            omitted_ids=omitted,
            estimated_text_tokens=text_tokens,
            image_count=image_count,
            image_pixels=image_pixels,
            metadata=selection_metadata,
        )


class FullHistoryPolicy(ContextPolicy):
    name = "full"

    def order(self, records: Sequence[ContextRecord], current_version: str) -> List[ContextRecord]:
        # A full-history chat evicts the oldest prefix when the common context
        # window is exceeded. Selection is newest-first, then ContextSelection
        # restores chronological presentation order for the retained tail.
        return sorted(records, key=lambda record: record.sequence, reverse=True)


class RecentPolicy(ContextPolicy):
    name = "recent"

    def __init__(self, k: int = 8):
        if k <= 0:
            raise ValueError("RecentPolicy k must be positive")
        self.k = int(k)

    def order(self, records: Sequence[ContextRecord], current_version: str) -> List[ContextRecord]:
        return sorted(records, key=lambda record: record.sequence, reverse=True)[: self.k]


class SummaryPolicy(ContextPolicy):
    """Deterministic status summary baseline with no additional model call."""

    name = "summary"

    def order(self, records: Sequence[ContextRecord], current_version: str) -> List[ContextRecord]:
        obligations = [
            record
            for record in records
            if record.kind in {"verification_residual", "verified_obligation"}
        ]
        latest: Dict[str, ContextRecord] = {}
        for record in obligations:
            key = record.checklist_id or record.record_id
            if key not in latest or record.sequence > latest[key].sequence:
                latest[key] = record
        if not latest:
            return []
        current = sum(record.code_version == current_version for record in latest.values())
        stale = len(latest) - current
        lines = [
            "[Deterministic trajectory summary]",
            f"Latest checklist states: {len(latest)}; current-version: {current}; stale: {stale}.",
        ]
        for checklist_id, record in sorted(latest.items()):
            version_label = "current" if record.code_version == current_version else "stale"
            lines.append(
                f"- {checklist_id}: {record.status} ({version_label}); "
                + _compact_record_text(record.text)
            )
        summary = ContextRecord(
            record_id=f"summary:{current_version}:{max(r.sequence for r in latest.values())}",
            kind="trajectory_summary",
            text="\n".join(lines),
            code_version=current_version,
            sequence=max(record.sequence for record in latest.values()),
            metadata={"source_record_ids": [record.record_id for record in latest.values()]},
        )
        return [summary]


class LLMSummaryPolicy(ContextPolicy):
    """Natural-language trajectory summary with explicit, cached policy cost."""

    name = "llm_summary"

    def __init__(
        self,
        *,
        backend: ModelBackend,
        artifact_dir: str | Path,
        model_name: str,
        max_tokens: int = 512,
        temperature: float = 0.0,
        seed: Optional[int] = 0,
    ):
        self.backend = backend
        self.artifact_dir = Path(artifact_dir).resolve()
        self.model_name = model_name
        self.max_tokens = int(max_tokens)
        self.temperature = float(temperature)
        self.seed = seed
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    def order(self, records: Sequence[ContextRecord], current_version: str) -> List[ContextRecord]:
        raise RuntimeError("LLMSummaryPolicy requires the common context budget")

    def prepare(
        self,
        records: Sequence[ContextRecord],
        current_version: str,
        budget: ContextBudget,
    ) -> tuple[List[ContextRecord], Dict[str, Any]]:
        # Give the summarizer the same text budget as the generator policies.
        # Images are deliberately not sent to this text-summary baseline, but
        # image-bearing records remain eligible for their textual evidence.
        source = FullHistoryPolicy().select(
            records,
            current_version=current_version,
            budget=ContextBudget(
                max_text_tokens=budget.max_text_tokens,
                max_images=1_000_000,
                max_image_pixels=10**15,
            ),
        )
        if not source.records:
            return [], {
                "context_policy_calls": 0,
                "context_policy_input_tokens": 0,
                "context_policy_output_tokens": 0,
                "context_policy_seconds": 0.0,
            }
        source_text = source.render_text()
        prompt = (
            "Summarize the coding-agent trajectory below for the next code revision. "
            "Preserve concrete current failures, expected versus observed behavior, "
            "first failing actions, regressions, relevant console/DOM evidence, and "
            "already attempted fixes. Mark stale evidence as stale. Do not propose a "
            "patch and do not invent evidence. Be concise.\n\n"
            f"Current code version: {current_version}\n\n{source_text}"
        )
        execution_ids = sorted(
            {
                str(record.metadata.get("execution_id"))
                for record in source.records
                if record.metadata.get("execution_id")
            }
        )
        key_payload = {
            "current_version": current_version,
            "record_ids": [record.record_id for record in source.records],
            "execution_ids": execution_ids,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "model": self.model_name,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "seed": self.seed,
        }
        artifact_key = hashlib.sha256(
            json.dumps(key_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        target = self.artifact_dir / artifact_key
        target.mkdir(parents=True, exist_ok=True)
        request_path = target / "request.json"
        response_path = target / "response.txt"
        metrics_path = target / "metrics.json"
        request_payload = {
            **key_payload,
            "prompt": prompt,
            "source_estimated_tokens": source.estimated_text_tokens,
            "source_image_records": source.image_count,
        }
        if not request_path.exists():
            write_json(request_path, request_payload)
        elif read_json(request_path) != request_payload:
            raise ValueError("Refusing to reuse an LLM summary for a different request")
        if response_path.exists() and metrics_path.exists():
            response = response_path.read_text(encoding="utf-8")
            metrics = read_json(metrics_path)
        else:
            started = time.monotonic()
            response = self.backend.generate(
                GenerationRequest(
                    prompt=prompt,
                    image_paths=[],
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    seed=self.seed,
                    system_prompt=(
                        "You summarize execution evidence for a coding agent. Return "
                        "only a faithful compact summary, never code or a patch."
                    ),
                )
            )
            elapsed = time.monotonic() - started
            response_path.write_text(response, encoding="utf-8")
            metrics = {
                "context_policy_calls": 1,
                "context_policy_input_tokens": _estimated_tokens(prompt),
                "context_policy_output_tokens": _estimated_tokens(response),
                "context_policy_seconds": elapsed,
            }
            write_json(metrics_path, metrics)
        rendered = "[LLM trajectory summary]\n" + response.strip()
        rendered = _truncate_to_estimated_tokens(rendered, budget.max_text_tokens)
        summary = ContextRecord(
            record_id=f"llm-summary:{artifact_key}",
            kind="trajectory_summary",
            text=rendered,
            code_version=current_version,
            sequence=max(record.sequence for record in source.records),
            metadata={
                "artifact_dir": str(target),
                "source_record_ids": [record.record_id for record in source.records],
                "raw_response_sha256": hashlib.sha256(response.encode("utf-8")).hexdigest(),
            },
        )
        return [summary], dict(metrics)


class ResidualPolicy(ContextPolicy):
    """Current-version open residuals, with regressions and first failures first."""

    name = "residual"

    def order(self, records: Sequence[ContextRecord], current_version: str) -> List[ContextRecord]:
        candidates = [
            record
            for record in records
            if record.kind == "verification_residual"
            and record.code_version == current_version
            and record.status in {"fail", "blocked"}
        ]
        return sorted(
            candidates,
            key=lambda record: (
                not record.is_regression,
                not record.is_first_failure,
                -record.sequence,
            ),
        )


class FrontierPolicy(ContextPolicy):
    """Linear ablation: retain only the earliest current open residual."""

    name = "frontier"

    def order(self, records: Sequence[ContextRecord], current_version: str) -> List[ContextRecord]:
        candidates = [
            record
            for record in records
            if record.kind == "verification_residual"
            and record.code_version == current_version
            and record.status in {"fail", "blocked"}
        ]
        if not candidates:
            return []

        def frontier_key(record: ContextRecord) -> tuple[bool, int, int]:
            raw_index = record.metadata.get("first_failing_action")
            has_no_index = raw_index is None
            return (has_no_index, int(raw_index or 0), record.sequence)

        return [min(candidates, key=frontier_key)]


class GuardedFrontierPolicy(FrontierPolicy):
    """Current counterexample plus obligations closed by the last revision."""

    name = "guarded_frontier"

    def order(self, records: Sequence[ContextRecord], current_version: str) -> List[ContextRecord]:
        frontier = super().order(records, current_version)
        if not frontier:
            return []
        newly_certified = [
            record
            for record in records
            if record.kind == "verified_obligation"
            and record.code_version == current_version
            and bool(record.metadata.get("is_newly_passed"))
        ]
        # The failure remains first in budget priority. ContextSelection later
        # restores checklist/sequence order for presentation.
        return frontier + sorted(
            newly_certified, key=lambda record: record.sequence, reverse=True
        )


class GuardedFrontierTextOnlyPolicy(GuardedFrontierPolicy):
    """Image-evidence ablation that preserves the selected certificate text."""

    name = "guarded_frontier_text_only"

    def prepare(
        self,
        records: Sequence[ContextRecord],
        current_version: str,
        budget: ContextBudget,
    ) -> tuple[List[ContextRecord], Dict[str, Any]]:
        ordered = self.order(records, current_version)
        stripped = [
            replace(record, image_path=None, image_pixels=0) for record in ordered
        ]
        return stripped, {
            "image_ablation": "strip_selected_images_preserve_text",
            "stripped_image_records": sum(
                1 for record in ordered if record.image_path is not None
            ),
        }


class ConservedFrontierPolicy(ContextPolicy):
    """A lossless obligation ledger plus one current executable frontier.

    Context compression may shorten evidence, but it must not silently erase
    the fact that a requirement is still open.  Consequently, the ledger is
    selected atomically and the policy fails closed when the common text budget
    cannot encode every observed requirement state.  Detailed stale evidence is
    deliberately withheld: after a state change it creates a recheck obligation
    rather than a license to patch or submit.
    """

    name = "conserved_frontier"
    _OBLIGATION_KINDS = {"verification_residual", "verified_obligation"}
    _OPEN_STATES = {"REFUTED", "BLOCKED", "RECHECK"}

    def order(
        self, records: Sequence[ContextRecord], current_version: str
    ) -> List[ContextRecord]:
        # ``prepare`` needs the common budget to enforce atomic ledger admission.
        ordered, _ = self._build(records, current_version)
        return ordered

    def prepare(
        self,
        records: Sequence[ContextRecord],
        current_version: str,
        budget: ContextBudget,
    ) -> tuple[List[ContextRecord], Dict[str, Any]]:
        ordered, metadata = self._build(records, current_version)
        if not ordered:
            return [], metadata
        ledger = ordered[0]
        if ledger.estimated_text_tokens > budget.max_text_tokens:
            raise ValueError(
                "Context budget cannot encode the conserved requirement ledger: "
                f"need {ledger.estimated_text_tokens} text tokens, "
                f"have {budget.max_text_tokens}"
            )
        return ordered, metadata

    def _build(
        self, records: Sequence[ContextRecord], current_version: str
    ) -> tuple[List[ContextRecord], Dict[str, Any]]:
        obligations = [
            record
            for record in records
            if record.kind in self._OBLIGATION_KINDS and record.checklist_id
        ]
        if not obligations:
            return [], {
                "obligation_count": 0,
                "open_obligation_count": 0,
                "open_obligation_ids": [],
                "ledger_atomic": True,
            }

        latest: Dict[str, ContextRecord] = {}
        current: Dict[str, ContextRecord] = {}
        for record in obligations:
            checklist_id = str(record.checklist_id)
            if (
                checklist_id not in latest
                or record.sequence > latest[checklist_id].sequence
            ):
                latest[checklist_id] = record
            if record.code_version == current_version and (
                checklist_id not in current
                or record.sequence > current[checklist_id].sequence
            ):
                current[checklist_id] = record

        states: Dict[str, str] = {}
        source_ids: Dict[str, str] = {}
        lines = [
            "[Conserved requirement ledger]",
            "Invariant: only requirement-matched evidence from the current program "
            "state can discharge an obligation.",
            "REFUTED/BLOCKED/RECHECK remain live: do not submit or claim success "
            "until each becomes SUPPORTED. BLOCKED calls for retry/escalation, not "
            "an unsupported code change.",
        ]
        for checklist_id in sorted(latest):
            source = current.get(checklist_id)
            if source is None:
                state = "RECHECK"
                source = latest[checklist_id]
                evidence_label = "stale-payload-withheld"
                compact = "re-run a requirement-matched check on the current state"
            else:
                state = self._state_for_status(source.status)
                evidence_label = "current"
                compact = _compact_record_text(source.text, max_chars=220)
            states[checklist_id] = state
            source_ids[checklist_id] = source.record_id
            lines.append(
                f"- {checklist_id} | {state} | {evidence_label} | {compact}"
            )

        open_ids = sorted(
            checklist_id
            for checklist_id, state in states.items()
            if state in self._OPEN_STATES
        )
        lines.insert(
            3,
            (
                f"Completion state: BLOCKED by {len(open_ids)} live obligation(s): "
                f"{', '.join(open_ids)}"
                if open_ids
                else "Completion state: all observed requirements SUPPORTED."
            ),
        )
        minimum_sequence = min(record.sequence for record in obligations)
        maximum_sequence = max(record.sequence for record in obligations)
        ledger = ContextRecord(
            record_id=f"conserved-ledger:{current_version}:{maximum_sequence}",
            kind="requirement_ledger",
            text="\n".join(lines),
            code_version=current_version,
            sequence=minimum_sequence - 1,
            metadata={
                "obligation_states": states,
                "source_record_ids": source_ids,
                "open_obligation_ids": open_ids,
                "ledger_atomic": True,
                "stale_payload_policy": "withhold_and_recheck",
            },
        )

        # Preserve one rich current-state counterexample after the atomic ledger.
        # Regression is prioritized, then the earliest failing action identifies
        # the smallest executable causal boundary.
        current_open = [
            current[checklist_id]
            for checklist_id in open_ids
            if checklist_id in current
            and states[checklist_id] in {"REFUTED", "BLOCKED"}
        ]
        frontier: List[ContextRecord] = []
        if current_open:
            frontier = [
                min(
                    current_open,
                    key=lambda record: (
                        not record.is_regression,
                        record.metadata.get("first_failing_action") is None,
                        int(record.metadata.get("first_failing_action") or 0),
                        record.sequence,
                    ),
                )
            ]
        metadata = {
            "obligation_count": len(states),
            "open_obligation_count": len(open_ids),
            "open_obligation_ids": open_ids,
            "obligation_states": states,
            "ledger_atomic": True,
            "ledger_estimated_text_tokens": ledger.estimated_text_tokens,
            "detailed_frontier_id": frontier[0].record_id if frontier else None,
        }
        return [ledger, *frontier], metadata

    @staticmethod
    def _state_for_status(status: Optional[str]) -> str:
        normalized = str(status or "").strip().lower()
        if normalized == "pass":
            return "SUPPORTED"
        if normalized == "fail":
            return "REFUTED"
        # Unknown/inconclusive results cannot discharge an obligation.  Treating
        # them as blocked is the conservative operational state.
        return "BLOCKED"


class UnboundFrontierPolicy(GuardedFrontierPolicy):
    """State-validity ablation that permits stale counterexamples and guards."""

    name = "unbound_frontier"

    def order(self, records: Sequence[ContextRecord], current_version: str) -> List[ContextRecord]:
        candidates = [
            record
            for record in records
            if record.kind == "verification_residual"
            and record.status in {"fail", "blocked"}
        ]
        if not candidates:
            return []

        def frontier_key(record: ContextRecord) -> tuple[bool, int, int]:
            raw_index = record.metadata.get("first_failing_action")
            return (raw_index is None, int(raw_index or 0), record.sequence)

        frontier = min(candidates, key=frontier_key)
        newly_certified = [
            record
            for record in records
            if record.kind == "verified_obligation"
            and bool(record.metadata.get("is_newly_passed"))
        ]
        return [frontier] + sorted(
            newly_certified, key=lambda record: record.sequence, reverse=True
        )


class OracleEvidencePolicy(ContextPolicy):
    """Gold dependency-DAG frontier supplied by the case manifest."""

    name = "oracle"

    def order(self, records: Sequence[ContextRecord], current_version: str) -> List[ContextRecord]:
        current = [
            record
            for record in records
            if record.code_version == current_version
            and record.kind in {"verification_residual", "verified_obligation"}
        ]
        status = {
            str(record.checklist_id): str(record.status)
            for record in current
            if record.checklist_id
        }
        open_records = [
            record
            for record in current
            if record.kind == "verification_residual"
            and record.status in {"fail", "blocked"}
        ]
        eligible = []
        for record in open_records:
            predecessors = [
                str(value) for value in record.metadata.get("oracle_predecessors", [])
            ]
            if all(status.get(predecessor) == "pass" for predecessor in predecessors):
                eligible.append(record)
        return sorted(
            eligible,
            key=lambda record: (
                record.metadata.get("first_failing_action") is None,
                int(record.metadata.get("first_failing_action") or 0),
                record.sequence,
            ),
        )


def _compact_record_text(text: str, max_chars: int = 500) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= max_chars else compact[: max_chars - 1] + "…"


def _estimated_tokens(text: str) -> int:
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def _truncate_to_estimated_tokens(text: str, max_tokens: int) -> str:
    max_bytes = max(0, int(max_tokens) * 4)
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    suffix = "\n[summary truncated to common context budget]"
    suffix_bytes = suffix.encode("utf-8")
    prefix = encoded[: max(0, max_bytes - len(suffix_bytes))]
    while prefix:
        try:
            decoded = prefix.decode("utf-8")
            return decoded + suffix
        except UnicodeDecodeError:
            prefix = prefix[:-1]
    return suffix[:max_bytes]
