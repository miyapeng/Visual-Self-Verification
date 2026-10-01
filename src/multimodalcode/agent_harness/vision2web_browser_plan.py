"""Archived batch-plan experiment over OpenHands' native BrowserToolSet.

This module is retained only to interpret and reproduce historical exploratory
trajectories. Active ``browser_enabled`` and ``guided_vsv`` runs expose the
complete upstream ``BrowserToolSet`` directly and do not import or register this
module. Do not use it for new primary experiments.
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
from openhands.tools.browser_use.definition import (
    BrowserGetStateAction,
    BrowserObservation,
    BrowserToolSet,
)

from .vision2web_experiment import BROWSER_INTERFACE_REVISION
from .vision2web_trace import (
    append_trace_event,
    capture_observation_binding,
    capture_first_application_observation,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation
    from openhands.sdk.conversation.state import ConversationState
    from openhands.tools.browser_use.impl import BrowserToolExecutor


DEFAULT_APP_URL = "http://localhost:3000"
MAX_SCENARIOS = 8
MAX_ACTIONS_PER_SCENARIO = 12
MAX_TOTAL_ACTIONS = 48
LOCAL_BROWSER_PROXY_BYPASS = "localhost,127.0.0.1,[::1]"
INTERACTIVE_AX_ROLES = {
    "button",
    "checkbox",
    "combobox",
    "link",
    "listbox",
    "menuitem",
    "option",
    "radio",
    "searchbox",
    "slider",
    "spinbutton",
    "switch",
    "tab",
    "textbox",
}

_CONSOLE_CAPTURE_SCRIPT = r"""
(() => {
  if (window.__mmcodeEvidence) return;
  const evidence = {console: [], errors: [], unhandled: []};
  const safe = (value) => {
    try { return typeof value === 'string' ? value : JSON.stringify(value); }
    catch (_) { return String(value); }
  };
  for (const level of ['error', 'warn']) {
    const original = console[level].bind(console);
    console[level] = (...args) => {
      evidence.console.push({level, args: args.map(safe), at: Date.now()});
      original(...args);
    };
  }
  window.addEventListener('error', (event) => evidence.errors.push({
    message: event.message || '', source: event.filename || '',
    line: event.lineno || 0, column: event.colno || 0, at: Date.now()
  }));
  window.addEventListener('unhandledrejection', (event) =>
    evidence.unhandled.push({reason: safe(event.reason), at: Date.now()}));
  Object.defineProperty(window, '__mmcodeEvidence', {value: evidence});
})();
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-") or "scenario"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _browser_proxy() -> Any | None:
    """Keep outbound browser access while never proxying the local app.

    The worker's HTTP(S) proxy is needed by package managers in Level 2/3.
    Chromium is a child of that same worker and may inherit the proxy, so use
    browser-use's native typed proxy setting with an explicit loopback bypass.
    """

    server = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
    )
    if not server:
        return None
    from browser_use.browser.profile import ProxySettings

    return ProxySettings(server=server, bypass=LOCAL_BROWSER_PROXY_BYPASS)


async def _direct_viewport_screenshot(native: "BrowserToolExecutor") -> bytes:
    """Capture the viewport through the native session's existing CDP channel."""

    await native._ensure_initialized()
    session = native._server.browser_session
    if session is None:
        raise RuntimeError("OpenHands browser session was not initialized")
    cdp = await session.get_or_create_cdp_session()
    result = await cdp.cdp_client.send.Page.captureScreenshot(
        params={
            "format": "png",
            "fromSurface": True,
            "captureBeyondViewport": False,
        },
        session_id=cdp.session_id,
    )
    encoded = result.get("data") if isinstance(result, dict) else None
    if not encoded:
        raise RuntimeError("CDP returned no screenshot data")
    return base64.b64decode(encoded)


