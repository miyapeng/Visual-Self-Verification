from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from ..backends import ModelBackend
from ..io import read_json, write_json
from ..schema import GenerationRequest
from .interactive import InteractiveJudge, hash_program
from .schema import ACTION_TYPES, ActionPlan, ChecklistItem


class OneShotActionPlanner:
    """Plans one complete interaction trace from one frozen initial observation."""

    def __init__(
        self,
        *,
        backend: ModelBackend,
        model_name: str,
        run_dir: str | Path,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        seed: Optional[int] = 0,
    ):
        self.backend = backend
        self.model_name = model_name
        self.run_dir = Path(run_dir).resolve()
        self.max_tokens = int(max_tokens)
        self.temperature = float(temperature)
        self.seed = seed
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.judge = InteractiveJudge(self.run_dir / "inspection")

    def plan(
        self,
        *,
        case_id: str,
        task: str,
        program_path: str | Path,
        task_image_paths: Sequence[str] = (),
        benchmark_checklist: Optional[Sequence[Dict[str, Any]]] = None,
        resume: bool = True,
    ) -> ActionPlan:
        program = Path(program_path).resolve()
        case_dir = self.run_dir / "plans" / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        plan_path = case_dir / "plan.json"
        if resume and plan_path.exists():
            return ActionPlan.from_dict(read_json(plan_path))

        inspection_path = case_dir / "inspection.json"
        if resume and inspection_path.exists():
            inspection = read_json(inspection_path)
        else:
            inspection = self.judge.execute(
                case_id=f"{case_id}-planner-inspection",
                program_path=program,
                plan=_inspection_plan(),
                purpose="planner-initial-observation",
                record_video=False,
            )
            write_json(inspection_path, inspection)

        prompt, initial_screenshot = self._prompt(
            task=task,
            program=program,
            inspection=inspection,
            benchmark_checklist=benchmark_checklist,
        )
        images = [str(Path(path).resolve()) for path in task_image_paths]
        if initial_screenshot:
            images.append(initial_screenshot)
        request = GenerationRequest(
            prompt=prompt,
            image_paths=images,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            seed=self.seed,
            system_prompt=(
                "You are a test planner. Return one complete deterministic browser "
                "action plan as JSON. Never interact step by step and never write code."
            ),
        )
        request_manifest = _request_manifest(request, self.model_name)
        request_path = case_dir / "request.json"
        response_path = case_dir / "response.txt"
        if request_path.exists() and read_json(request_path) != request_manifest:
            raise ValueError("Refusing to reuse a planner response for a different request")
        if not request_path.exists():
            write_json(request_path, request_manifest)
        if resume and response_path.exists():
            response = response_path.read_text(encoding="utf-8")
        else:
            response = self.backend.generate(request)
            response_path.write_text(response, encoding="utf-8")

        value = _parse_json_object(response)
        if benchmark_checklist is not None:
            # The benchmark checklist is authoritative. The planner chooses the
            # complete action trace but cannot silently rewrite the rubric.
            value["checklist"] = list(benchmark_checklist)
        value["planner"] = "llm-one-shot-frozen"
        value["plan_version"] = 1
        metadata = dict(value.get("metadata", {}))
        metadata.update(
            {
                "planner_model": self.model_name,
                "program_version": hash_program(program),
                "inspection_result": str(inspection_path),
                "benchmark_checklist": benchmark_checklist is not None,
                "deterministic_replay": True,
            }
        )
        value["metadata"] = metadata
        plan = ActionPlan.from_dict(value)
        write_json(plan_path, plan.to_dict())
        return plan

    def _prompt(
        self,
        *,
        task: str,
        program: Path,
        inspection: Dict[str, Any],
        benchmark_checklist: Optional[Sequence[Dict[str, Any]]],
    ) -> tuple[str, Optional[str]]:
        initial = dict(inspection.get("initial_state") or {})
        first_action = (inspection.get("actions") or [{}])[0]
        evidence = dict(first_action.get("evidence") or initial.get("evidence") or {})
        screenshot = evidence.get("screenshot")
        dom = _read_excerpt(evidence.get("dom"), 12_000)
        accessibility = _read_excerpt(evidence.get("accessibility"), 8_000)
        console = _read_excerpt(evidence.get("console_delta"), 4_000)
        checklist_payload = (
            json.dumps(list(benchmark_checklist), ensure_ascii=False, indent=2)
            if benchmark_checklist is not None
            else "[Generate a concise checklist from the task. Every item needs an executable assertion.]"
        )
        source = _render_program(program, 80_000)
        prompt = f"""Task:\n{task}\n\nAuthoritative checklist:\n{checklist_payload}\n\nInitial browser state:\n{json.dumps({k: initial.get(k) for k in ('url', 'title', 'fingerprint', 'browser_state')}, ensure_ascii=False, indent=2)}\n\nAccessibility tree:\n{accessibility or '[empty]'}\n\nDOM snapshot:\n{dom or '[empty]'}\n\nConsole events:\n{console or '[empty]'}\n\nCurrent program:\n{source}\n\nReturn exactly one JSON object with keys checklist, actions, planner, plan_version, metadata. Plan the entire trace now; it will be frozen and replayed without another model decision. Allowed action types: {', '.join(sorted(ACTION_TYPES))}. Use stable selectors or accessible role/name. Every checklist item must have at least one linked assert_* action. Supported action fields include type, checklist_id, selector, role, name, text, value, url, key, expected, match, x, y, milliseconds, timeout_ms, minimum_ratio, reference_image, note. Do not include comments, markdown, code changes, or actions outside this vocabulary."""
        return prompt, str(screenshot) if screenshot and Path(screenshot).is_file() else None


