from __future__ import annotations

import json
import hashlib
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from PIL import Image

from ..backends import ModelBackend
from ..io import append_jsonl, read_json, write_json
from ..schema import GenerationRequest
from .context import ContextBudget, ContextPolicy, ContextRecord
from .events import EventLog
from .interactive import InteractiveJudge, hash_program
from .schema import ActionPlan
from .tools import SafeFileToolExecutor, parse_revision


SOURCE_CONTEXT_MODES = {"full", "execution_rooted"}


@dataclass(frozen=True)
class AgentCase:
    case_id: str
    task: str
    program_path: str
    plan: ActionPlan
    task_image_paths: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class AgentLoop:
    """Minimal revision loop whose only experimental variable is ContextPolicy."""

    def __init__(
        self,
        *,
        backend: ModelBackend,
        model_name: str,
        run_dir: str | Path,
        policy: ContextPolicy,
        budget: ContextBudget,
        max_revisions: int = 2,
        max_contract_retries: int = 1,
        max_tokens: int = 8192,
        temperature: float = 0.0,
        seed: Optional[int] = 0,
        record_video: bool = True,
        fresh_certification: bool = True,
        source_context_mode: str = "full",
    ):
        self.backend = backend
        self.model_name = model_name
        self.run_dir = Path(run_dir).resolve()
        self.policy = policy
        self.budget = budget
        self.max_revisions = max_revisions
        self.max_contract_retries = max(0, int(max_contract_retries))
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.seed = seed
        self.record_video = bool(record_video)
        self.fresh_certification = bool(fresh_certification)
        if source_context_mode not in SOURCE_CONTEXT_MODES:
            raise ValueError(
                f"Unsupported source context mode {source_context_mode!r}; "
                f"expected one of {sorted(SOURCE_CONTEXT_MODES)}"
            )
        self.source_context_mode = source_context_mode
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.event_log = EventLog(self.run_dir / "events.jsonl")
        self.judge = InteractiveJudge(self.run_dir, event_log=self.event_log)

    def run(self, case: AgentCase, *, resume: bool = True) -> Dict[str, Any]:
        case_dir = self.run_dir / "cases" / case.case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        state_path = case_dir / "state.json"
        if resume and state_path.exists():
            existing = read_json(state_path)
            existing = self._reconcile_artifact_counts(case_dir, existing)
            if existing.get("status") == "complete":
                write_json(state_path, existing)
                return existing
        tool = SafeFileToolExecutor.prepare(
            case.program_path,
            case_dir / "workspace",
            case_dir / "checkpoints",
        )
        frozen_config = {
            "case_id": case.case_id,
            "task": case.task,
            "model": self.model_name,
            "policy": self.policy.name,
            "policy_parameters": {
                "recent_k": getattr(self.policy, "k", None),
            },
            "budget": asdict(self.budget),
            "max_revisions": self.max_revisions,
            "max_contract_retries": self.max_contract_retries,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "seed": self.seed,
            "record_video": self.record_video,
            "fresh_certification": self.fresh_certification,
            "source_context_mode": self.source_context_mode,
            "task_image_paths": case.task_image_paths,
            "metadata": case.metadata,
            "plan": case.plan.to_dict(),
        }
        config_path = case_dir / "config.json"
        if not config_path.exists():
            write_json(config_path, frozen_config)
        elif read_json(config_path) != frozen_config:
            raise ValueError("Refusing to resume with a different frozen configuration")

        state = read_json(state_path) if state_path.exists() else {
            "status": "running",
            "case_id": case.case_id,
            "next_iteration": 0,
            "best_score": -1.0,
            "best_version": None,
            "generator_calls": 0,
            "verifier_calls": 0,
        }
        state = self._reconcile_artifact_counts(case_dir, state)
        records = self._load_context_records(case_dir / "context_records.jsonl")
        previous_status = self._latest_checklist_status(case_dir)

        for iteration in range(int(state.get("next_iteration", 0)), self.max_revisions + 1):
            version = hash_program(tool.workspace)
            tool.checkpoint(version)
            report_path = case_dir / "reports" / f"iteration-{iteration:02d}.json"
            report = None
            if resume and report_path.exists():
                candidate = read_json(report_path)
                if candidate.get("code_version") == version and candidate.get("status") == "ok":
                    report = candidate
            if report is None:
                verifier_started = time.monotonic()
                report = self.judge.execute(
                    case_id=case.case_id,
                    program_path=tool.workspace,
                    plan=case.plan,
                    purpose=f"iteration-{iteration}",
                    record_video=self.record_video,
                )
                report_path.parent.mkdir(parents=True, exist_ok=True)
                write_json(report_path, report)
                state["verifier_calls"] = int(state.get("verifier_calls", 0)) + 1
                state["verifier_seconds"] = float(
                    state.get("verifier_seconds", 0.0)
                ) + (time.monotonic() - verifier_started)
            new_records = self._report_records(
                report,
                previous_status,
                len(records),
                oracle_predecessors=case.metadata.get("oracle_predecessors", {}),
            )
            self._append_new_records(case_dir / "context_records.jsonl", records, new_records)
            records.extend(record for record in new_records if record.record_id not in {r.record_id for r in records})
            previous_status = {
                item["checklist_id"]: item["status"] for item in report.get("checklist", [])
            }

            score = float(report.get("score", 0.0))
            if score > float(state.get("best_score", -1.0)):
                state["best_score"] = score
                state["best_version"] = version
            state["last_score"] = score
            state["last_version"] = version
            state["next_iteration"] = iteration
            write_json(state_path, state)
            if score >= 1.0 or iteration >= self.max_revisions:
                break

            selection = self.policy.select(
                records, current_version=version, budget=self.budget
            )
            context_path = case_dir / "contexts" / f"iteration-{iteration:02d}.json"
            context_path.parent.mkdir(parents=True, exist_ok=True)
            write_json(context_path, selection.to_dict())
            program_context, source_manifest = self._program_source_context(
                tool, report
            )
            source_context_path = (
                case_dir / "source_contexts" / f"iteration-{iteration:02d}.json"
            )
            source_context_path.parent.mkdir(parents=True, exist_ok=True)
            if not source_context_path.exists():
                write_json(source_context_path, source_manifest)
            elif read_json(source_context_path) != source_manifest:
                raise ValueError(
                    "Refusing to resume with a different source-context selection"
                )
            prompt = self._revision_prompt(
                case,
                tool,
                selection.render_text(),
                program_context=program_context,
            )
            stop_requested = False
            admitted = False
            rejection_message = ""
            for attempt in range(self.max_contract_retries + 1):
                stem = _attempt_stem(iteration, attempt)
                attempt_prompt = prompt
                if rejection_message:
                    attempt_prompt += self._contract_retry_suffix(rejection_message)
                prompt_path = case_dir / "prompts" / f"{stem}.txt"
                prompt_path.parent.mkdir(parents=True, exist_ok=True)
                prompt_path.write_text(attempt_prompt, encoding="utf-8")
                response_path = case_dir / "responses" / f"{stem}.txt"
                response_path.parent.mkdir(parents=True, exist_ok=True)
                generation_request = GenerationRequest(
                    prompt=attempt_prompt,
                    image_paths=case.task_image_paths + selection.image_paths,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    seed=self.seed,
                    system_prompt=(
                        "You are a coding agent. Return only one JSON object following "
                        "the revision contract in the user message."
                    ),
                )
                request_path = case_dir / "requests" / f"{stem}.json"
                request_path.parent.mkdir(parents=True, exist_ok=True)
                request_manifest = _generation_request_manifest(
                    generation_request, model_name=self.model_name
                )
                if not request_path.exists():
                    write_json(request_path, request_manifest)
                elif read_json(request_path) != request_manifest:
                    raise ValueError(
                        "Refusing to reuse a response for a different model request"
                    )
                if resume and response_path.exists():
                    response = response_path.read_text(encoding="utf-8")
                else:
                    generator_started = time.monotonic()
                    response = self.backend.generate(generation_request)
                    response_path.write_text(response, encoding="utf-8")
                    state["generator_calls"] = int(state.get("generator_calls", 0)) + 1
                    state["generator_seconds"] = float(
                        state.get("generator_seconds", 0.0)
                    ) + (time.monotonic() - generator_started)
                    # A malformed-but-saved response is still a real call.
                    write_json(state_path, state)
                try:
                    revision = parse_revision(response)
                    write_json(case_dir / "responses" / f"{stem}.json", revision)
                    decision = revision["decision"]
                    if decision == "patch":
                        written, rejected_existing_payloads = (
                            tool.apply_revision_with_admission(
                                files=revision["files"], edits=revision["edits"]
                            )
                        )
                        self.event_log.append(
                            "patch_applied",
                            {
                                "iteration": iteration,
                                "attempt": attempt,
                                "files": written,
                                "exact_edit_count": len(revision["edits"]),
                                "new_file_count": (
                                    len(revision["files"])
                                    - len(rejected_existing_payloads)
                                ),
                                "proposed_full_content_count": len(revision["files"]),
                                "rejected_existing_file_payloads": (
                                    rejected_existing_payloads
                                ),
                                "normalizations": revision.get("normalizations", []),
                                "reason": revision["reason"],
                            },
                            case_id=case.case_id,
                            code_version=version,
                        )
                    elif decision == "rollback":
                        selected = str(
                            revision.get("checkpoint")
                            or state.get("best_version")
                            or ""
                        )
                        if not selected:
                            raise ValueError(
                                "Rollback requested without an available checkpoint"
                            )
                        tool.restore(selected)
                    elif decision in {"keep", "stop"}:
                        state["stop_reason"] = f"model_{decision}"
                        stop_requested = True
                    admitted = True
                    break
                except (FileNotFoundError, ValueError) as exc:
                    # Admission must be state preserving. Restore defensively if
                    # a future executor implementation violates that invariant.
                    if hash_program(tool.workspace) != version:
                        tool.restore(version)
                    rejection_message = f"{type(exc).__name__}: {exc}"
                    rejection_path = case_dir / "rejections" / f"{stem}.json"
                    if not rejection_path.exists():
                        rejection = {
                            "iteration": iteration,
                            "attempt": attempt,
                            "code_version": version,
                            "error": rejection_message,
                            "response_path": str(response_path),
                            "workspace_unchanged": hash_program(tool.workspace) == version,
                        }
                        write_json(rejection_path, rejection)
                        self.event_log.append(
                            "revision_rejected",
                            rejection,
                            case_id=case.case_id,
                            code_version=version,
                        )
                    state = self._reconcile_artifact_counts(case_dir, state)
                    state["last_revision_rejection"] = rejection_message
                    write_json(state_path, state)
                    if attempt >= self.max_contract_retries:
                        raise
            if not admitted:
                raise RuntimeError("Revision admission ended without a decision")
            if stop_requested:
                write_json(state_path, state)
                break
            state["next_iteration"] = iteration + 1
            write_json(state_path, state)

        best_version = str(state.get("best_version") or hash_program(tool.workspace))
        if hash_program(tool.workspace) != best_version:
            tool.restore(best_version)
            state["restored_best_checkpoint"] = True
            state["rollback_useful"] = float(state.get("best_score", 0.0)) > float(
                state.get("last_score", 0.0)
            )
        if self.fresh_certification:
            certification_started = time.monotonic()
            certification = self.judge.certify(
                case_id=case.case_id,
                program_path=tool.workspace,
                plan=case.plan,
                record_video=self.record_video,
            )
            state["verifier_calls"] = int(state.get("verifier_calls", 0)) + 1
            state["verifier_seconds"] = float(state.get("verifier_seconds", 0.0)) + (
                time.monotonic() - certification_started
            )
        else:
            historical = [
                read_json(path)
                for path in sorted((case_dir / "reports").glob("iteration-*.json"))
                if path.is_file()
            ]
            matches = [
                report
                for report in historical
                if str(report.get("code_version")) == best_version
                and report.get("status") == "ok"
            ]
            if not matches:
                raise RuntimeError(
                    "Historical-certification ablation found no verifier result "
                    "bound to the submitted code version"
                )
            certification = dict(matches[-1])
            certification["fresh_certification"] = False
            certification["historical_result_reused"] = True
        state = self._reconcile_artifact_counts(case_dir, state)
        state.update(
            {
                "status": "complete",
                "final_version": certification["code_version"],
                "final_score": certification["score"],
                "fresh_certification": bool(certification["fresh_certification"]),
                "historical_result_reused": bool(
                    certification.get("historical_result_reused", False)
                ),
                "certification_result": certification["result_path"],
            }
        )
        write_json(state_path, state)
        return state

    def recover_state(self, case_id: str) -> Dict[str, Any]:
        case_dir = self.run_dir / "cases" / case_id
        state_path = case_dir / "state.json"
        if not state_path.exists():
            return {}
        state = self._reconcile_artifact_counts(case_dir, read_json(state_path))
        write_json(state_path, state)
        return state

    def _reconcile_artifact_counts(
        self, case_dir: Path, state: Dict[str, Any]
    ) -> Dict[str, Any]:
        reconciled = dict(state)
        saved_responses = sum(
            path.is_file() and path.stat().st_size > 0
            for path in (case_dir / "responses").glob("iteration-*.txt")
        )
        reconciled["generator_calls"] = max(
            int(reconciled.get("generator_calls", 0)), int(saved_responses)
        )
        contexts = []
        for path in sorted((case_dir / "contexts").glob("iteration-*.json")):
            try:
                row = read_json(path)
                match = re.search(r"iteration-(\d+)", path.stem)
                row["_iteration"] = int(match.group(1)) if match else None
                contexts.append(row)
            except (OSError, json.JSONDecodeError):
                continue
        reconciled["context_turns"] = len(contexts)
        reconciled["context_estimated_tokens"] = sum(
            int(row.get("estimated_text_tokens", 0)) for row in contexts
        )
        reconciled["context_images"] = sum(int(row.get("image_count", 0)) for row in contexts)
        reconciled["context_image_pixels"] = sum(
            int(row.get("image_pixels", 0)) for row in contexts
        )
        source_contexts = []
        for path in sorted((case_dir / "source_contexts").glob("iteration-*.json")):
            try:
                source_contexts.append(read_json(path))
            except (OSError, json.JSONDecodeError):
                continue
        reconciled["source_context_turns"] = len(source_contexts)
        reconciled["source_context_rendered_bytes"] = sum(
            int(row.get("render", {}).get("rendered_bytes", 0))
            for row in source_contexts
        )
        reconciled["source_context_selected_files"] = sum(
            int(row.get("render", {}).get("selected_file_count", 0))
            for row in source_contexts
        )
        reconciled["source_context_fallbacks"] = sum(
            bool(row.get("runtime_source_context", {}).get("fallback"))
            for row in source_contexts
        )
        stale_by_iteration: Dict[int, int] = {}
        for row in contexts:
            selected_version = str(
                row.get("metadata", {}).get("selection_code_version", "")
            )
            stale_count = sum(
                str(record.get("code_version", "")) != selected_version
                for record in row.get("records", [])
            )
            if row.get("_iteration") is not None:
                stale_by_iteration[int(row["_iteration"])] = stale_count
        reconciled["stale_context_records"] = sum(stale_by_iteration.values())
        reconciled["stale_context_turns"] = sum(
            count > 0 for count in stale_by_iteration.values()
        )
        for metric in (
            "context_policy_calls",
            "context_policy_input_tokens",
            "context_policy_output_tokens",
            "context_policy_seconds",
        ):
            reconciled[metric] = sum(
                float(row.get("metadata", {}).get(metric, 0.0)) for row in contexts
            )
        for metric in (
            "context_policy_calls",
            "context_policy_input_tokens",
            "context_policy_output_tokens",
        ):
            reconciled[metric] = int(reconciled[metric])
        response_paths = [
            path
            for path in sorted((case_dir / "responses").glob("iteration-*.txt"))
            if path.is_file()
        ]
        reconciled["response_characters"] = sum(
            len(path.read_text(encoding="utf-8", errors="replace")) for path in response_paths
        )
        reconciled["contract_rejections"] = sum(
            path.is_file()
            for path in (case_dir / "rejections").glob("iteration-*.json")
        )
        reports = []
        reports_by_iteration: Dict[int, Dict[str, Any]] = {}
        for path in sorted((case_dir / "reports").glob("iteration-*.json")):
            try:
                report = read_json(path)
                reports.append(report)
                match = re.search(r"iteration-(\d+)", path.stem)
                if match:
                    reports_by_iteration[int(match.group(1))] = report
            except (OSError, json.JSONDecodeError):
                continue
        reconciled["stale_evidence_nonimproving_revision_count"] = sum(
            1
            for iteration, stale_count in stale_by_iteration.items()
            if stale_count > 0
            and iteration in reports_by_iteration
            and iteration + 1 in reports_by_iteration
            and float(reports_by_iteration[iteration + 1].get("score", 0.0))
            <= float(reports_by_iteration[iteration].get("score", 0.0))
        )
        if reports:
            reconciled["initial_score"] = float(reports[0].get("score", 0.0))
            reconciled["score_history"] = [float(row.get("score", 0.0)) for row in reports]
            regressions = 0
            previous: Dict[str, str] = {}
            once_correct_then_broken = set()
            for report in reports:
                current = {
                    str(item.get("checklist_id")): str(item.get("status"))
                    for item in report.get("checklist", [])
                }
                for checklist_id, status in current.items():
                    if previous.get(checklist_id) == "pass" and status != "pass":
                        regressions += 1
                        once_correct_then_broken.add(checklist_id)
                previous = current
            reconciled["regression_count"] = regressions
            reconciled["once_correct_then_broken_count"] = len(once_correct_then_broken)
        return reconciled

    def _revision_prompt(
        self,
        case: AgentCase,
        tool: SafeFileToolExecutor,
        selected_context: str,
        *,
        program_context: Optional[str] = None,
    ) -> str:
        rendered_program = (
            tool.render_program() if program_context is None else program_context
        )
        return f"""Task:\n{case.task}\n\nCurrent program:\n{rendered_program}\n\nSelected verification context:\n{selected_context or '[none]'}\n\nRevision contract:\nReturn JSON only. For existing files, use exact localized edits: {{\"decision\":\"patch\",\"edits\":[{{\"path\":\"relative/path\",\"old\":\"exact unique existing text\",\"new\":\"replacement text\"}}],\"files\":{{}},\"reason\":\"...\"}}. The old text must match exactly and uniquely; include enough surrounding text to disambiguate it. The files object is only for creating genuinely new files and must never reproduce an existing file. You may instead return decision keep or stop. Make the smallest repair, do not modify tests or verification artifacts, and preserve behavior not identified as failing."""

    def _program_source_context(
        self,
        tool: SafeFileToolExecutor,
        report: Dict[str, Any],
    ) -> tuple[str, Dict[str, Any]]:
        runtime_context = report.get("runtime_source_context")
        if not isinstance(runtime_context, dict):
            runtime_context = {}
        relative_paths: Optional[List[str]] = None
        if self.source_context_mode == "execution_rooted":
            relative_paths = [
                str(row.get("path"))
                for row in runtime_context.get("files", [])
                if isinstance(row, dict) and str(row.get("path", "")).strip()
            ]
            if not relative_paths:
                raise RuntimeError(
                    "Execution-rooted source context has no browser-mapped source file"
                )
        rendered, render_manifest = tool.render_program_context(
            relative_paths=relative_paths
        )
        if not rendered.strip():
            raise RuntimeError("Source-context selection rendered no editable program")
        return rendered, {
            "source_context_mode": self.source_context_mode,
            "selection_code_version": str(report.get("code_version", "")),
            "execution_id": report.get("execution_id"),
            "runtime_source_context": runtime_context,
            "render": render_manifest,
        }

    @staticmethod
    def _contract_retry_suffix(error: str) -> str:
        guidance = "Correct the JSON shape and typed file operation."
        if "target does not exist" in error:
            guidance = (
                "That path does not exist. Create it with complete contents in the "
                "top-level files object; exact edits are only for existing files."
            )
        elif "unknown fields" in error:
            guidance = (
                "Keep only path/old/new/replace_all/reason inside each edit; put files "
                "only at the top level."
            )
        elif "no-op" in error:
            guidance = "Make an actual localized change, or return keep/stop."
        return (
            "\n\nIndependent admission gate rejected the previous response. "
            "No code was changed.\n"
            f"Rejection: {error}\n{guidance}\n"
            "Return one corrected JSON object under the same revision contract."
        )

    def _report_records(
        self,
        report: Dict[str, Any],
        previous_status: Dict[str, str],
        start_sequence: int,
        oracle_predecessors: Optional[Dict[str, Sequence[str]]] = None,
    ) -> List[ContextRecord]:
        action_rows = list(report.get("actions", []))
        actions = {int(row["index"]): row for row in action_rows}
        global_console_errors = _local_console_errors(report.get("console_path"), 2400)
        open_items = [
            item
            for item in report.get("checklist", [])
            if str(item.get("status")) != "pass"
        ]
        error_anchor_id = None
        if open_items:
            anchor = min(
                open_items,
                key=lambda item: (
                    item.get("first_failing_action") is None,
                    int(item.get("first_failing_action") or 0),
                ),
            )
            error_anchor_id = str(anchor.get("checklist_id"))
        records: List[ContextRecord] = []
        for action in action_rows:
            evidence = dict(action.get("evidence", {}))
            image_path = evidence.get("screenshot")
            console_excerpt = _stable_evidence_text(
                _evidence_excerpt(evidence.get("console_delta"), 1200)
            )
            accessibility_excerpt = _stable_evidence_text(
                _evidence_excerpt(evidence.get("accessibility"), 2200)
            )
            dom_excerpt = _stable_evidence_text(
                _evidence_excerpt(evidence.get("dom"), 3500)
            )
            trace_text = (
                f"[Interactive trace action {action.get('index')}]\n"
                f"Checklist: {action.get('checklist_id')}\n"
                f"Action: {action.get('type')}\nStatus: {action.get('status')}\n"
                f"Expected: {_stable_evidence_text(str(action.get('expected')))}\n"
                f"Observed: {_stable_evidence_text(str(action.get('observed')))}\n"
                f"URL: {_stable_url(action.get('url'))}\nChanged: {action.get('changed')}\n"
                f"Note: {action.get('note')}\n"
                "Evidence captured: screenshot, DOM, accessibility, console delta.\n"
                f"Console delta:\n{console_excerpt or '[empty]'}\n"
                f"Accessibility snapshot excerpt:\n{accessibility_excerpt or '[empty]'}\n"
                f"DOM excerpt:\n{dom_excerpt or '[empty]'}"
            )
            records.append(
                ContextRecord(
                    record_id=f"{report['code_version']}:action:{action.get('index')}",
                    kind="interactive_trace",
                    text=trace_text,
                    code_version=str(report["code_version"]),
                    sequence=start_sequence + len(records),
                    checklist_id=action.get("checklist_id"),
                    status=action.get("status"),
                    image_path=image_path,
                    image_pixels=_image_pixels(image_path),
                    metadata={
                        "execution_id": report.get("execution_id"),
                        "action_index": action.get("index"),
                        "evidence": evidence,
                    },
                )
            )
        for item in report.get("checklist", []):
            checklist_id = str(item["checklist_id"])
            status = str(item["status"])
            failure_index = item.get("first_failing_action")
            action = actions.get(int(failure_index)) if failure_index is not None else None
            image_path = None if action is None else action.get("evidence", {}).get("screenshot")
            image_pixels = _image_pixels(image_path)
            action_evidence = {} if action is None else dict(action.get("evidence", {}))
            console_excerpt = _stable_evidence_text(
                _evidence_excerpt(action_evidence.get("console_delta"), 1600)
            )
            accessibility_excerpt = _stable_evidence_text(
                _evidence_excerpt(action_evidence.get("accessibility"), 2600)
            )
            is_regression = previous_status.get(checklist_id) == "pass" and status != "pass"
            is_newly_passed = (
                previous_status.get(checklist_id) in {"fail", "blocked"}
                and status == "pass"
            )
            label = "Verification residual" if status != "pass" else "Verified obligation"
            anchored_errors = (
                global_console_errors if checklist_id == error_anchor_id else ""
            )
            text = (
                f"[{label}: {checklist_id}]\n"
                f"Status: {status}\nExpected: {_stable_evidence_text(str(item.get('expected', '')))}\n"
                f"Observed: {_stable_evidence_text(str(item.get('observed', '')))}\n"
                f"First failing action: {failure_index}\n"
                f"Residual: {_stable_evidence_text(str(item.get('residual', '')))}\n"
                f"Current-version local console errors: "
                f"{anchored_errors or '[none]'}\n"
                f"Console evidence: {console_excerpt or '[empty]'}\n"
                f"Accessibility evidence: {accessibility_excerpt or '[empty]'}"
            )
            records.append(
                ContextRecord(
                    record_id=f"{report['code_version']}:{checklist_id}",
                    kind="verification_residual" if status != "pass" else "verified_obligation",
                    text=text,
                    code_version=str(report["code_version"]),
                    sequence=start_sequence + len(records),
                    checklist_id=checklist_id,
                    status=status,
                    image_path=image_path,
                    image_pixels=image_pixels,
                    is_first_failure=failure_index is not None,
                    is_regression=is_regression,
                    metadata={
                        "execution_id": report.get("execution_id"),
                        "first_failing_action": failure_index,
                        "has_global_error_certificate": bool(anchored_errors),
                        "is_newly_passed": is_newly_passed,
                        "oracle_predecessors": list(
                            (oracle_predecessors or {}).get(checklist_id, [])
                        ),
                    },
                )
            )
        return records

    def _load_context_records(self, path: Path) -> List[ContextRecord]:
        if not path.exists():
            return []
        return [
            ContextRecord(**json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _append_new_records(
        self, path: Path, existing: Sequence[ContextRecord], new: Sequence[ContextRecord]
    ) -> None:
        known = {record.record_id for record in existing}
        for record in new:
            if record.record_id not in known:
                append_jsonl(path, asdict(record))
                known.add(record.record_id)

    def _latest_checklist_status(self, case_dir: Path) -> Dict[str, str]:
        reports = sorted((case_dir / "reports").glob("iteration-*.json"))
        if not reports:
            return {}
        latest = read_json(reports[-1])
        return {item["checklist_id"]: item["status"] for item in latest.get("checklist", [])}


def _image_pixels(path: Optional[str]) -> int:
    if not path or not Path(path).is_file():
        return 0
    try:
        with Image.open(path) as image:
            return image.width * image.height
    except Exception:
        return 0


def _attempt_stem(iteration: int, attempt: int) -> str:
    base = f"iteration-{iteration:02d}"
    return base if attempt == 0 else f"{base}-retry-{attempt:02d}"


def _generation_request_manifest(
    request: GenerationRequest, *, model_name: str
) -> Dict[str, Any]:
    image_rows = []
    for raw_path in request.image_paths:
        path = Path(raw_path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        image_rows.append({"path": str(path), "sha256": digest})
    stable_payload = {
        "model": model_name,
        "system_prompt": request.system_prompt,
        "prompt": request.prompt,
        "image_sha256": [row["sha256"] for row in image_rows],
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "seed": request.seed,
    }
    canonical = json.dumps(
        stable_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "request_fingerprint": hashlib.sha256(canonical).hexdigest(),
        "prompt_sha256": hashlib.sha256(request.prompt.encode("utf-8")).hexdigest(),
        "model": model_name,
        "system_prompt": request.system_prompt,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "seed": request.seed,
        "images": image_rows,
    }


def _evidence_excerpt(path: Optional[str], max_chars: int) -> str:
    if not path or not Path(path).is_file():
        return ""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def _local_console_error_payload(path: Optional[str]) -> Dict[str, List[str]]:
    if not path or not Path(path).is_file():
        return {"missing_local_resources": [], "page_errors": []}
    try:
        events = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {"missing_local_resources": [], "page_errors": []}
    resources: List[str] = []
    page_errors: List[str] = []
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type", ""))
        event_text = str(event.get("text", ""))
        event_stack = str(event.get("stack", ""))
        location = event.get("location") if isinstance(event.get("location"), dict) else {}
        url = str(location.get("url", ""))
        stable_text = _stable_evidence_text(event_text)
        stable_stack = _stable_evidence_text(event_stack)
        stable_url = _stable_url(url)
        if event_type == "pageerror":
            detail = stable_text
            if stable_stack and stable_stack != stable_text:
                detail = f"{detail}\n{stable_stack}" if detail else stable_stack
            if stable_url:
                detail = f"{detail}\nLocation: {stable_url}" if detail else stable_url
            if detail and detail not in page_errors:
                page_errors.append(detail)
            continue
        if not (
            event_type == "requestfailed"
            or "ERR_FILE_NOT_FOUND" in event_text
            or "Failed to load resource" in event_text
        ):
            continue
        candidates = re.findall(
            r"workspace/[^\s\"'<>]+", f"{stable_text} {stable_url}"
        )
        for resource in candidates:
            cleaned = resource.rstrip(".,;:)")
            if cleaned not in resources:
                resources.append(cleaned)
    return {
        "missing_local_resources": resources,
        "page_errors": page_errors,
    }


def _render_global_error_payload(
    payload: Dict[str, List[str]], *, max_chars: int
) -> str:
    if not payload["missing_local_resources"] and not payload["page_errors"]:
        return ""
    rendered = json.dumps(payload, ensure_ascii=False)
    return rendered if len(rendered) <= max_chars else rendered[: max_chars - 1] + "…"


def _local_console_errors(path: Optional[str], max_chars: int) -> str:
    return _render_global_error_payload(
        _local_console_error_payload(path), max_chars=max_chars
    )


def _stable_url(value: Any) -> str:
    """Remove run-directory identity while preserving the useful page location."""

    text = "" if value is None else str(value)
    if not text.startswith("file://"):
        return _stable_evidence_text(text)
    marker = "/workspace/"
    if marker in text:
        return "workspace/" + text.split(marker, 1)[1]
    return "file:///<local>/" + text.rsplit("/", 1)[-1]


def _stable_evidence_text(text: str) -> str:
    # Console and DOM evidence may embed a case-specific absolute file URL.
    # The model cannot access that path, so exposing it is both unnecessary and
    # a confound when otherwise identical policies use different output roots.
    stable = re.sub(r"file:///[^\s\"'<>]*?/workspace/", "workspace/", text)
    return re.sub(
        r"(https?://(?:127\.0\.0\.1|localhost)):\d+",
        r"\1:<port>",
        stable,
    )