async def _direct_compact_browser_state(
    native: "BrowserToolExecutor", *, include_screenshot: bool
) -> tuple[dict[str, Any], bytes | None, list[dict[str, Any]]]:
    """Read compact state from the existing native BrowserSession.

    This avoids browser-use's optional clean/full-page screenshot processing,
    which can time out on image-heavy generated sites. Navigation, session
    ownership, and all interaction still remain in OpenHands BrowserToolSet.
    """

    await native._ensure_initialized()
    session = native._server.browser_session
    if session is None:
        raise RuntimeError("OpenHands browser session was not initialized")
    cdp = await session.get_or_create_cdp_session()
    raw_state = await cdp.cdp_client.send.Runtime.evaluate(
        params={
            "expression": (
                "(() => {"
                "const roleFor = (el) => el.getAttribute('role') || ({"
                "A:'link',BUTTON:'button',INPUT:(el.type==='search'?'searchbox':"
                "(el.type==='checkbox'?'checkbox':el.type==='radio'?'radio':'textbox'))," 
                "TEXTAREA:'textbox',SELECT:'combobox'}[el.tagName] || 'generic');"
                "const nodes = Array.from(document.querySelectorAll("
                "'a,button,input,textarea,select,[role],[contenteditable=true]')).map(el => ({"
                "role: roleFor(el), name: (el.getAttribute('aria-label') || "
                "el.getAttribute('title') || el.getAttribute('placeholder') || "
                "el.innerText || el.textContent || '').trim().replace(/\\s+/g,' ').slice(0,240),"
                "value: ('value' in el ? String(el.value || '') : '')}));"
                "return {url: location.href, title: document.title, "
                "viewport: {width: window.innerWidth, height: window.innerHeight}, "
                "scroll: {x: window.scrollX, y: window.scrollY}, nodes};})()"
            ),
            "returnByValue": True,
            "awaitPromise": True,
        },
        session_id=cdp.session_id,
    )
    state = raw_state.get("result", {}).get("value", {})
    if not isinstance(state, dict):
        state = {}
    compact_nodes = state.pop("nodes", [])
    if not isinstance(compact_nodes, list):
        compact_nodes = []
    accessible = [row for row in compact_nodes if isinstance(row, dict)]
    interactive = [
        row
        for row in accessible
        if str(row.get("role") or "").casefold() in INTERACTIVE_AX_ROLES
    ]
    state.update(
        {
            "interactive_elements": interactive[:100],
            "interactive_element_count": len(interactive),
            "interactive_elements_truncated": len(interactive) > 100,
            "accessibility_tree": {
                "nodes": accessible[:100],
                "node_count": len(accessible),
                "truncated": len(accessible) > 100,
            },
        }
    )
    screenshot = (
        await _direct_viewport_screenshot(native) if include_screenshot else None
    )
    return state, screenshot, accessible


def _ax_value(node: dict[str, Any], key: str) -> Any:
    value = node.get(key)
    return value.get("value") if isinstance(value, dict) else value


class BrowserReset(BaseModel):
    """Reset condition for one independent scenario."""

    url: str = Field(default=DEFAULT_APP_URL, description="URL to load at reset.")
    viewport_width: int | None = Field(
        default=None,
        ge=320,
        le=3840,
        description="Optional viewport width for this scenario.",
    )
    viewport_height: int | None = Field(
        default=None,
        ge=320,
        le=2160,
        description="Optional viewport height for this scenario.",
    )
    clear_storage: bool = Field(
        default=False,
        description="Clear cookies and origin storage, then reload the reset URL.",
    )
    bypass_cache: bool = Field(
        default=False,
        description=(
            "Disable the native Chromium session cache before loading the reset URL. "
            "Useful when replaying a check after editing a cacheable static app."
        ),
    )


class BrowserTarget(BaseModel):
    """Stable semantic target resolved against the latest accessibility tree."""

    role: str = Field(description="Accessibility role, for example button or textbox.")
    name: str = Field(description="Accessible name of the target element.")
    exact: bool = Field(
        default=True,
        description="Require exact normalized role/name matching when true.",
    )


class BrowserPlannedAction(BaseModel):
    """One mechanical action; it contains no assertion or verdict."""

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
    target: BrowserTarget | None = Field(
        default=None,
        description="Semantic target for click, fill, hover, or select.",
    )
    url: str | None = Field(default=None, description="URL for navigate.")
    value: str | None = Field(
        default=None, description="Text for fill or exact option text for select."
    )
    key: str | None = Field(
        default=None, description="Key or shortcut for press, such as Enter or ctrl+a."
    )
    direction: Literal["up", "down", "left", "right"] | None = None
    amount: int | None = Field(default=None, ge=1, le=5000)
    seconds: float | None = Field(default=None, ge=0, le=10)