def _inspection_plan() -> ActionPlan:
    return ActionPlan.from_dict(
        {
            "planner": "bootstrap-inspector",
            "checklist": [
                {
                    "id": "initial_state",
                    "description": "Initial browser state is captured for planning.",
                    "expected": "A screenshot and browser state are available.",
                    "source": "harness",
                }
            ],
            "actions": [
                {"type": "screenshot", "checklist_id": "initial_state"}
            ],
        }
    )


def _parse_json_object(text: str) -> Dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    if start < 0:
        raise ValueError("Planner response does not contain a JSON object")
    try:
        value, end = json.JSONDecoder().raw_decode(stripped[start:])
    except json.JSONDecodeError as exc:
        raise ValueError("Planner response does not contain valid JSON") from exc
    trailing = stripped[start + end :].strip()
    if trailing and trailing != "```":
        raise ValueError("Planner response has unexpected trailing content")
    if not isinstance(value, dict):
        raise ValueError("Planner response must be a JSON object")
    return value


def _read_excerpt(path: Any, max_chars: int) -> str:
    if not path or not Path(str(path)).is_file():
        return ""
    text = Path(str(path)).read_text(encoding="utf-8", errors="replace")
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def _render_program(program: Path, max_chars: int) -> str:
    root = program if program.is_dir() else program.parent
    allowed = {".html", ".htm", ".css", ".js", ".jsx", ".ts", ".tsx", ".json"}
    parts = []
    used = 0
    paths = [program] if program.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
    for path in paths:
        if path.suffix.lower() not in allowed:
            continue
        label = path.name if program.is_file() else str(path.relative_to(root))
        content = path.read_text(encoding="utf-8", errors="replace")
        chunk = f"\n--- FILE: {label} ---\n{content}"
        remaining = max_chars - used
        if remaining <= 0:
            break
        parts.append(chunk[:remaining])
        used += len(parts[-1])
    return "".join(parts)


def _request_manifest(request: GenerationRequest, model_name: str) -> Dict[str, Any]:
    image_rows = []
    for raw in request.image_paths:
        path = Path(raw)
        image_rows.append(
            {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    payload = {
        "model": model_name,
        "system_prompt": request.system_prompt,
        "prompt": request.prompt,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "seed": request.seed,
        "images": image_rows,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {**payload, "request_fingerprint": hashlib.sha256(canonical).hexdigest()}
