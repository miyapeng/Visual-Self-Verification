"""Archived pre-refactor Vision2Web browser interfaces.

This module is retained only so historical experiment-1 trajectories and
already-running smoke jobs remain reproducible. The later batch-plan module is
also archived. Active runs register OpenHands' native ``BrowserToolSet``
directly through ``vision2web_openhands_browser_entrypoint``.

The historical implementation reused OpenHands' native Chromium session and
did not modify the official evaluator.
"""

from __future__ import annotations

import asyncio
import base64
import difflib
import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Self

from pydantic import BaseModel, Field

from openhands.sdk.llm import ImageContent, TextContent
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)
from openhands.tools.browser_use.definition import BrowserToolSet


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation
    from openhands.sdk.conversation.state import ConversationState
    from openhands.tools.browser_use.impl import BrowserToolExecutor


DEFAULT_APP_URL = "http://localhost:3000"
MAX_SCENARIOS = 8
MAX_ACTIONS_PER_SCENARIO = 12
MAX_TOTAL_ACTIONS = 48

_CONSOLE_CAPTURE_SCRIPT = r"""
(() => {
  if (window.__mmcodeEvidence) return;
  const evidence = {console: [], errors: [], unhandled: []};
  const safe = (value) => {
    try {
      if (typeof value === 'string') return value;
      return JSON.stringify(value);
    } catch (_) {
      return String(value);
    }
  };
  for (const level of ['error', 'warn']) {
    const original = console[level].bind(console);
    console[level] = (...args) => {
      evidence.console.push({level, args: args.map(safe), at: Date.now()});
      original(...args);
    };
  }
  window.addEventListener('error', (event) => {
    evidence.errors.push({
      message: event.message || '',
      source: event.filename || '',
      line: event.lineno || 0,
      column: event.colno || 0,
      at: Date.now()
    });
  });
  window.addEventListener('unhandledrejection', (event) => {
    evidence.unhandled.push({reason: safe(event.reason), at: Date.now()});
  });
  Object.defineProperty(window, '__mmcodeEvidence', {
    value: evidence,
    configurable: false,
    enumerable: false,
    writable: false
  });
})();
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-") or "scenario"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class BrowserReset(BaseModel):
    """Mechanical reset condition for one independent scenario."""

    url: str = Field(default=DEFAULT_APP_URL, description="URL to load at reset.")
    clear_storage: bool = Field(
        default=False,
        description="Clear cookies plus local/session storage before loading the URL.",
    )


class BrowserPlannedAction(BaseModel):
    """One mechanical browser action; it contains no assertion or verdict."""

    type: Literal[
        "navigate",
        "click",
        "fill",
        "hover",
        "press",
        "select",
        "scroll",
        "go_back",
        "wait",
    ]
    index: int | None = Field(
        default=None,
        ge=0,
        description="Current browser element index, when already known.",
    )
    target: str | None = Field(
        default=None,
        description=(
            "Visible text, placeholder, or href used for deterministic element "
            "grounding immediately before this action."
        ),
    )
    tag: str | None = Field(
        default=None,
        description="Optional HTML tag used to disambiguate target grounding.",
    )
    url: str | None = Field(default=None, description="URL for navigate.")
    value: str | None = Field(
        default=None, description="Text for fill or exact option text for select."
    )
    key: str | None = Field(
        default=None, description="Key or shortcut for press, such as Enter or ctrl+a."
    )
    direction: Literal["up", "down", "left", "right"] | None = Field(
        default=None, description="Scroll direction."
    )
    amount: int | None = Field(
        default=None, ge=1, le=5000, description="Scroll amount in pixels."
    )
    seconds: float | None = Field(
        default=None, ge=0, le=10, description="Wait duration, capped at 10 seconds."
    )


class BrowserScenario(BaseModel):
    """A reset-separated action sequence authored by the coding policy."""

    name: str = Field(description="Stable human-readable scenario name.")
    expectation: str = Field(
        description=(
            "Observable outcome committed by the coding policy. The browser records "
            "this text but never judges it."
        )
    )
    reset: BrowserReset = Field(default_factory=BrowserReset)
    actions: list[BrowserPlannedAction] = Field(
        min_length=1,
        max_length=MAX_ACTIONS_PER_SCENARIO,
        description="Complete action sequence to execute without another model call.",
    )


class BrowserSnapshotAction(Action):
    """Inspect the current application state without judging it."""

    url: str | None = Field(
        default=None,
        description=(
            "Optional URL to navigate to before observing. Use this for the first "
            "inspection of a newly deployed application."
        ),
    )
    full_page: bool = Field(
        default=False, description="Capture the entire scrollable page when true."
    )


class BrowserExecuteAction(Action):
    """Execute complete reset-separated scenarios in one tool call."""

    scenarios: list[BrowserScenario] = Field(
        min_length=1,
        max_length=MAX_SCENARIOS,
        description=(
            "All scenarios planned in the current verification pass. The executor "
            "runs them in order and returns evidence after every action."
        ),
    )


class Vision2WebBrowserObservation(Observation):
    """Text manifest plus one image observation for every captured browser state."""

    pass


class _NativeBrowserEvidenceExecutor(
    ToolExecutor[BrowserSnapshotAction | BrowserExecuteAction, Vision2WebBrowserObservation]
):
    def __init__(self, native: "BrowserToolExecutor", *, trace_root: Path):
        self.native = native
        self.trace_root = trace_root.resolve()
        self.evidence_root = self.trace_root / "evidence"
        self.evidence_root.mkdir(parents=True, exist_ok=True)
        self.events_path = self.trace_root / "browser.events.jsonl"
        self._write_lock = threading.Lock()
        self._closed = False

    def __call__(
        self,
        action: BrowserSnapshotAction | BrowserExecuteAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> Vision2WebBrowserObservation:
        try:
            return self.native._async_executor.run_async(
                self._execute(action), timeout=600
            )
        except Exception as exc:
            self._append("browser_interface_error", {"error": f"{type(exc).__name__}: {exc}"})
            return Vision2WebBrowserObservation.from_text(
                f"Browser interface failed: {type(exc).__name__}: {exc}",
                is_error=True,
            )

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.native.close()

    def _append(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "timestamp": _utc_now(),
            "monotonic_ns": time.monotonic_ns(),
            "type": event_type,
            "payload": payload,
        }
        with self._write_lock:
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    async def _execute(
        self, action: BrowserSnapshotAction | BrowserExecuteAction
    ) -> Vision2WebBrowserObservation:
        if isinstance(action, BrowserSnapshotAction):
            return await self._execute_snapshot(action)
        return await self._execute_scenarios(action)

    async def _execute_snapshot(
        self, action: BrowserSnapshotAction
    ) -> Vision2WebBrowserObservation:
        self._append("snapshot_request", action.model_dump())
        if action.url:
            self._append("browser_internal", {"stage": "snapshot_navigate_started", "url": action.url})
            await self.native.navigate(action.url)
            self._append("browser_internal", {"stage": "snapshot_navigate_finished", "url": action.url})
        record, screenshot = await self._capture(
            scenario="snapshot", step="current", previous_dom=None, full_page=action.full_page
        )
        self._append("snapshot_observation", record)
        return self._observation(
            {"interface": "browser_snapshot", "observation": record},
            [("current browser state", screenshot)],
        )

    async def _execute_scenarios(
        self, action: BrowserExecuteAction
    ) -> Vision2WebBrowserObservation:
        total_actions = sum(len(scenario.actions) for scenario in action.scenarios)
        if total_actions > MAX_TOTAL_ACTIONS:
            raise ValueError(
                f"The batch contains {total_actions} actions; maximum is {MAX_TOTAL_ACTIONS}."
            )
        execution_id = str(uuid.uuid4())
        plan = action.model_dump()
        self._append(
            "browser_plan",
            {"execution_id": execution_id, "plan": plan, "browser_makes_verdict": False},
        )
        scenario_results: list[dict[str, Any]] = []
        images: list[tuple[str, bytes]] = []
        for scenario_index, scenario in enumerate(action.scenarios):
            result: dict[str, Any] = {
                "name": scenario.name,
                "expectation": scenario.expectation,
                "reset": scenario.reset.model_dump(),
                "steps": [],
                "status": "ok",
            }
            previous_dom: str | None = None
            try:
                await self._reset(scenario.reset)
                reset_record, reset_image = await self._capture(
                    scenario=scenario.name,
                    step="reset",
                    previous_dom=None,
                )
                result["reset_observation"] = reset_record
                images.append((f"{scenario.name}: reset", reset_image))
                previous_dom = self._read_artifact(reset_record["evidence"]["dom"]["path"])
                for step_index, planned in enumerate(scenario.actions):
                    action_record = {
                        "execution_id": execution_id,
                        "scenario_index": scenario_index,
                        "scenario": scenario.name,
                        "step_index": step_index,
                        "action": planned.model_dump(exclude_none=True),
                    }
                    self._append("browser_action", action_record)
                    action_output = await self._perform(planned)
                    observation, screenshot = await self._capture(
                        scenario=scenario.name,
                        step=f"{step_index:02d}-{planned.type}",
                        previous_dom=previous_dom,
                    )
                    previous_dom = self._read_artifact(observation["evidence"]["dom"]["path"])
                    step_result = {
                        "index": step_index,
                        "action": planned.model_dump(exclude_none=True),
                        "execution_output": action_output,
                        "observation": observation,
                    }
                    result["steps"].append(step_result)
                    images.append((f"{scenario.name}: step {step_index} {planned.type}", screenshot))
                    self._append("browser_observation", {**action_record, **step_result})
            except Exception as exc:
                result["status"] = "error"
                result["error"] = f"{type(exc).__name__}: {exc}"
                self._append(
                    "browser_scenario_error",
                    {
                        "execution_id": execution_id,
                        "scenario": scenario.name,
                        "error": result["error"],
                    },
                )
            scenario_results.append(result)

        manifest = {
            "interface": "browser_execute",
            "execution_id": execution_id,
            "browser_makes_verdict": False,
            "scenario_results": scenario_results,
        }
        manifest_path = self.trace_root / "executions" / f"{execution_id}.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self._append(
            "browser_execution_complete",
            {"execution_id": execution_id, "manifest": str(manifest_path)},
        )
        return self._observation(manifest, images)

    async def _reset(self, reset: BrowserReset) -> None:
        await self.native._ensure_initialized()
        session = self.native._server.browser_session
        if session is None:
            raise RuntimeError("OpenHands browser session was not initialized")
        if reset.clear_storage:
            cdp = await session.get_or_create_cdp_session()
            await cdp.cdp_client.send.Network.clearBrowserCookies(
                session_id=cdp.session_id
            )
            await self._evaluate(
                "localStorage.clear(); sessionStorage.clear(); true",
                return_by_value=True,
            )
        await self.native.navigate(reset.url)

    async def _perform(self, action: BrowserPlannedAction) -> str:
        kind = action.type
        if kind == "navigate":
            if not action.url:
                raise ValueError("navigate requires url")
            return await self.native.navigate(action.url)
        if kind == "go_back":
            return await self.native.go_back()
        if kind == "wait":
            seconds = 1.0 if action.seconds is None else action.seconds
            await asyncio.sleep(seconds)
            return f"Waited {seconds:g} seconds"
        if kind == "press":
            if not action.key:
                raise ValueError("press requires key")
            from browser_use.browser.events import SendKeysEvent

            await self._dispatch(SendKeysEvent(keys=action.key))
            return f"Pressed {action.key}"
        if kind == "scroll":
            from browser_use.browser.events import ScrollEvent

            direction = action.direction or "down"
            amount = action.amount or 600
            await self._dispatch(ScrollEvent(node=None, direction=direction, amount=amount))
            return f"Scrolled {direction} by {amount}px"

        index = await self._resolve_index(action)
        if kind == "click":
            return await self.native.click(index)
        if kind == "fill":
            if action.value is None:
                raise ValueError("fill requires value")
            return await self.native.type_text(index, action.value)

        session = self.native._server.browser_session
        if session is None:
            raise RuntimeError("OpenHands browser session was not initialized")
        node = await session.get_dom_element_by_index(index)
        if node is None:
            raise LookupError(f"Element index {index} is no longer available")
        if kind == "select":
            if action.value is None:
                raise ValueError("select requires value")
            from browser_use.browser.events import SelectDropdownOptionEvent

            await self._dispatch(SelectDropdownOptionEvent(node=node, text=action.value))
            return f"Selected {action.value!r} in element {index}"
        if kind == "hover":
            await self._hover(node)
            return f"Hovered element {index}"
        raise ValueError(f"Unsupported browser action: {kind}")

    async def _dispatch(self, event: Any) -> None:
        session = self.native._server.browser_session
        if session is None:
            raise RuntimeError("OpenHands browser session was not initialized")
        pending = session.event_bus.dispatch(event)
        await pending

    async def _resolve_index(self, action: BrowserPlannedAction) -> int:
        state_observation = await self.native.get_state(include_screenshot=False)
        try:
            state = json.loads(state_observation.text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Could not refresh browser element map: {state_observation.text}") from exc
        elements = state.get("interactive_elements", [])
        if action.index is not None:
            if any(int(row.get("index", -1)) == action.index for row in elements):
                return action.index
            raise LookupError(f"Element index {action.index} is absent from the current state")
        if not action.target:
            raise ValueError(f"{action.type} requires index or target")
        needle = " ".join(action.target.casefold().split())

        def values(row: dict[str, Any]) -> list[str]:
            return [
                " ".join(str(row.get(key, "")).casefold().split())
                for key in ("text", "placeholder", "href")
                if row.get(key)
            ]

        candidates = [
            row
            for row in elements
            if (not action.tag or str(row.get("tag", "")).casefold() == action.tag.casefold())
            and needle in values(row)
        ]
        if not candidates:
            candidates = [
                row
                for row in elements
                if (not action.tag or str(row.get("tag", "")).casefold() == action.tag.casefold())
                and any(needle in value for value in values(row))
            ]
        if len(candidates) != 1:
            sample = [
                {key: row.get(key) for key in ("index", "tag", "text", "placeholder", "href")}
                for row in candidates[:8]
            ]
            raise LookupError(
                f"Target {action.target!r} matched {len(candidates)} elements; candidates={sample}"
            )
        return int(candidates[0]["index"])

    async def _hover(self, node: Any) -> None:
        backend_node_id = getattr(node, "backend_node_id", None)
        if backend_node_id is None:
            raise RuntimeError("browser_use DOM node has no backend_node_id for hover")
        session = self.native._server.browser_session
        cdp = await session.get_or_create_cdp_session()
        box = await cdp.cdp_client.send.DOM.getBoxModel(
            params={"backendNodeId": backend_node_id}, session_id=cdp.session_id
        )
        quad = box.get("model", {}).get("content") or box.get("model", {}).get("border")
        if not quad or len(quad) < 8:
            raise RuntimeError("Could not obtain an element box for hover")
        x = sum(quad[0::2]) / 4
        y = sum(quad[1::2]) / 4
        await cdp.cdp_client.send.Input.dispatchMouseEvent(
            params={"type": "mouseMoved", "x": x, "y": y}, session_id=cdp.session_id
        )

    async def _evaluate(self, expression: str, *, return_by_value: bool) -> Any:
        session = self.native._server.browser_session
        if session is None:
            raise RuntimeError("OpenHands browser session was not initialized")
        cdp = await session.get_or_create_cdp_session()
        result = await cdp.cdp_client.send.Runtime.evaluate(
            params={
                "expression": expression,
                "returnByValue": return_by_value,
                "awaitPromise": True,
            },
            session_id=cdp.session_id,
        )
        if result.get("exceptionDetails"):
            raise RuntimeError(str(result["exceptionDetails"]))
        return result.get("result", {}).get("value")

    async def _capture(
        self,
        *,
        scenario: str,
        step: str,
        previous_dom: str | None,
        full_page: bool = False,
    ) -> tuple[dict[str, Any], bytes]:
        self._append("browser_internal", {"stage": "capture_state_started", "scenario": scenario, "step": step})
        state_observation = await self.native.get_state(include_screenshot=not full_page)
        self._append("browser_internal", {"stage": "capture_state_finished", "scenario": scenario, "step": step})
        try:
            state = json.loads(state_observation.text)
        except json.JSONDecodeError:
            state = {"raw_state": state_observation.text}
        session = self.native._server.browser_session
        if session is None:
            raise RuntimeError("OpenHands browser session was not initialized")
        if full_page or not state_observation.screenshot_data:
            screenshot = await session.take_screenshot(full_page=full_page)
        else:
            screenshot = base64.b64decode(state_observation.screenshot_data)
        self._append("browser_internal", {"stage": "capture_screenshot_finished", "scenario": scenario, "step": step})

        dom = await self._evaluate("document.documentElement.outerHTML", return_by_value=True)
        self._append("browser_internal", {"stage": "capture_dom_finished", "scenario": scenario, "step": step})
        runtime = await self._evaluate(
            "(() => { const e = window.__mmcodeEvidence || {console:[],errors:[],unhandled:[]}; "
            "const out = {console:[...e.console],errors:[...e.errors],unhandled:[...e.unhandled]}; "
            "e.console.length=0; e.errors.length=0; e.unhandled.length=0; "
            "return JSON.stringify(out); })()",
            return_by_value=True,
        )
        self._append("browser_internal", {"stage": "capture_runtime_finished", "scenario": scenario, "step": step})
        resources = await self._evaluate(
            "JSON.stringify(performance.getEntriesByType('resource').map((e) => "
            "({url:e.name,initiatorType:e.initiatorType,duration:e.duration,transferSize:e.transferSize})))",
            return_by_value=True,
        )
        self._append("browser_internal", {"stage": "capture_resources_finished", "scenario": scenario, "step": step})
        cdp = await session.get_or_create_cdp_session()
        await cdp.cdp_client.send.Accessibility.enable(session_id=cdp.session_id)
        ax_result = await cdp.cdp_client.send.Accessibility.getFullAXTree(
            session_id=cdp.session_id
        )
        self._append("browser_internal", {"stage": "capture_accessibility_finished", "scenario": scenario, "step": step})
        ax_tree = ax_result.get("nodes", [])

        capture_id = f"{time.time_ns()}-{_slug(scenario)}-{_slug(step)}"
        root = self.evidence_root / capture_id
        root.mkdir(parents=True, exist_ok=False)
        screenshot_path = root / "screenshot.png"
        dom_path = root / "dom.html"
        state_path = root / "browser_state.json"
        ax_path = root / "accessibility.json"
        runtime_path = root / "runtime.json"
        resources_path = root / "resources.json"
        screenshot_path.write_bytes(screenshot)
        dom_text = str(dom or "")
        dom_path.write_text(dom_text, encoding="utf-8")
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        ax_path.write_text(json.dumps(ax_tree, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        runtime_value = json.loads(runtime) if isinstance(runtime, str) else (runtime or {})
        resources_value = json.loads(resources) if isinstance(resources, str) else (resources or [])
        runtime_path.write_text(
            json.dumps(runtime_value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        resources_path.write_text(
            json.dumps(resources_value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        dom_change = self._dom_change(previous_dom, dom_text)
        record = {
            "captured_at": _utc_now(),
            "url": state.get("url") or await session.get_current_page_url(),
            "title": state.get("title"),
            "viewport": state.get("viewport"),
            "scroll": state.get("scroll"),
            "interactive_elements": state.get("interactive_elements", []),
            "accessibility_tree": self._accessibility_summary(ax_tree),
            "console_delta": runtime_value,
            "dom_change": dom_change,
            "evidence": {
                "screenshot": self._artifact(screenshot_path),
                "dom": self._artifact(dom_path),
                "accessibility": self._artifact(ax_path),
                "browser_state": self._artifact(state_path),
                "runtime": self._artifact(runtime_path),
                "resources": self._artifact(resources_path),
            },
        }
        return record, screenshot

    def _artifact(self, path: Path) -> dict[str, Any]:
        data = path.read_bytes()
        return {
            "path": str(path),
            "relative_path": path.relative_to(self.trace_root).as_posix(),
            "sha256": _sha256(data),
            "bytes": len(data),
        }

    @staticmethod
    def _dom_change(before: str | None, after: str) -> dict[str, Any]:
        if before is None:
            return {"changed": None, "reason": "initial_state"}
        before_hash = _sha256(before.encode())
        after_hash = _sha256(after.encode())
        if before_hash == after_hash:
            return {"changed": False, "before_sha256": before_hash, "after_sha256": after_hash}
        diff = list(
            difflib.unified_diff(
                before.splitlines(), after.splitlines(), lineterm="", n=1
            )
        )
        return {
            "changed": True,
            "before_sha256": before_hash,
            "after_sha256": after_hash,
            "diff_line_count": len(diff),
            "diff_preview": diff[:80],
        }

    @staticmethod
    def _accessibility_summary(nodes: list[dict[str, Any]]) -> dict[str, Any]:
        def value(row: dict[str, Any], key: str) -> Any:
            item = row.get(key)
            return item.get("value") if isinstance(item, dict) else item

        visible = []
        for row in nodes:
            if not isinstance(row, dict) or row.get("ignored"):
                continue
            role = value(row, "role")
            name = value(row, "name")
            current_value = value(row, "value")
            if not any((role, name, current_value)):
                continue
            visible.append(
                {
                    "node_id": row.get("nodeId"),
                    "parent_id": row.get("parentId"),
                    "backend_dom_node_id": row.get("backendDOMNodeId"),
                    "role": role,
                    "name": name,
                    "value": current_value,
                }
            )
        return {
            "nodes": visible[:300],
            "node_count": len(visible),
            "truncated": len(visible) > 300,
        }

    @staticmethod
    def _read_artifact(path: str) -> str:
        return Path(path).read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _observation(
        manifest: dict[str, Any], images: list[tuple[str, bytes]]
    ) -> Vision2WebBrowserObservation:
        content: list[TextContent | ImageContent] = [
            TextContent(text=json.dumps(manifest, ensure_ascii=False, indent=2))
        ]
        for label, payload in images:
            content.append(TextContent(text=f"\nBrowser screenshot — {label}"))
            content.append(
                ImageContent(
                    image_urls=[
                        "data:image/png;base64," + base64.b64encode(payload).decode("ascii")
                    ]
                )
            )
        return Vision2WebBrowserObservation(content=content, is_error=False)


class BrowserSnapshotTool(
    ToolDefinition[BrowserSnapshotAction, Vision2WebBrowserObservation]
):
    name: ClassVar[str] = "browser_snapshot"

    @classmethod
    def create(cls, executor: _NativeBrowserEvidenceExecutor) -> Sequence[Self]:
        return [
            cls(
                description=(
                    "Observe the current deployed page. Returns a screenshot, URL, full "
                    "accessibility tree, interactive browser state, DOM snapshot, resource "
                    "state, and console/runtime errors. It does not decide whether the page "
                    "is correct."
                ),
                action_type=BrowserSnapshotAction,
                observation_type=Vision2WebBrowserObservation,
                annotations=ToolAnnotations(
                    title="browser_snapshot",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


class BrowserExecuteTool(
    ToolDefinition[BrowserExecuteAction, Vision2WebBrowserObservation]
):
    name: ClassVar[str] = "browser_execute"

    @classmethod
    def create(cls, executor: _NativeBrowserEvidenceExecutor) -> Sequence[Self]:
        return [
            cls(
                description=(
                    "Execute all reset-separated browser scenarios from one complete plan. "
                    "After every action it returns a screenshot, URL, DOM change, "
                    "accessibility/browser state, and runtime errors. Prefer this single "
                    "batched call when interaction is needed. The executor never writes an "
                    "expectation, judges a result, suggests a repair, or changes source code."
                ),
                action_type=BrowserExecuteAction,
                observation_type=Vision2WebBrowserObservation,
                annotations=ToolAnnotations(
                    title="browser_execute",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


class Vision2WebBrowserToolSet(
    ToolDefinition[
        BrowserSnapshotAction | BrowserExecuteAction,
        Vision2WebBrowserObservation,
    ]
):
    """Resolve two semantic tools over OpenHands' native shared browser executor."""

    name: ClassVar[str] = "vision2web_browser_interfaces"

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",
        trace_root: str,
        **_: Any,
    ) -> list[ToolDefinition]:
        if BrowserToolSet._shared_executor is None:
            native_definitions = BrowserToolSet.create(
                conv_state,
                inject_scripts=[_CONSOLE_CAPTURE_SCRIPT],
                action_timeout_seconds=120,
                # browser-use otherwise downloads optional extensions on first
                # launch. Vision2Web workers may be offline; these extensions are
                # unrelated to rendering, interaction, screenshots, or CDP data.
                enable_default_extensions=False,
            )
            native = native_definitions[0].executor
        else:
            native = BrowserToolSet._shared_executor
        if native is None:
            raise RuntimeError("OpenHands did not create its native BrowserToolExecutor")
        executor = _NativeBrowserEvidenceExecutor(native, trace_root=Path(trace_root))
        return [
            *BrowserSnapshotTool.create(executor),
            *BrowserExecuteTool.create(executor),
        ]


register_tool(Vision2WebBrowserToolSet.name, Vision2WebBrowserToolSet)
