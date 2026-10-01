"""Minimal same-policy implementation–interaction–repair loop.

The loop starts at a benchmark agent's first complete runnable program.  It is
deliberately separate from official evaluators: public task inputs form the
obligations, Playwright only executes a frozen action list, and one frozen model
configuration performs obligation extraction, planning, evidence judgment, and
repair.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from ..backends import ModelBackend
from ..io import append_jsonl, read_json, safe_name, write_json
from ..schema import GenerationRequest
from .events import EventLog
from .interactive import InteractiveJudge, hash_program
from .planner import _parse_json_object
from .schema import ActionPlan, PlannedAction
from .tools import SafeFileToolExecutor, parse_revision


SELF_VERIFY_SCHEMA = "multimodalcode-same-policy-self-verify-1"
MECHANICAL_ACTION_TYPES = {
    "reset",
    "navigate",
    "click",
    "fill",
    "select",
    "hover",
    "press",
    "scroll",
    "wait",
    "screenshot",
}
MECHANICAL_ACTION_FIELDS = {
    "reset": {"type", "note"},
    "navigate": {"type", "url", "timeout_ms", "note"},
    "click": {"type", "selector", "role", "name", "text", "timeout_ms", "note"},
    "fill": {
        "type", "selector", "role", "name", "text", "value", "timeout_ms", "note"
    },
    "select": {
        "type", "selector", "role", "name", "text", "value", "timeout_ms", "note"
    },
    "hover": {"type", "selector", "role", "name", "text", "timeout_ms", "note"},
    "press": {
        "type", "selector", "role", "name", "text", "key", "timeout_ms", "note"
    },
    "scroll": {"type", "x", "y", "note"},
    "wait": {"type", "milliseconds", "note"},
    "screenshot": {"type", "note"},
}
PRIVATE_INPUT_KEYS = {
    "benchmark_checklist",
    "evaluator",
    "gold_patch",
    "ground_truth",
    "hidden_test",
    "hidden_tests",
    "official_evaluator",
    "oracle",
    "private_rubric",
    "reference_answer",
    "rubric",
    "test_patch",
}


class BudgetExhausted(RuntimeError):
    pass


class DuplicateRepairResponse(RuntimeError):
    """The primary repair call repeated the previous revision attempt verbatim."""

    def __init__(self, response_sha256: str):
        super().__init__("duplicate_repair_response")
        self.response_sha256 = response_sha256


@dataclass(frozen=True)
class SelfVerifyBudget:
    max_model_calls: int = 9
    max_browser_actions: int = 48
    max_revisions: int = 2
    max_schema_retries: int = 1
    max_checks: int = 6
    max_actions_per_check: int = 8
    max_evidence_images: int = 6
    max_patch_edits: int = 4
    max_patch_chars: int = 24_000
    browser_timeout_ms: int = 30_000

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if int(value) < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.max_model_calls < 3:
            raise ValueError("max_model_calls must allow obligations, planning, and judgment")
        if self.max_checks < 1 or self.max_actions_per_check < 1:
            raise ValueError("check and action budgets must be positive")


@dataclass(frozen=True)
class PublicSelfVerifyCase:
    case_id: str
    benchmark: str
    task: str
    program_path: str
    task_image_paths: List[str] = field(default_factory=list)
    modified_files: List[str] = field(default_factory=list)
    web_runtime: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, base_dir: Path) -> "PublicSelfVerifyCase":
        _reject_private_inputs(value)
        required = {"case_id", "benchmark", "task", "program_path"}
        missing = sorted(required - set(value))
        if missing:
            raise ValueError(f"Public self-verify case is missing fields: {missing}")
        program = _resolve(base_dir, str(value["program_path"]))
        images = [_resolve(base_dir, str(path)) for path in value.get("task_image_paths", [])]
        if not Path(program).exists():
            raise FileNotFoundError(program)
        for image in images:
            if not Path(image).is_file():
                raise FileNotFoundError(image)
        return cls(
            case_id=str(value["case_id"]),
            benchmark=str(value["benchmark"]),
            task=str(value["task"]),
            program_path=program,
            task_image_paths=images,
            modified_files=[str(path) for path in value.get("modified_files", [])],
            web_runtime=dict(value.get("web_runtime") or {}),
            metadata=dict(value.get("metadata") or {}),
        )

    def public_manifest(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "benchmark": self.benchmark,
            "task_sha256": _text_sha256(self.task),
            "program_path": self.program_path,
            "program_version": hash_program(self.program_path),
            "task_images": [
                {"path": path, "sha256": _file_sha256(Path(path))}
                for path in self.task_image_paths
            ],
            "modified_files": self.modified_files,
            "web_runtime": self.web_runtime,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class Obligation:
    obligation_id: str
    obligation: str


@dataclass(frozen=True)
class ObligationSet:
    obligations: List[Obligation]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, max_checks: int) -> "ObligationSet":
        if set(value) != {"obligations"}:
            raise ValueError("Obligation output must contain only 'obligations'")
        rows = value.get("obligations")
        if not isinstance(rows, list) or not rows:
            raise ValueError("At least one public-input obligation is required")
        if len(rows) > max_checks:
            raise ValueError(f"Obligation count exceeds budget {max_checks}")
        result: List[Obligation] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"obligation_id", "obligation"}:
                raise ValueError("Each obligation requires only obligation_id and obligation")
            obligation_id = str(row["obligation_id"]).strip()
            text = str(row["obligation"]).strip()
            if not obligation_id or not text:
                raise ValueError("Obligation id and text must be non-empty")
            result.append(Obligation(obligation_id, text))
        if len({item.obligation_id for item in result}) != len(result):
            raise ValueError("Obligation ids must be unique")
        if len({item.obligation for item in result}) != len(result):
            raise ValueError("Obligation texts must be unique")
        return cls(result)

    def to_dict(self) -> Dict[str, Any]:
        return {"obligations": [asdict(item) for item in self.obligations]}


@dataclass(frozen=True)
class InteractionCheck:
    check_id: str
    obligation: str
    actions: List[PlannedAction]
    expected_observation: str


@dataclass(frozen=True)
class InteractionPlan:
    checks: List[InteractionCheck]

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        obligations: ObligationSet,
        budget: SelfVerifyBudget,
        frozen_semantics: Optional["InteractionPlan"] = None,
    ) -> "InteractionPlan":
        if set(value) != {"checks"}:
            raise ValueError("Interaction plan must contain only 'checks'")
        rows = value.get("checks")
        if not isinstance(rows, list) or not rows:
            raise ValueError("Interaction plan requires at least one check")
        if len(rows) > budget.max_checks:
            raise ValueError("Interaction plan exceeds the check budget")
        public_obligations = {item.obligation for item in obligations.obligations}
        checks: List[InteractionCheck] = []
        for check_index, row in enumerate(rows):
            if not isinstance(row, dict) or set(row) != {
                "check_id",
                "obligation",
                "actions",
                "expected_observation",
            }:
                raise ValueError(
                    "Each check requires only check_id, obligation, actions, and expected_observation"
                )
            check_id = str(row["check_id"]).strip()
            obligation = str(row["obligation"]).strip()
            expected = str(row["expected_observation"]).strip()
            raw_actions = row["actions"]
            if obligation not in public_obligations:
                raise ValueError("A check obligation must exactly match a public-input obligation")
            if not check_id or not expected or not isinstance(raw_actions, list) or not raw_actions:
                raise ValueError("Check id, expected observation, and actions are required")
            actions: List[PlannedAction] = []
            for raw_action in raw_actions:
                if not isinstance(raw_action, dict):
                    raise ValueError("Every planned action must be an object")
                if raw_action.get("checklist_id") is not None:
                    raise ValueError("The model must not emit internal checklist_id fields")
                raw_type = str(raw_action.get("type", "")).strip()
                allowed_fields = MECHANICAL_ACTION_FIELDS.get(raw_type)
                if allowed_fields is None:
                    raise ValueError(
                        f"Same-policy plans allow only mechanical actions, not {raw_type!r}"
                    )
                unknown_fields = sorted(set(raw_action) - allowed_fields)
                if unknown_fields:
                    raise ValueError(
                        f"Action {raw_type!r} has unsupported fields: {unknown_fields}"
                    )
                action = PlannedAction.from_dict(dict(raw_action))
                if action.type not in MECHANICAL_ACTION_TYPES:
                    raise ValueError(
                        f"Same-policy plans allow only mechanical actions, not {action.type!r}"
                    )
                _validate_mechanical_action_contract(action)
                if action.timeout_ms is not None and not (
                    1 <= action.timeout_ms <= budget.browser_timeout_ms
                ):
                    raise ValueError(
                        f"Action timeout_ms must be between 1 and {budget.browser_timeout_ms}"
                    )
                if action.type == "wait" and not (
                    0 <= action.milliseconds <= budget.browser_timeout_ms
                ):
                    raise ValueError(
                        f"Wait milliseconds must be between 0 and {budget.browser_timeout_ms}"
                    )
                actions.append(action)
            inject_reset = check_index > 0 and actions[0].type != "reset"
            if len(actions) + int(inject_reset) > budget.max_actions_per_check:
                raise ValueError(f"Check {check_id} exceeds its action budget")
            if inject_reset:
                # Reset is a deterministic isolation boundary, not a semantic
                # planning decision. The normalized frozen plan records it and
                # the action still consumes the ordinary browser budget.
                actions.insert(0, PlannedAction.from_dict({"type": "reset"}))
            checks.append(InteractionCheck(check_id, obligation, actions, expected))
        if len({check.check_id for check in checks}) != len(checks):
            raise ValueError("Check ids must be unique")
        planned_obligations = [check.obligation for check in checks]
        if (
            len(planned_obligations) != len(public_obligations)
            or set(planned_obligations) != public_obligations
        ):
            raise ValueError(
                "Interaction plan must cover every frozen obligation exactly once"
            )
        action_count = sum(len(check.actions) for check in checks)
        replay_divisor = 1 + budget.max_revisions
        max_initial_plan_actions = max(
            0, (budget.max_browser_actions - 1) // replay_divisor
        )
        if action_count > max_initial_plan_actions:
            raise ValueError(
                "Interaction plan exceeds the total action budget reserved for "
                "initial execution and every allowed candidate replay "
                f"({max_initial_plan_actions})"
            )
        if frozen_semantics is not None:
            old = [
                (check.check_id, check.obligation, check.expected_observation)
                for check in frozen_semantics.checks
            ]
            new = [
                (check.check_id, check.obligation, check.expected_observation)
                for check in checks
            ]
            if new != old:
                raise ValueError("A selector replan cannot change frozen check semantics")
        return cls(checks)

    @property
    def action_count(self) -> int:
        return sum(len(check.actions) for check in self.checks)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checks": [
                {
                    "check_id": check.check_id,
                    "obligation": check.obligation,
                    "actions": [
                        {
                            key: value
                            for key, value in action.to_dict().items()
                            if value not in (None, 0, "contains") and key != "checklist_id"
                        }
                        for action in check.actions
                    ],
                    "expected_observation": check.expected_observation,
                }
                for check in self.checks
            ]
        }

    def to_executor_plan(self, *, model_name: str, web_runtime: Mapping[str, Any]) -> ActionPlan:
        actions: List[Dict[str, Any]] = []
        checklist: List[Dict[str, Any]] = []
        for check in self.checks:
            checklist.append(
                {
                    "id": check.check_id,
                    "description": check.obligation,
                    "expected": check.expected_observation,
                    "source": "public_task_same_policy",
                }
            )
            for action in check.actions:
                row = action.to_dict()
                row["checklist_id"] = check.check_id
                actions.append(row)
        return ActionPlan.from_dict(
            {
                "checklist": checklist,
                "actions": actions,
                "planner": "same-policy-one-shot",
                "plan_version": 1,
                "metadata": {
                    "planner_model": model_name,
                    "mechanical_execution_only": True,
                    "web_runtime": dict(web_runtime),
                },
            }
        )


@dataclass(frozen=True)
class CheckJudgment:
    check_id: str
    status: str
    first_failure_step: Optional[int]
    evidence_refs: List[str]


@dataclass(frozen=True)
class PolicyJudgment:
    checks: List[CheckJudgment]

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        plan: InteractionPlan,
        allowed_evidence_refs: Iterable[str],
    ) -> "PolicyJudgment":
        if set(value) != {"checks"}:
            raise ValueError("Judgment must contain only 'checks'")
        rows = value.get("checks")
        if not isinstance(rows, list):
            raise ValueError("Judgment checks must be a list")
        action_steps = {
            check.check_id: set(range(len(check.actions))) for check in plan.checks
        }
        allowed = set(allowed_evidence_refs)
        result: List[CheckJudgment] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {
                "check_id",
                "status",
                "first_failure_step",
                "evidence_refs",
            }:
                raise ValueError(
                    "Each judgment requires only check_id, status, first_failure_step, evidence_refs"
                )
            check_id = str(row["check_id"])
            status = str(row["status"])
            step = row["first_failure_step"]
            refs = [str(ref) for ref in row["evidence_refs"]]
            if status not in {"pass", "fail"}:
                raise ValueError("Judgment status must be pass or fail")
            if status == "pass" and step is not None:
                raise ValueError("A passing check cannot have a failure step")
            if status == "fail" and (not isinstance(step, int) or step not in action_steps.get(check_id, set())):
                raise ValueError("A failing check requires a valid first failure step")
            if not refs or not set(refs).issubset(allowed):
                raise ValueError("Every judgment must cite only supplied evidence refs")
            result.append(CheckJudgment(check_id, status, step, refs))
        expected_ids = [check.check_id for check in plan.checks]
        if [row.check_id for row in result] != expected_ids:
            raise ValueError("Judgment must cover every check once and preserve plan order")
        return cls(result)

    def to_dict(self) -> Dict[str, Any]:
        return {"checks": [asdict(item) for item in self.checks]}

    def statuses(self) -> Dict[str, str]:
        return {item.check_id: item.status for item in self.checks}

    def all_pass(self) -> bool:
        return all(item.status == "pass" for item in self.checks)

    def failure_signature(self) -> str:
        payload = [
            [item.check_id, item.status, item.first_failure_step]
            for item in self.checks
            if item.status == "fail"
        ]
        return _canonical_sha256(payload)


class FrozenPolicy:
    """One immutable model configuration shared by every semantic stage."""

    def __init__(
        self,
        *,
        backend: ModelBackend,
        model_name: str,
        artifact_dir: Path,
        budget: SelfVerifyBudget,
        max_tokens: int,
        temperature: float,
        seed: Optional[int],
    ):
        self.backend = backend
        self.model_name = str(model_name)
        self.artifact_dir = artifact_dir
        self.budget = budget
        self.max_tokens = int(max_tokens)
        self.temperature = float(temperature)
        self.seed = seed
        self.calls = 0
        self.input_tokens_estimated = 0
        self.output_tokens_estimated = 0
        self.seconds = 0.0

    def call(
        self,
        *,
        stage: str,
        prompt: str,
        image_paths: Sequence[str],
        system_prompt: str,
    ) -> str:
        if self.calls >= self.budget.max_model_calls:
            raise BudgetExhausted("model_call_budget_exhausted")
        request = GenerationRequest(
            prompt=prompt,
            image_paths=list(image_paths),
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            seed=self.seed,
            system_prompt=system_prompt,
        )
        ordinal = self.calls
        request_row = {
            "stage": stage,
            "ordinal": ordinal,
            "model": self.model_name,
            "backend_class": type(self.backend).__name__,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "seed": self.seed,
            "system_prompt": system_prompt,
            "prompt": prompt,
            "images": [
                {"path": path, "sha256": _file_sha256(Path(path))}
                for path in image_paths
            ],
        }
        request_row["request_sha256"] = _canonical_sha256(request_row)
        write_json(self.artifact_dir / "requests" / f"{ordinal:02d}-{stage}.json", request_row)
        started = time.monotonic()
        response = self.backend.generate(request)
        elapsed = time.monotonic() - started
        (self.artifact_dir / "responses").mkdir(parents=True, exist_ok=True)
        response_path = self.artifact_dir / "responses" / f"{ordinal:02d}-{stage}.txt"
        response_path.write_text(response, encoding="utf-8")
        self.calls += 1
        self.seconds += elapsed
        self.input_tokens_estimated += _estimated_tokens(system_prompt + "\n" + prompt)
        self.output_tokens_estimated += _estimated_tokens(response)
        append_jsonl(
            self.artifact_dir / "model_calls.jsonl",
            {
                "stage": stage,
                "ordinal": ordinal,
                "model": self.model_name,
                "request_sha256": request_row["request_sha256"],
                "response_sha256": _text_sha256(response),
                "input_tokens_estimated": _estimated_tokens(system_prompt + "\n" + prompt),
                "output_tokens_estimated": _estimated_tokens(response),
                "image_count": len(image_paths),
                "seconds": elapsed,
            },
        )
        return response

    def structured_call(
        self,
        *,
        stage: str,
        prompt: str,
        image_paths: Sequence[str],
        system_prompt: str,
        parser,
    ):
        error = ""
        previous_response_sha256: Optional[str] = None
        for attempt in range(self.budget.max_schema_retries + 1):
            retry_prompt = prompt
            if error:
                retry_prompt += (
                    "\n\nYour previous output violated the frozen JSON contract: "
                    f"{error}. Return a corrected JSON object only."
                )
            response = self.call(
                stage=stage if attempt == 0 else f"{stage}-schema-retry-{attempt}",
                prompt=retry_prompt,
                image_paths=image_paths,
                system_prompt=system_prompt,
            )
            response_sha256 = _text_sha256(response)
            if (
                stage == "action-plan"
                and previous_response_sha256 == response_sha256
            ):
                raise ValueError(
                    "Action plan repeated the same invalid response twice consecutively"
                )
            previous_response_sha256 = response_sha256
            try:
                return parser(_parse_json_object(response))
            except (TypeError, ValueError) as exc:
                error = f"{type(exc).__name__}: {exc}"
        raise ValueError(f"Structured stage {stage} failed after retries: {error}")

    def costs(self) -> Dict[str, Any]:
        return {
            "model_calls": self.calls,
            "input_tokens_estimated": self.input_tokens_estimated,
            "output_tokens_estimated": self.output_tokens_estimated,
            "model_seconds": self.seconds,
            "token_accounting": "character_count_divided_by_four; API usage unavailable",
        }


class SamePolicySelfVerifyLoop:
    def __init__(
        self,
        *,
        backend: ModelBackend,
        model_name: str,
        run_dir: str | Path,
        budget: SelfVerifyBudget = SelfVerifyBudget(),
        max_tokens: int = 4096,
        temperature: float = 0.0,
        seed: Optional[int] = 0,
        record_video: bool = True,
        interactive_judge: Optional[Any] = None,
    ):
        self.run_dir = Path(run_dir).resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.budget = budget
        self.record_video = bool(record_video)
        self.event_log = EventLog(self.run_dir / "events.jsonl")
        self.judge = interactive_judge or InteractiveJudge(
            self.run_dir / "browser", event_log=self.event_log
        )
        self.policy = FrozenPolicy(
            backend=backend,
            model_name=model_name,
            artifact_dir=self.run_dir,
            budget=budget,
            max_tokens=max_tokens,
            temperature=temperature,
            seed=seed,
        )
        self.browser_actions = 0

    def run(self, case: PublicSelfVerifyCase) -> Dict[str, Any]:
        case_dir = self.run_dir / "cases" / safe_name(case.case_id)
        state_path = case_dir / "result.json"
        if state_path.exists():
            existing = read_json(state_path)
            if existing.get("status") == "complete":
                return existing
            raise RuntimeError("Refusing to overwrite an incomplete same-policy case run")
        case_dir.mkdir(parents=True, exist_ok=False)
        started = time.monotonic()
        tool = SafeFileToolExecutor.prepare(
            case.program_path,
            case_dir / "workspace",
            case_dir / "checkpoints",
        )
        initial_version = hash_program(tool.workspace)
        tool.checkpoint(initial_version)
        frozen = {
            "schema": SELF_VERIFY_SCHEMA,
            "enabled": True,
            "same_policy": {
                "model": self.policy.model_name,
                "backend_class": type(self.policy.backend).__name__,
                "max_tokens": self.policy.max_tokens,
                "temperature": self.policy.temperature,
                "seed": self.policy.seed,
            },
            "budget": asdict(self.budget),
            "record_video": self.record_video,
            "public_case": case.public_manifest(),
            "official_evaluator_visible": False,
        }
        write_json(case_dir / "config.json", frozen)
        versions: List[Dict[str, Any]] = [
            {
                "version": initial_version,
                "kind": "first_complete_program",
                "accepted": True,
                "manifest": _program_manifest(tool.workspace),
            }
        ]
        plans: List[Dict[str, Any]] = []
        judgments: List[Dict[str, Any]] = []
        patches: List[Dict[str, Any]] = []
        replays: List[Dict[str, Any]] = []
        termination_reason = "unknown"
        accepted_version = initial_version
        accepted_judgment: Optional[PolicyJudgment] = None
        previous_patch_digest: Optional[str] = None
        previous_primary_repair_response: Optional[str] = None
        previous_failure_signature: Optional[str] = None

        try:
            obligations = self._obligations(case)
            write_json(case_dir / "obligations.json", obligations.to_dict())
            inspection = self._inspect_initial(case, tool, purpose="initial-plan-state")
            write_json(case_dir / "initial_inspection.json", inspection)
            plan = self._plan(case, obligations, inspection)
            plan_digest = _canonical_sha256(plan.to_dict())
            plans.append({"plan_sha256": plan_digest, "kind": "initial", **plan.to_dict()})
            write_json(case_dir / "plans" / "plan-00.json", plans[-1])

            execution = self._execute(case, tool, plan, purpose="initial-verification")
            replays.append(_execution_record(execution, plan_digest, "initial"))
            judgment, evidence_index = self._judge(case, obligations, plan, execution)
            accepted_judgment = judgment
            judgments.append(
                {
                    "version": initial_version,
                    "kind": "initial",
                    "evidence_index": evidence_index,
                    **judgment.to_dict(),
                }
            )
            write_json(case_dir / "judgments" / "judgment-00.json", judgments[-1])
            previous_failure_signature = judgment.failure_signature()
            if judgment.all_pass():
                termination_reason = "all_checks_passed_initial_version"
            else:
                for revision_index in range(1, self.budget.max_revisions + 1):
                    try:
                        revision, primary_response_sha256 = self._repair(
                            case=case,
                            obligations=obligations,
                            plan=plan,
                            judgment=accepted_judgment,
                            execution=execution,
                            tool=tool,
                            previous_primary_response_sha256=(
                                previous_primary_repair_response
                            ),
                        )
                    except DuplicateRepairResponse as exc:
                        patches.append(
                            {
                                "revision": revision_index,
                                "base_version": accepted_version,
                                "admitted": False,
                                "response_sha256": exc.response_sha256,
                                "rejection": (
                                    "duplicate_repair_response_twice_consecutively"
                                ),
                            }
                        )
                        termination_reason = "duplicate_repair_response"
                        break
                    previous_primary_repair_response = primary_response_sha256
                    patch_digest = _canonical_sha256(revision)
                    patch_row = {
                        "revision": revision_index,
                        "base_version": accepted_version,
                        "patch_sha256": patch_digest,
                        "revision_contract": revision,
                    }
                    if previous_patch_digest == patch_digest:
                        patch_row["admitted"] = False
                        patch_row["rejection"] = "duplicate_patch_twice_consecutively"
                        patches.append(patch_row)
                        termination_reason = "duplicate_patch"
                        break
                    previous_patch_digest = patch_digest
                    if revision["decision"] in {"keep", "stop", "rollback"}:
                        patch_row["admitted"] = False
                        patches.append(patch_row)
                        termination_reason = f"policy_{revision['decision']}"
                        break
                    before_manifest = _program_manifest(tool.workspace)
                    written, rejected = tool.apply_revision_with_admission(
                        files=revision["files"], edits=revision["edits"]
                    )
                    candidate_version = hash_program(tool.workspace)
                    if candidate_version == accepted_version:
                        raise ValueError("Admitted patch did not create a new program version")
                    patch_row.update(
                        {
                            "admitted": True,
                            "written_files": written,
                            "rejected_existing_file_payloads": rejected,
                            "candidate_version": candidate_version,
                        }
                    )
                    patches.append(patch_row)
                    tool.checkpoint(candidate_version)
                    versions.append(
                        {
                            "version": candidate_version,
                            "kind": "candidate",
                            "accepted": None,
                            "base_version": accepted_version,
                            "changed_files": _changed_files(
                                before_manifest, _program_manifest(tool.workspace)
                            ),
                            "manifest": _program_manifest(tool.workspace),
                        }
                    )

                    candidate_execution = self._execute(
                        case,
                        tool,
                        plan,
                        purpose=f"revision-{revision_index}-failed-and-pass-replay",
                    )
                    replays.append(_execution_record(candidate_execution, plan_digest, "candidate"))
                    candidate_judgment, evidence_index = self._judge(
                        case, obligations, plan, candidate_execution
                    )
                    judgments.append(
                        {
                            "version": candidate_version,
                            "kind": "candidate",
                            "evidence_index": evidence_index,
                            **candidate_judgment.to_dict(),
                        }
                    )
                    write_json(
                        case_dir / "judgments" / f"judgment-{revision_index:02d}.json",
                        judgments[-1],
                    )
                    acceptance = _acceptance(accepted_judgment, candidate_judgment)
                    patches[-1]["acceptance"] = acceptance
                    versions[-1]["accepted"] = acceptance["accepted"]
                    if acceptance["accepted"]:
                        accepted_version = candidate_version
                        accepted_judgment = candidate_judgment
                        execution = candidate_execution
                    else:
                        tool.restore(accepted_version)
                    failure_signature = candidate_judgment.failure_signature()
                    if candidate_judgment.all_pass() and acceptance["accepted"]:
                        termination_reason = "all_checks_passed_after_repair"
                        break
                    if failure_signature == previous_failure_signature:
                        termination_reason = "duplicate_failure_signature"
                        break
                    previous_failure_signature = failure_signature
                else:
                    termination_reason = "revision_budget_exhausted"
        except BudgetExhausted as exc:
            termination_reason = str(exc)
        except Exception as exc:
            restore_error = ""
            if hash_program(tool.workspace) != accepted_version:
                try:
                    tool.restore(accepted_version)
                except Exception as restore_exc:  # preserve both failure causes
                    restore_error = (
                        f"; restore_failed={type(restore_exc).__name__}: {restore_exc}"
                    )
            result = self._result(
                case=case,
                status="error",
                termination_reason=f"{type(exc).__name__}: {exc}{restore_error}",
                initial_version=initial_version,
                accepted_version=accepted_version,
                workspace=tool.workspace,
                versions=versions,
                plans=plans,
                judgments=judgments,
                patches=patches,
                replays=replays,
                wall_seconds=time.monotonic() - started,
            )
            write_json(state_path, result)
            raise

        if hash_program(tool.workspace) != accepted_version:
            tool.restore(accepted_version)
        result = self._result(
            case=case,
            status="complete",
            termination_reason=termination_reason,
            initial_version=initial_version,
            accepted_version=accepted_version,
            workspace=tool.workspace,
            versions=versions,
            plans=plans,
            judgments=judgments,
            patches=patches,
            replays=replays,
            wall_seconds=time.monotonic() - started,
        )
        write_json(state_path, result)
        return result

    def _obligations(self, case: PublicSelfVerifyCase) -> ObligationSet:
        prompt = f"""Public user task:\n{case.task}\n\nExtract between 1 and {self.budget.max_checks} distinct externally observable behavioral obligations. Use only the public task and attached public reference images. Do not infer behavior from generated code and do not produce selectors, browser actions, scores, priorities, or confidence. Do not repeat equivalent obligation text. Return JSON exactly as {{\"obligations\":[{{\"obligation_id\":\"O1\",\"obligation\":\"...\"}}]}}."""
        return self.policy.structured_call(
            stage="obligations",
            prompt=prompt,
            image_paths=_bounded_images(
                case.task_image_paths, self.budget.max_evidence_images
            ),
            system_prompt=(
                "You are the same multimodal coding policy used for every stage. "
                "Extract public behavioral obligations and return JSON only."
            ),
            parser=lambda value: ObligationSet.from_dict(
                value, max_checks=self.budget.max_checks
            ),
        )

    def _inspect_initial(
        self,
        case: PublicSelfVerifyCase,
        tool: SafeFileToolExecutor,
        *,
        purpose: str,
    ) -> Dict[str, Any]:
        plan = ActionPlan.from_dict(
            {
                "checklist": [
                    {
                        "id": "initial_state",
                        "description": "Capture public initial browser state",
                        "expected": "Initial state captured",
                        "source": "harness",
                    }
                ],
                "actions": [{"type": "screenshot", "checklist_id": "initial_state"}],
                "planner": "mechanical-bootstrap",
                "metadata": {"web_runtime": case.web_runtime},
            }
        )
        self._charge_browser_actions(1)
        return self.judge.execute(
            case_id=case.case_id,
            program_path=tool.workspace,
            plan=plan,
            purpose=purpose,
            timeout_ms=self.budget.browser_timeout_ms,
            record_video=False,
        )

    def _plan(
        self,
        case: PublicSelfVerifyCase,
        obligations: ObligationSet,
        inspection: Mapping[str, Any],
    ) -> InteractionPlan:
        state, evidence_text, evidence_images = _initial_state_prompt(inspection)
        prompt = f"""Public task:\n{case.task}\n\nFrozen public obligations:\n{json.dumps(obligations.to_dict(), ensure_ascii=False, indent=2)}\n\nInitial browser state:\n{json.dumps(state, ensure_ascii=False, indent=2)}\n\nInitial DOM/accessibility/console evidence:\n{evidence_text}\n\nGenerate all checks in one call, with exactly one check for every frozen obligation and no omitted or duplicate obligation. Playwright will execute them mechanically. Do not use assertions: you will judge raw evidence in a later call. Each independent check after the first will receive a deterministic reset if omitted. Action contracts (no other keys): reset {{\"type\":\"reset\"}}; navigate {{\"type\":\"navigate\",\"url\":\"/...\"}}; click/hover {{\"type\":\"click\", one locator from \"selector\", \"role\" with optional \"name\", or \"text\"}}; fill/select add \"value\"; press adds \"key\" and may omit a locator; scroll {{\"type\":\"scroll\",\"x\":0,\"y\":800}}; wait {{\"type\":\"wait\",\"milliseconds\":500}}; screenshot {{\"type\":\"screenshot\"}}. Locator actions may add \"timeout_ms\" from 1 through {self.budget.browser_timeout_ms}; waits must not exceed {self.budget.browser_timeout_ms} ms; any action may add \"note\". Never emit \"name\" without \"role\", or use description, direction, amount, timeout, or wait_ms keys. Use only this outer JSON shape: {{\"checks\":[{{\"check_id\":\"C1\",\"obligation\":\"exact text from frozen obligations\",\"actions\":[{{\"type\":\"click\",\"role\":\"button\",\"name\":\"...\"}}],\"expected_observation\":\"...\"}}]}}."""
        prompt += (
            f"\n\nHard planning budgets: return at most {self.budget.max_checks} checks "
            f"and at most {self.budget.max_actions_per_check} actions per normalized check. "
            "For every check after the first, either emit reset explicitly or leave one "
            f"of the {self.budget.max_actions_per_check} action slots free for the "
            "deterministically injected reset. "
            f"Across all normalized checks, use at most {max(0, (self.budget.max_browser_actions - 1) // (1 + self.budget.max_revisions))} total planned actions so the global browser budget retains room for initial execution and every allowed candidate replay."
        )
        images = _separately_bounded_images(
            evidence_images,
            case.task_image_paths,
            self.budget.max_evidence_images,
        )
        return self.policy.structured_call(
            stage="action-plan",
            prompt=prompt,
            image_paths=images,
            system_prompt=(
                "You are the same multimodal coding policy. Plan one complete finite browser "
                "check sequence after deployment. Return JSON only and do not write code."
            ),
            parser=lambda value: InteractionPlan.from_dict(
                value, obligations=obligations, budget=self.budget
            ),
        )

    def _execute(
        self,
        case: PublicSelfVerifyCase,
        tool: SafeFileToolExecutor,
        plan: InteractionPlan,
        *,
        purpose: str,
    ) -> Dict[str, Any]:
        self._charge_browser_actions(plan.action_count)
        return self.judge.execute(
            case_id=case.case_id,
            program_path=tool.workspace,
            plan=plan.to_executor_plan(
                model_name=self.policy.model_name, web_runtime=case.web_runtime
            ),
            purpose=purpose,
            timeout_ms=self.budget.browser_timeout_ms,
            record_video=self.record_video,
        )

    def _judge(
        self,
        case: PublicSelfVerifyCase,
        obligations: ObligationSet,
        plan: InteractionPlan,
        execution: Mapping[str, Any],
    ) -> tuple[PolicyJudgment, Dict[str, Any]]:
        evidence_text, images, evidence_index = _execution_evidence(
            plan, execution, max_images=self.budget.max_evidence_images
        )
        prompt = f"""Public task:\n{case.task}\n\nFrozen obligations:\n{json.dumps(obligations.to_dict(), ensure_ascii=False, indent=2)}\n\nFrozen checks:\n{json.dumps(plan.to_dict(), ensure_ascii=False, indent=2)}\n\nMechanical execution evidence (executor status means only whether an action ran; it is not a semantic verdict):\n{evidence_text}\n\nJudge each check from the supplied execution evidence. Return every check in plan order. A failure must identify the first local step that supports failure and cite supplied evidence refs. A pass must use null for first_failure_step. Return only: {{\"checks\":[{{\"check_id\":\"C1\",\"status\":\"pass|fail\",\"first_failure_step\":null,\"evidence_refs\":[\"...\"]}}]}}."""
        judgment = self.policy.structured_call(
            stage="evidence-judgment",
            prompt=prompt,
            image_paths=_separately_bounded_images(
                images,
                case.task_image_paths,
                self.budget.max_evidence_images,
            ),
            system_prompt=(
                "You are the same multimodal coding policy. Judge raw browser evidence against "
                "frozen public obligations. Return JSON only; do not change the checks."
            ),
            parser=lambda value: PolicyJudgment.from_dict(
                value,
                plan=plan,
                allowed_evidence_refs=evidence_index,
            ),
        )
        return judgment, evidence_index

    def _repair(
        self,
        *,
        case: PublicSelfVerifyCase,
        obligations: ObligationSet,
        plan: InteractionPlan,
        judgment: PolicyJudgment,
        execution: Mapping[str, Any],
        tool: SafeFileToolExecutor,
        previous_primary_response_sha256: Optional[str],
    ) -> tuple[Dict[str, Any], str]:
        evidence_text, images, evidence_index = _execution_evidence(
            plan, execution, max_images=self.budget.max_evidence_images
        )
        runtime_paths = [
            str(row.get("path"))
            for row in (execution.get("runtime_source_context") or {}).get("files", [])
            if row.get("path")
        ]
        selected = []
        # Browser-observed source is the strongest mechanical localization
        # signal and must precede broad generation manifests (which may include
        # large lock files).  The latter remains a deterministic fallback.
        for path in runtime_paths + list(case.modified_files):
            if path not in selected:
                selected.append(path)
        source, source_manifest = tool.render_program_context(
            relative_paths=selected or None,
            max_bytes=200_000,
        )
        repair_dir = self.run_dir / "cases" / safe_name(case.case_id) / "repair_contexts"
        repair_dir.mkdir(parents=True, exist_ok=True)
        write_json(
            repair_dir / f"context-{self.policy.calls:02d}.json",
            {
                "code_version": hash_program(tool.workspace),
                "selected_paths": selected,
                "source_manifest": source_manifest,
                "evidence_index": evidence_index,
            },
        )
        prompt = f"""Public task:\n{case.task}\n\nFrozen obligations:\n{json.dumps(obligations.to_dict(), ensure_ascii=False, indent=2)}\n\nFrozen checks:\n{json.dumps(plan.to_dict(), ensure_ascii=False, indent=2)}\n\nSame-policy judgment:\n{json.dumps(judgment.to_dict(), ensure_ascii=False, indent=2)}\n\nRelevant execution evidence:\n{evidence_text}\n\nRelevant current source files:\n{source}\n\nReturn one localized revision JSON. Existing files must use exact edits: {{\"decision\":\"patch\",\"edits\":[{{\"path\":\"relative/path\",\"old\":\"exact unique text\",\"new\":\"replacement\"}}],\"files\":{{}},\"reason\":\"...\"}}. The files object may only create new files. You may return keep or stop. Do not modify tests, evidence, checks, or evaluator artifacts; preserve checks that already passed. Hard revision budgets: at most {self.budget.max_patch_edits} edits and at most {self.budget.max_patch_chars} total characters across old/new edit text and new-file contents. Do not reformat unrelated code or include unchanged edits."""
        error = ""
        primary_response_sha256 = ""
        for attempt in range(self.budget.max_schema_retries + 1):
            retry_prompt = prompt + (
                "" if not error else f"\n\nPrevious revision contract error: {error}. Correct it."
            )
            response = self.policy.call(
                stage="repair" if attempt == 0 else f"repair-contract-retry-{attempt}",
                prompt=retry_prompt,
                image_paths=_separately_bounded_images(
                    images,
                    case.task_image_paths,
                    self.budget.max_evidence_images,
                ),
                system_prompt=(
                    "You are the same multimodal coding policy. Produce the smallest typed code "
                    "repair grounded in cited browser evidence. Return JSON only."
                ),
            )
            response_sha256 = _text_sha256(response)
            if attempt == 0:
                primary_response_sha256 = response_sha256
                if response_sha256 == previous_primary_response_sha256:
                    raise DuplicateRepairResponse(response_sha256)
            try:
                revision = parse_revision(
                    response,
                    max_edits=self.budget.max_patch_edits,
                    max_patch_chars=self.budget.max_patch_chars,
                )
                if revision["decision"] == "patch":
                    # A syntactically valid exact-edit contract may still be
                    # inapplicable to the current accepted code version.  Test
                    # admission without mutation so the same bounded contract
                    # retry can correct the model-authored edit.  The runner
                    # applies the returned revision only after duplicate checks.
                    tool.validate_revision_with_admission(
                        files=revision["files"], edits=revision["edits"]
                    )
                return revision, primary_response_sha256
            except (FileNotFoundError, ValueError) as exc:
                error = f"{type(exc).__name__}: {exc}"
        raise ValueError(f"Repair contract failed after retries: {error}")

    def _charge_browser_actions(self, count: int) -> None:
        if self.browser_actions + count > self.budget.max_browser_actions:
            raise BudgetExhausted("browser_action_budget_exhausted")
        self.browser_actions += count

    def _result(self, **kwargs: Any) -> Dict[str, Any]:
        workspace = Path(kwargs.pop("workspace"))
        case = kwargs.pop("case")
        try:
            workspace_relative = workspace.relative_to(self.run_dir).as_posix()
        except ValueError:
            workspace_relative = None
        return {
            "schema": SELF_VERIFY_SCHEMA,
            **kwargs,
            "case_id": case.case_id,
            "benchmark": case.benchmark,
            "model": self.policy.model_name,
            "same_policy_stages": sorted(
                {
                    row["stage"].split("-schema-retry", 1)[0]
                    for row in _jsonl_rows(self.run_dir / "model_calls.jsonl")
                }
            ),
            "official_evaluator_visible": False,
            "browser_actions": self.browser_actions,
            "cost": self.policy.costs(),
            "final_workspace": str(workspace),
            "final_workspace_relative": workspace_relative,
            "final_program_version": hash_program(workspace),
            "final_program_manifest": _program_manifest(workspace),
        }


def _reject_private_inputs(value: Mapping[str, Any], path: str = "case") -> None:
    for key, item in value.items():
        normalized = str(key).lower()
        if normalized in PRIVATE_INPUT_KEYS:
            raise ValueError(f"Private/evaluator field is forbidden in policy input: {path}.{key}")
        if isinstance(item, Mapping):
            _reject_private_inputs(item, f"{path}.{key}")
        elif isinstance(item, list):
            for index, child in enumerate(item):
                if isinstance(child, Mapping):
                    _reject_private_inputs(child, f"{path}.{key}[{index}]")


def _validate_mechanical_action_contract(action: PlannedAction) -> None:
    locator_families = [
        bool(action.selector),
        bool(action.role),
        bool(action.text),
    ]
    if action.name and not action.role:
        raise ValueError("A locator name requires a role")
    if action.type in {"click", "fill", "select", "hover"}:
        if sum(locator_families) != 1:
            raise ValueError(
                f"Action {action.type!r} requires exactly one locator: selector, "
                "role with optional name, or text"
            )
    elif action.type == "press" and sum(locator_families) > 1:
        raise ValueError("A press action may use at most one locator")
    if action.type in {"fill", "select"} and action.value is None:
        raise ValueError(f"Action {action.type!r} requires value")
    if action.type == "press" and not action.key:
        raise ValueError("Action 'press' requires key")
    if action.type == "navigate" and not action.url:
        raise ValueError("Action 'navigate' requires url")


def _resolve(base: Path, value: str) -> str:
    path = Path(value)
    return str((base / path).resolve() if not path.is_absolute() else path.resolve())


def _estimated_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _read_excerpt(path: Any, max_chars: int) -> str:
    if not path or not Path(str(path)).is_file():
        return ""
    text = Path(str(path)).read_text(encoding="utf-8", errors="replace")
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def _initial_state_prompt(
    inspection: Mapping[str, Any],
) -> tuple[Dict[str, Any], str, List[str]]:
    state = dict(inspection.get("initial_state") or {})
    action = (inspection.get("actions") or [{}])[0]
    evidence = dict(action.get("evidence") or state.get("evidence") or {})
    text = json.dumps(
        {
            "dom": _read_excerpt(evidence.get("dom"), 12_000),
            "accessibility": _read_excerpt(evidence.get("accessibility"), 8_000),
            "console": _read_excerpt(evidence.get("console_delta"), 4_000),
        },
        ensure_ascii=False,
    )
    images = [str(evidence["screenshot"])] if evidence.get("screenshot") else []
    public_state = {
        key: state.get(key)
        for key in ("url", "title", "fingerprint", "browser_state")
    }
    return public_state, text, images


def _execution_evidence(
    plan: InteractionPlan,
    execution: Mapping[str, Any],
    *,
    max_images: int,
) -> tuple[str, List[str], Dict[str, Any]]:
    plan_by_id = {check.check_id: check for check in plan.checks}
    imported_by_source = {
        str(row.get("source")): row
        for row in execution.get("evidence_manifest", [])
        if isinstance(row, dict) and row.get("source")
    }
    local_steps = {check.check_id: 0 for check in plan.checks}
    text_rows: List[Dict[str, Any]] = []
    images: List[str] = []
    index: Dict[str, Any] = {}
    for action in execution.get("actions", []):
        check_id = str(action.get("checklist_id"))
        if check_id not in plan_by_id:
            continue
        step = local_steps[check_id]
        local_steps[check_id] += 1
        evidence = dict(action.get("evidence") or {})
        refs: List[str] = []
        for kind, path in evidence.items():
            if not path or not Path(str(path)).is_file():
                continue
            ref = f"{check_id}:step:{step}:{kind}"
            refs.append(ref)
            imported = imported_by_source.get(str(Path(str(path)).resolve()))
            index[ref] = {
                "path": str(path),
                "relative_object_path": (
                    imported.get("relative_path") if imported else None
                ),
                "object_sha256": (
                    imported.get("evidence_id") if imported else None
                ),
                "sha256": _file_sha256(Path(str(path))),
                "kind": kind,
                "check_id": check_id,
                "step": step,
            }
        for image_key in ("screenshot", "visual_before", "visual_after", "visual_actual"):
            path = evidence.get(image_key)
            if path and Path(str(path)).is_file() and str(path) not in images:
                images.append(str(path))
        text_rows.append(
            {
                "check_id": check_id,
                "step": step,
                "action": plan_by_id[check_id].actions[step].to_dict(),
                "execution_status": action.get("status"),
                "execution_error": action.get("error"),
                "observed": action.get("observed"),
                "url": action.get("url"),
                "title": action.get("title"),
                "changed": action.get("changed"),
                "dom_excerpt": _read_excerpt(evidence.get("dom"), 3_000),
                "accessibility_excerpt": _read_excerpt(
                    evidence.get("accessibility"), 2_000
                ),
                "console_excerpt": _read_excerpt(evidence.get("console_delta"), 2_000),
                "evidence_refs": refs,
            }
        )
    if not index:
        raise ValueError("Mechanical execution produced no citable evidence")
    return (
        json.dumps(text_rows, ensure_ascii=False, indent=2),
        _bounded_images(images, max_images),
        index,
    )


def _bounded_images(paths: Sequence[str], limit: int) -> List[str]:
    result: List[str] = []
    for path in paths:
        if path and Path(path).is_file() and path not in result:
            result.append(path)
        if len(result) >= limit:
            break
    return result


def _separately_bounded_images(
    evidence_paths: Sequence[str], task_paths: Sequence[str], limit: int
) -> List[str]:
    """Retain independently bounded runtime evidence and public references."""

    evidence = _bounded_images(evidence_paths, limit)
    task = _bounded_images(
        [path for path in task_paths if path not in evidence], limit
    )
    return evidence + task


def _acceptance(previous: PolicyJudgment, candidate: PolicyJudgment) -> Dict[str, Any]:
    old = previous.statuses()
    new = candidate.statuses()
    repaired = sorted(check_id for check_id, status in old.items() if status == "fail" and new[check_id] == "pass")
    regressions = sorted(check_id for check_id, status in old.items() if status == "pass" and new[check_id] != "pass")
    return {
        "accepted": bool(repaired) and not regressions,
        "repaired_checks": repaired,
        "regressed_checks": regressions,
        "rule": "at_least_one_failure_repaired_and_no_previous_pass_regressed",
    }


def _program_manifest(root: Path) -> Dict[str, Any]:
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file() and not item.is_symlink()):
        relative = path.relative_to(root)
        if any(part in {".git", "node_modules", "dist", "build", ".cache", ".vite"} for part in relative.parts):
            continue
        rows.append(
            {
                "path": relative.as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _file_sha256(path),
            }
        )
    return {"version": hash_program(root), "files": rows}


def _changed_files(before: Mapping[str, Any], after: Mapping[str, Any]) -> List[str]:
    left = {row["path"]: row["sha256"] for row in before.get("files", [])}
    right = {row["path"]: row["sha256"] for row in after.get("files", [])}
    return sorted(path for path in set(left) | set(right) if left.get(path) != right.get(path))


def _execution_record(execution: Mapping[str, Any], plan_digest: str, kind: str) -> Dict[str, Any]:
    return {
        "kind": kind,
        "execution_id": execution.get("execution_id"),
        "code_version": execution.get("code_version"),
        "purpose": execution.get("purpose"),
        "plan_sha256": plan_digest,
        "result_path": execution.get("result_path"),
        "result_relative_path": execution.get("result_relative_path"),
        "action_count": len(execution.get("actions", [])),
        "executor_semantic_score_ignored": True,
    }


def _jsonl_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