class BrowserScenario(BaseModel):
    """A complete reset-separated action sequence authored by the policy."""

    name: str = Field(description="Stable human-readable scenario name.")
    expectation: str = Field(
        description="Observable expected outcome, recorded but never judged by the browser."
    )
    reset: BrowserReset = Field(default_factory=BrowserReset)
    actions: list[BrowserPlannedAction] = Field(
        min_length=1,
        max_length=MAX_ACTIONS_PER_SCENARIO,
        description="Complete sequence executed without another model call.",
    )


class BrowserExecutePlanAction(Action):
    """Execute one functional verification episode in one tool call."""

    scenarios: list[BrowserScenario] = Field(
        min_length=1,
        max_length=MAX_SCENARIOS,
        description=(
            "Reset-separated scenarios for one coherent functional objective, or a "
            "tightly coupled positive/negative pair. Use another tool call for a "
            "different functional objective."
        ),
    )


class BrowserPlanObservation(Observation):
    """Execution manifest followed by one screenshot per recorded state."""

    pass


class _BrowserPlanExecutor(
    ToolExecutor[BrowserExecutePlanAction, BrowserPlanObservation]
):
    def __init__(
        self,
        native: "BrowserToolExecutor",
        *,
        trace_root: Path,
        workspace: Path,
    ):
        self.native = native
        self.trace_root = trace_root.resolve()
        self.workspace = workspace.resolve()
        self.evidence_root = self.trace_root / "evidence"
        self.evidence_root.mkdir(parents=True, exist_ok=True)
        self.events_path = self.trace_root / "browser.events.jsonl"
        self._write_lock = threading.Lock()
        self._closed = False

    def __call__(
        self,
        action: BrowserExecutePlanAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> BrowserPlanObservation:
        try:
            return self.native._async_executor.run_async(
                self._execute(action), timeout=600
            )
        except Exception as exc:
            self._append(
                "browser_interface_error",
                {"error": f"{type(exc).__name__}: {exc}"},
            )
            return BrowserPlanObservation.from_text(
                f"browser_execute_plan failed: {type(exc).__name__}: {exc}",
                is_error=True,
            )

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.native.close()

    def _append(self, event_type: str, payload: dict[str, Any]) -> None:
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

    async def _execute(
        self, action: BrowserExecutePlanAction
    ) -> BrowserPlanObservation:
        total_actions = sum(len(item.actions) for item in action.scenarios)
        if total_actions > MAX_TOTAL_ACTIONS:
            raise ValueError(
                f"The plan contains {total_actions} actions; maximum is "
                f"{MAX_TOTAL_ACTIONS}."
            )

        execution_id = str(uuid.uuid4())
        self._append(
            "browser_plan",
            {
                "execution_id": execution_id,
                "interface_revision": BROWSER_INTERFACE_REVISION,
                "plan": action.model_dump(),
                "browser_makes_verdict": False,
            },
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
                    scenario=scenario.name, step="reset", previous_dom=None
                )
                result["reset_observation"] = reset_record
                images.append((f"{scenario.name}: reset", reset_image))
                self._record_visual_observation(
                    tool="browser_execute_plan",
                    phase="reset",
                    record=reset_record,
                    screenshot=reset_image,
                    execution_id=execution_id,
                    scenario=scenario.name,
                )
                previous_dom = self._read_artifact(
                    reset_record["evidence"]["dom"]["path"]
                )

                for step_index, planned in enumerate(scenario.actions):
                    action_record = {
                        "execution_id": execution_id,
                        "scenario_index": scenario_index,
                        "scenario": scenario.name,
                        "step_index": step_index,
                        "action": planned.model_dump(exclude_none=True),
                    }
                    self._append("browser_action", action_record)
                    try:
                        execution_output, grounding = await self._perform(planned)
                        step_status = "ok"
                        step_error = None
                    except Exception as exc:
                        execution_output = None
                        grounding = None
                        step_status = "error"
                        step_error = f"{type(exc).__name__}: {exc}"

                    observation, screenshot = await self._capture(
                        scenario=scenario.name,
                        step=f"{step_index:02d}-{planned.type}-{step_status}",
                        previous_dom=previous_dom,
                    )
                    previous_dom = self._read_artifact(
                        observation["evidence"]["dom"]["path"]
                    )
                    step_result = {
                        "step": step_index,
                        "status": step_status,
                        "action": planned.model_dump(exclude_none=True),
                        "grounding": grounding,
                        "execution_output": execution_output,
                        "error": step_error,
                        "observation": observation,
                    }
                    result["steps"].append(step_result)
                    images.append(
                        (f"{scenario.name}: step {step_index} {planned.type}", screenshot)
                    )
                    self._record_visual_observation(
                        tool="browser_execute_plan",
                        phase=f"step-{step_index}",
                        record=observation,
                        screenshot=screenshot,
                        execution_id=execution_id,
                        scenario=scenario.name,
                    )
                    self._append("browser_observation", {**action_record, **step_result})
                    if step_error:
                        result["status"] = "error"
                        result["error"] = step_error
                        break
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
            "interface": "browser_execute_plan",
            "interface_revision": BROWSER_INTERFACE_REVISION,
            "execution_id": execution_id,
            "browser_makes_verdict": False,
            "scenario_results": scenario_results,
        }
        manifest_path = self.trace_root / "executions" / f"{execution_id}.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._append(
            "browser_execution_complete",
            {"execution_id": execution_id, "manifest": str(manifest_path)},
        )
        return self._observation(manifest, images)

    async def _reset(self, reset: BrowserReset) -> None:
        if reset.bypass_cache:
            await self.native._ensure_initialized()
            session = self.native._server.browser_session
            if session is None:
                raise RuntimeError("OpenHands browser session was not initialized")
            cdp = await session.get_or_create_cdp_session()
            await cdp.cdp_client.send.Network.setCacheDisabled(
                params={"cacheDisabled": True}, session_id=cdp.session_id
            )
        await self.native.navigate(reset.url)
        if reset.clear_storage:
            session = self.native._server.browser_session
            if session is None:
                raise RuntimeError("OpenHands browser session was not initialized")
            cdp = await session.get_or_create_cdp_session()
            await cdp.cdp_client.send.Network.clearBrowserCookies(
                session_id=cdp.session_id
            )
            await self._evaluate(
                "localStorage.clear(); sessionStorage.clear(); true",
                return_by_value=True,
            )
            await self.native.navigate(reset.url)

        has_width = reset.viewport_width is not None
        has_height = reset.viewport_height is not None
        if has_width != has_height:
            raise ValueError(
                "viewport_width and viewport_height must be provided together"
            )
        if has_width and has_height:
            session = self.native._server.browser_session
            if session is None:
                raise RuntimeError("OpenHands browser session was not initialized")
            cdp = await session.get_or_create_cdp_session()
            await cdp.cdp_client.send.Emulation.setDeviceMetricsOverride(
                params={
                    "width": reset.viewport_width,
                    "height": reset.viewport_height,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
                session_id=cdp.session_id,
            )
            await self._evaluate(
                "window.dispatchEvent(new Event('resize')); true",
                return_by_value=True,
            )

    async def _perform(
        self, action: BrowserPlannedAction
    ) -> tuple[str, dict[str, str] | None]:
        kind = action.type
        if kind == "navigate":
            if not action.url:
                raise ValueError("navigate requires url")
            return await self.native.navigate(action.url), None
        if kind == "go_back":
            return await self.native.go_back(), None
        if kind == "wait":
            seconds = 1.0 if action.seconds is None else action.seconds
            await asyncio.sleep(seconds)
            return f"Waited {seconds:g} seconds", None
        if kind == "press":
            if not action.key:
                raise ValueError("press requires key")
            from browser_use.browser.events import SendKeysEvent

            await self._dispatch(SendKeysEvent(keys=action.key))
            return f"Pressed {action.key}", None
        if kind == "scroll":
            from browser_use.browser.events import ScrollEvent

            direction = action.direction or "down"
            amount = action.amount or 600
            await self._dispatch(
                ScrollEvent(node=None, direction=direction, amount=amount)
            )
            return f"Scrolled {direction} by {amount}px", None

        index, grounding = await self._resolve_target(action)
        if kind == "click":
            return await self.native.click(index), grounding
        if kind == "fill":
            if action.value is None:
                raise ValueError("fill requires value")
            return await self.native.type_text(index, action.value), grounding

        session = self.native._server.browser_session
        if session is None:
            raise RuntimeError("OpenHands browser session was not initialized")
        node = await session.get_dom_element_by_index(index)
        if node is None:
            raise LookupError("The semantically grounded element is no longer available")
        if kind == "select":
            if action.value is None:
                raise ValueError("select requires value")
            from browser_use.browser.events import SelectDropdownOptionEvent

            await self._dispatch(
                SelectDropdownOptionEvent(node=node, text=action.value)
            )
            return f"Selected {action.value!r}", grounding
        if kind == "hover":
            await self._hover(node)
            return "Hovered semantic target", grounding
        raise ValueError(f"Unsupported browser action: {kind}")

    async def _resolve_target(
        self, action: BrowserPlannedAction
    ) -> tuple[int, dict[str, str]]:
        if action.target is None:
            raise ValueError(f"{action.type} requires a semantic target")
        await self.native._ensure_initialized()
        session = self.native._server.browser_session
        if session is None:
            raise RuntimeError("OpenHands browser session was not initialized")

        # Refresh browser-use's selector map after every preceding action.
        state = await session.get_browser_state_summary()
        cdp = await session.get_or_create_cdp_session()
        await cdp.cdp_client.send.Accessibility.enable(session_id=cdp.session_id)
        ax_result = await cdp.cdp_client.send.Accessibility.getFullAXTree(
            session_id=cdp.session_id
        )
        ax_by_backend = {
            int(node["backendDOMNodeId"]): node
            for node in ax_result.get("nodes", [])
            if isinstance(node, dict) and node.get("backendDOMNodeId") is not None
        }
        expected_role = _normalized(action.target.role)
        expected_name = _normalized(action.target.name)
        candidates: list[tuple[int, dict[str, str]]] = []
        available: list[dict[str, str]] = []
        for index, element in state.dom_state.selector_map.items():
            backend_id = getattr(element, "backend_node_id", None)
            ax = ax_by_backend.get(int(backend_id)) if backend_id is not None else None
            if not ax or ax.get("ignored"):
                continue
            role = str(_ax_value(ax, "role") or "")
            name = str(_ax_value(ax, "name") or "")
            available.append({"role": role, "name": name})
            role_matches = _normalized(role) == expected_role
            actual_name = _normalized(name)
            name_matches = (
                actual_name == expected_name
                if action.target.exact
                else expected_name in actual_name
            )
            if role_matches and name_matches:
                candidates.append((int(index), {"role": role, "name": name}))

        if len(candidates) != 1:
            raise LookupError(
                f"Semantic target role={action.target.role!r}, "
                f"name={action.target.name!r} matched {len(candidates)} elements; "
                f"available={available[:20]}"
            )
        return candidates[0]

    async def _dispatch(self, event: Any) -> None:
        session = self.native._server.browser_session
        if session is None:
            raise RuntimeError("OpenHands browser session was not initialized")
        await session.event_bus.dispatch(event)

    async def _hover(self, node: Any) -> None:
        backend_node_id = getattr(node, "backend_node_id", None)
        if backend_node_id is None:
            raise RuntimeError("The grounded DOM node has no backend node id")
        session = self.native._server.browser_session
        cdp = await session.get_or_create_cdp_session()
        box = await cdp.cdp_client.send.DOM.getBoxModel(
            params={"backendNodeId": backend_node_id}, session_id=cdp.session_id
        )
        quad = box.get("model", {}).get("content") or box.get("model", {}).get("border")
        if not quad or len(quad) < 8:
            raise RuntimeError("Could not obtain the grounded element box")
        x = sum(quad[0::2]) / 4
        y = sum(quad[1::2]) / 4
        await cdp.cdp_client.send.Input.dispatchMouseEvent(
            params={"type": "mouseMoved", "x": x, "y": y},
            session_id=cdp.session_id,
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
        self, *, scenario: str, step: str, previous_dom: str | None
    ) -> tuple[dict[str, Any], bytes]:
        state, screenshot, ax_tree = await _direct_compact_browser_state(
            self.native, include_screenshot=True
        )
        if screenshot is None:
            raise RuntimeError("compact browser capture returned no screenshot")
        session = self.native._server.browser_session
        if session is None:
            raise RuntimeError("OpenHands browser session was not initialized")
        dom = str(
            await self._evaluate(
                "document.documentElement.outerHTML", return_by_value=True
            )
            or ""
        )
        runtime = await self._evaluate(
            "(() => { const e = window.__mmcodeEvidence || "
            "{console:[],errors:[],unhandled:[]}; "
            "const out = {console:[...e.console],errors:[...e.errors],"
            "unhandled:[...e.unhandled]}; e.console.length=0; "
            "e.errors.length=0; e.unhandled.length=0; return out; })()",
            return_by_value=True,
        )
        capture_id = f"{time.time_ns()}-{_slug(scenario)}-{_slug(step)}"
        root = self.evidence_root / capture_id
        root.mkdir(parents=True, exist_ok=False)
        artifacts = {
            "screenshot": (root / "screenshot.png", screenshot),
            "dom": (root / "dom.html", dom.encode()),
            "browser_state": (
                root / "browser_state.json",
                (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode(),
            ),
            "accessibility": (
                root / "accessibility.json",
                (json.dumps(ax_tree, ensure_ascii=False, indent=2) + "\n").encode(),
            ),
            "runtime": (
                root / "runtime.json",
                (json.dumps(runtime or {}, ensure_ascii=False, indent=2) + "\n").encode(),
            ),
        }
        evidence: dict[str, Any] = {}
        for name, (path, payload) in artifacts.items():
            path.write_bytes(payload)
            evidence[name] = self._artifact(path)

        interactive = state.get("interactive_elements", [])
        record = {
            "captured_at": _utc_now(),
            "url": state.get("url") or await session.get_current_page_url(),
            "title": state.get("title"),
            "viewport": state.get("viewport"),
            "scroll": state.get("scroll"),
            "interactive_elements": interactive[:100],
            "interactive_element_count": len(interactive),
            "interactive_elements_truncated": len(interactive) > 100,
            "accessibility_tree": self._accessibility_summary(ax_tree),
            "console_delta": runtime or {},
            "dom_change": self._dom_change(previous_dom, dom),
            "evidence": evidence,
        }
        return record, screenshot

    def _record_visual_observation(
        self,
        *,
        tool: str,
        phase: str,
        record: dict[str, Any],
        screenshot: bytes,
        execution_id: str,
        scenario: str,
    ) -> None:
        screenshot_evidence = record.get("evidence", {}).get("screenshot", {})
        screenshot_hash = _sha256(screenshot)
        observation_binding = capture_observation_binding(
            self.workspace,
            self.trace_root,
            url=str(record.get("url") or ""),
            observation_succeeded=True,
        )
        self._append(
            "model_visual_observation_prepared",
            {
                "tool": tool,
                "phase": phase,
                "execution_id": execution_id,
                "scenario": scenario,
                "url": record.get("url"),
                "screenshot_sha256": screenshot_hash,
                "screenshot_path": screenshot_evidence.get("path"),
                "inline_image_content": True,
                "observation_binding": observation_binding,
            },
        )
        capture_first_application_observation(
            self.workspace,
            self.trace_root,
            framework="openhands",
            tool=tool,
            url=str(record.get("url") or ""),
            screenshot_sha256=screenshot_hash,
            evidence_path=screenshot_evidence.get("path"),
        )

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
            return {
                "changed": False,
                "before_sha256": before_hash,
                "after_sha256": after_hash,
            }
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
            "diff_preview": diff[:40],
        }

    @staticmethod
    def _accessibility_summary(nodes: list[dict[str, Any]]) -> dict[str, Any]:
        visible = []
        for node in nodes:
            if not isinstance(node, dict) or node.get("ignored"):
                continue
            role = _ax_value(node, "role")
            name = _ax_value(node, "name")
            value = _ax_value(node, "value")
            if any((role, name, value)):
                visible.append({"role": role, "name": name, "value": value})
        return {
            "nodes": visible[:100],
            "node_count": len(visible),
            "truncated": len(visible) > 100,
        }

    @staticmethod
    def _read_artifact(path: str) -> str:
        return Path(path).read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _observation(
        manifest: dict[str, Any], images: list[tuple[str, bytes]]
    ) -> BrowserPlanObservation:
        content: list[TextContent | ImageContent] = [
            TextContent(text=json.dumps(manifest, ensure_ascii=False, indent=2))
        ]
        for label, payload in images:
            content.append(TextContent(text=f"\nBrowser screenshot - {label}"))
            content.append(
                ImageContent(
                    image_urls=[
                        "data:image/png;base64,"
                        + base64.b64encode(payload).decode("ascii")
                    ]
                )
            )
        return BrowserPlanObservation(content=content, is_error=False)


class BrowserExecutePlanTool(
    ToolDefinition[BrowserExecutePlanAction, BrowserPlanObservation]
):
    name: ClassVar[str] = "browser_execute_plan"

    @classmethod
    def create(cls, executor: _BrowserPlanExecutor) -> Sequence[Self]:
        return [
            cls(
                description=(
                    "Execute one coherent functional verification episode from a "
                    "complete, precommitted plan. Call the tool again only when the "
                    "policy chooses another functional objective or replays a repaired "
                    "objective. "
                    "Use semantic accessibility role/name targets; each target is resolved "
                    "again from the latest page state immediately before its action. The "
                    "tool records a screenshot, URL, DOM change, accessibility state, and "
                    "runtime errors after every action. It never judges results or repairs code."
                ),
                action_type=BrowserExecutePlanAction,
                observation_type=BrowserPlanObservation,
                annotations=ToolAnnotations(
                    title="browser_execute_plan",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


class _ObservedBrowserGetStateExecutor(
    ToolExecutor[BrowserGetStateAction, BrowserObservation]
):
    """Record the native visual observation without changing its model payload."""

    def __init__(
        self,
        native: "BrowserToolExecutor",
        *,
        trace_root: Path,
        workspace: Path,
    ):
        self.native = native
        self.trace_root = trace_root.resolve()
        self.workspace = workspace.resolve()

    def __call__(
        self,
        action: "Vision2WebBrowserGetStateAction",
        conversation: "LocalConversation | None" = None,
    ) -> BrowserObservation:
        try:
            state, screenshot, _ = self.native._async_executor.run_async(
                _direct_compact_browser_state(
                    self.native, include_screenshot=action.include_screenshot
                ),
                timeout=120,
            )
            observation = BrowserObservation.from_text(
                text=json.dumps(state, ensure_ascii=False, indent=2),
                is_error=False,
                screenshot_data=(
                    base64.b64encode(screenshot).decode("ascii")
                    if screenshot is not None
                    else None
                ),
                full_output_save_dir=self.native.full_output_save_dir,
            )
        except Exception as exc:
            observation = BrowserObservation.from_text(
                text=f"browser_get_state failed: {type(exc).__name__}: {exc}",
                is_error=True,
                full_output_save_dir=self.native.full_output_save_dir,
            )
        payload: dict[str, Any]
        try:
            payload = json.loads(observation.text)
        except (json.JSONDecodeError, TypeError):
            payload = {}
        url = str(payload.get("url") or "")
        screenshot_hash = None
        screenshot_path = None
        inline_image = False
        if not observation.is_error and observation.screenshot_data:
            screenshot = base64.b64decode(observation.screenshot_data)
            screenshot_hash = _sha256(screenshot)
            capture_root = (
                self.trace_root
                / "evidence"
                / f"{time.time_ns()}-browser-get-state"
            )
            capture_root.mkdir(parents=True, exist_ok=False)
            screenshot_file = capture_root / "screenshot.png"
            state_file = capture_root / "browser_state.json"
            screenshot_file.write_bytes(screenshot)
            state_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            screenshot_path = str(screenshot_file)
            inline_image = any(
                isinstance(item, ImageContent) for item in observation.to_llm_content
            )
            capture_first_application_observation(
                self.workspace,
                self.trace_root,
                framework="openhands",
                tool="browser_get_state",
                url=url,
                screenshot_sha256=screenshot_hash,
                evidence_path=screenshot_path,
            )
        observation_binding = capture_observation_binding(
            self.workspace,
            self.trace_root,
            url=url,
            observation_succeeded=not observation.is_error,
        )
        append_trace_event(
            self.trace_root,
            "model_visual_observation_prepared",
            {
                "tool": "browser_get_state",
                "url": url,
                "is_error": observation.is_error,
                "text_chars": len(observation.text or ""),
                "screenshot_sha256": screenshot_hash,
                "screenshot_path": screenshot_path,
                "inline_image_content": inline_image,
                "state_source": "native_browser_session_cdp_compact",
                "observation_binding": observation_binding,
            },
            filename="browser.events.jsonl",
        )
        return observation


class Vision2WebBrowserToolSet(
    ToolDefinition[BrowserExecutePlanAction, BrowserPlanObservation]
):
    """Native navigation/state plus one batch-plan tool."""

    name: ClassVar[str] = "vision2web_browser_interfaces"

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",
        trace_root: str,
        **_: Any,
    ) -> list[ToolDefinition]:
        browser_proxy = _browser_proxy()
        native_tools = BrowserToolSet.create(
            conv_state,
            inject_scripts=[_CONSOLE_CAPTURE_SCRIPT],
            action_timeout_seconds=120,
            # Optional browser-use extensions are unrelated to this experiment
            # and require network access that ClusterX workers may not have.
            enable_default_extensions=False,
            proxy=browser_proxy,
        )
        native = BrowserToolSet._shared_executor
        if native is None:
            raise RuntimeError("OpenHands did not create its BrowserToolExecutor")
        working_dir = getattr(getattr(conv_state, "workspace", None), "working_dir", None)
        workspace = Path(
            str(working_dir or os.environ.get("MULTIMODALCODE_WORKSPACE") or Path.cwd())
        )
        executor = _BrowserPlanExecutor(
            native,
            trace_root=Path(trace_root),
            workspace=workspace,
        )
        get_state_executor = _ObservedBrowserGetStateExecutor(
            native,
            trace_root=Path(trace_root),
            workspace=workspace,
        )
        exposed_native = [
            tool
            for tool in native_tools
            if tool.name == "browser_navigate"
        ]
        if {tool.name for tool in exposed_native} != {"browser_navigate"}:
            raise RuntimeError("The frozen OpenHands native browser tools are incomplete")
        return [
            *exposed_native,
            *Vision2WebBrowserGetStateTool.create(get_state_executor),
            *BrowserExecutePlanTool.create(executor),
        ]


class Vision2WebBrowserGetStateAction(BrowserGetStateAction):
    """Native OpenHands state request with visual evidence enabled by default."""

    include_screenshot: bool = Field(
        default=True,
        description=(
            "Include a screenshot of the current page. Default: True for the "
            "Vision2Web self-verification interface."
        ),
    )


class Vision2WebBrowserGetStateTool(
    ToolDefinition[Vision2WebBrowserGetStateAction, BrowserObservation]
):
    """The native state tool projected with ``include_screenshot=True``."""

    name: ClassVar[str] = "browser_get_state"

    @classmethod
    def create(cls, executor: ToolExecutor) -> Sequence[Self]:
        return [
            cls(
                description=(
                    "Get the current URL, page state, interactive elements, and "
                    "rendered screenshot. The screenshot is included by default; "
                    "set include_screenshot=false only when visual evidence is "
                    "unnecessary."
                ),
                action_type=Vision2WebBrowserGetStateAction,
                observation_type=BrowserObservation,
                annotations=ToolAnnotations(
                    title="browser_get_state",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=True,
                ),
                executor=executor,
            )
        ]


register_tool(Vision2WebBrowserToolSet.name, Vision2WebBrowserToolSet)
