from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

from multimodalcode.io import read_json, write_json


def _safe_workspace(path: str | Path) -> Path:
    workspace = Path(path).resolve()
    if not workspace.is_dir():
        raise FileNotFoundError(workspace)
    if workspace in {Path("/"), Path("/data"), Path("/workspace")}:
        raise ValueError(f"Refusing broad replay workspace: {workspace}")
    return workspace


def _wait_ready(url: str, timeout: float) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=1) as response:
                if response.status < 500:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"Application did not become ready: {url}")


def _locator(page: Any, target: dict[str, Any]) -> Any:
    if target.get("selector"):
        return page.locator(str(target["selector"]))
    role = target.get("role")
    name = target.get("name")
    if not role or name is None:
        raise ValueError(f"Target needs selector or role/name: {target}")
    return page.get_by_role(
        str(role), name=str(name), exact=bool(target.get("exact", True))
    )


def _capture(page: Any, output: Path, name: str, console: list[dict[str, str]]) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    screenshot = output / f"{name}.png"
    dom = output / f"{name}.html"
    page.screenshot(path=str(screenshot), full_page=True)
    content = page.content()
    dom.write_text(content, encoding="utf-8")
    return {
        "url": page.url,
        "title": page.title(),
        "screenshot": str(screenshot),
        "screenshot_sha256": hashlib.sha256(screenshot.read_bytes()).hexdigest(),
        "dom": str(dom),
        "dom_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "console": list(console),
    }


def _perform(page: Any, action: dict[str, Any]) -> None:
    kind = str(action.get("type") or "")
    target = action.get("target") or {}
    if kind == "navigate":
        page.goto(str(action["url"]), wait_until="domcontentloaded")
    elif kind == "click":
        _locator(page, target).click()
    elif kind == "fill":
        _locator(page, target).fill(str(action.get("value") or ""))
    elif kind == "hover":
        _locator(page, target).hover()
    elif kind == "press":
        if target:
            _locator(page, target).press(str(action["key"]))
        else:
            page.keyboard.press(str(action["key"]))
    elif kind == "select":
        _locator(page, target).select_option(label=str(action.get("value") or ""))
    elif kind == "scroll":
        amount = int(action.get("amount") or 700)
        direction = str(action.get("direction") or "down")
        dx = amount if direction == "right" else -amount if direction == "left" else 0
        dy = amount if direction == "down" else -amount if direction == "up" else 0
        page.mouse.wheel(dx, dy)
    elif kind == "go_back":
        page.go_back(wait_until="domcontentloaded")
    elif kind == "wait":
        page.wait_for_timeout(float(action.get("seconds") or 0.5) * 1000)
    else:
        raise ValueError(f"Unsupported replay action: {kind}")


def _assertion(page: Any, rule: dict[str, Any]) -> dict[str, Any]:
    kind = str(rule.get("type") or "")
    passed = False
    observed: Any = None
    if kind == "url_contains":
        observed = page.url
        passed = str(rule.get("value") or "") in observed
    elif kind == "text_visible":
        observed = str(rule.get("value") or "")
        passed = page.get_by_text(observed, exact=bool(rule.get("exact", False))).first.is_visible()
    elif kind == "count":
        observed = _locator(page, rule.get("target") or {}).count()
        expected = int(rule.get("value", 0))
        operator = str(rule.get("operator") or "eq")
        passed = {
            "eq": observed == expected,
            "ge": observed >= expected,
            "gt": observed > expected,
            "le": observed <= expected,
            "lt": observed < expected,
        }.get(operator, False)
    elif kind == "text_equals":
        observed = _locator(page, rule.get("target") or {}).inner_text().strip()
        passed = observed == str(rule.get("value") or "").strip()
    else:
        raise ValueError(f"Unsupported replay assertion: {kind}")
    return {"rule": rule, "observed": observed, "passed": passed}


def _execute_plans(app_url: str, plans: list[dict[str, Any]], output: Path) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Replay requires: pip install playwright && python -m playwright install chromium") from exc
    rows: list[dict[str, Any]] = []
    images: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for plan_index, plan in enumerate(plans):
                reset = plan.get("reset") or {}
                viewport = plan.get("viewport") or {
                    "width": int(reset.get("viewport_width") or 1440),
                    "height": int(reset.get("viewport_height") or 900),
                }
                context = browser.new_context(
                    viewport=viewport
                )
                page = context.new_page()
                console: list[dict[str, str]] = []
                page.on("console", lambda message: console.append({"type": message.type, "text": message.text}))
                plan_row = {
                    "name": str(plan.get("name") or f"plan-{plan_index}"),
                    "kind": str(plan.get("kind") or "target"),
                    "workflow_id": plan.get("workflow_id"),
                    "status": "pass",
                    "steps": [],
                    "assertions": [],
                }
                try:
                    reset_url = str(plan.get("reset_url") or reset.get("url") or app_url)
                    page.goto(reset_url, wait_until="domcontentloaded")
                    initial = _capture(page, output, f"{plan_index:02d}-reset", console)
                    images.append(initial["screenshot"])
                    for action_index, action in enumerate(plan.get("actions") or []):
                        _perform(page, action)
                        page.wait_for_timeout(100)
                        evidence = _capture(
                            page, output, f"{plan_index:02d}-{action_index:02d}-{action.get('type','action')}", console
                        )
                        images.append(evidence["screenshot"])
                        plan_row["steps"].append({"action": action, "status": "pass", "evidence": evidence})
                    plan_row["assertions"] = [
                        _assertion(page, rule) for rule in plan.get("assertions") or []
                    ]
                    if any(not row["passed"] for row in plan_row["assertions"]):
                        plan_row["status"] = "assertion_failed"
                except Exception as exc:
                    plan_row["status"] = "execution_failed"
                    plan_row["error"] = f"{type(exc).__name__}: {exc}"
                finally:
                    context.close()
                rows.append(plan_row)
        finally:
            browser.close()
    return {
        "plans": rows,
        "images": images,
        "mechanical_execution_passed": all(row["status"] != "execution_failed" for row in rows),
    }


def replay_workspace(
    workspace: str | Path,
    specification: dict[str, Any],
    output: str | Path,
    *,
    app_url: str = "http://127.0.0.1:3000/",
    start_command: str = "bash start.sh",
    ready_timeout: float = 120,
) -> dict[str, Any]:
    root = _safe_workspace(workspace)
    destination = Path(output).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    log_path = destination / "server.log"
    log = log_path.open("w", encoding="utf-8")
    env = os.environ.copy()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    process = subprocess.Popen(
        ["bash", "-lc", start_command],
        cwd=root,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    try:
        _wait_ready(app_url, ready_timeout)
        result = _execute_plans(app_url, specification.get("plans") or [], destination / "evidence")
        result.update({"workspace": str(root), "app_url": app_url, "server_log": str(log_path)})
        write_json(destination / "result.json", result)
        return result
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        log.close()


def compare_replays(
    episode_id: str,
    before: dict[str, Any],
    after: dict[str, Any] | None,
) -> dict[str, Any]:
    before_plans = before.get("plans") or []
    after_plans = (after or {}).get("plans") or []
    target_after = [row for row in after_plans if row.get("kind") == "target"]
    target_before = [row for row in before_plans if row.get("kind") == "target"]
    regression_after = [row for row in after_plans if row.get("kind") == "regression"]
    target_with_assertions = [row for row in target_after if row.get("assertions")]
    def rules(row):
        return [a.get("rule") for a in row.get("assertions") or []]

    comparable = bool(target_with_assertions and len(target_before) == len(target_after)
                      and len(target_with_assertions) == len(target_after)) and all(
        b.get("name") == a.get("name") and b.get("assertions")
        and rules(b) == rules(a) and all(r is not None for r in rules(a))
        and b.get("status") in {"pass", "assertion_failed"} and a.get("status") in {"pass", "assertion_failed"}
        and all(type(x.get("passed")) is bool for r in (a, b) for x in r["assertions"])
        for b, a in zip(target_before, target_after)
    )
    before_failed = comparable and any(x.get("passed") is False for r in target_before for x in r["assertions"])
    target_fixed = (all(a.get("status") == "pass" and all(x.get("passed") is True for x in a["assertions"])
                        for a in target_after) if before_failed else None)
    def regression_baseline(row):
        matches = [b for b in before_plans if b.get("kind") == "regression"
                   and b.get("workflow_id") == row.get("workflow_id") and b.get("name") == row.get("name")]
        return bool(len(matches) == 1 and row.get("assertions") and rules(matches[0]) == rules(row)
                    and all(r is not None for r in rules(row)) and matches[0].get("status") == "pass"
                    and all(x.get("passed") is True for x in matches[0].get("assertions") or []))
    return {
        "schema": "multimodalcode-vsv-replay-1",
        "episodes": {
            episode_id: {
                "action_replay": {
                    "status": "pass" if before.get("mechanical_execution_passed") else "fail",
                    "reason": "Exact declarative action plan replay on P_before",
                },
                "target": {
                    "fixed": target_fixed,
                    "comparable_before_after": comparable,
                    "before_failure_demonstrated": bool(before_failed),
                    "reason": (
                        "Same target assertions fail on P_before and pass on P_after"
                        if target_fixed is True
                        else "Target assertions fail on P_after"
                        if target_fixed is False
                        else "No comparable, demonstrated target failure on P_before"
                    ),
                },
                "regressions": [
                    {
                        "workflow_id": row.get("workflow_id"),
                        "before_passed": regression_baseline(row),
                        "passed": (
                            all(assertion.get("passed") is True for assertion in row["assertions"])
                            if regression_baseline(row) and row.get("status") == "pass" and row.get("assertions")
                            else False if regression_baseline(row) and row.get("status") == "assertion_failed"
                            else None
                        ),
                    }
                    for row in regression_after
                ],
                "before_images": before.get("images") or [],
                "after_images": (after or {}).get("images") or [],
                "before": before_plans,
                "after": after_plans,
            }
        },
    }
