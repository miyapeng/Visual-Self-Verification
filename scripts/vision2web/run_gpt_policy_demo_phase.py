#!/usr/bin/env python3
"""Archived legacy batch-interface policy demonstration.

This runner is deliberately passive. It copies the selected program into a
fresh task workspace, starts it, exposes the native OpenHands browser tools,
and persists their raw observations. It does not read workflow.json, judge an
expectation, choose an action, or modify code.
It is not part of the active official/browser_enabled/guided_vsv protocol.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.request import ProxyHandler, build_opener

from openhands.tools.browser_use.definition import BrowserNavigateAction

from multimodalcode.agent_harness.vision2web_browser_plan import (
    BrowserExecutePlanAction,
    BrowserScenario,
    Vision2WebBrowserGetStateAction,
    Vision2WebBrowserToolSet,
)
from multimodalcode.agent_harness.vision2web_trace import Vision2WebRunRecorder


def inline_image_count(observation) -> int:
    return sum(
        item.__class__.__name__ == "ImageContent"
        for item in observation.to_llm_content
    )


def decode_manifest(text: str) -> dict:
    manifest, _ = json.JSONDecoder().raw_decode(text)
    return manifest


def wait_until_reachable(url: str, attempts: int = 1200) -> int:
    opener = build_opener(ProxyHandler({}))
    for _ in range(attempts):
        try:
            with opener.open(url, timeout=1) as response:
                return response.status
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"application did not become reachable: {url}")


def prepare_workspace(source: Path, workspace: Path) -> None:
    if any(workspace.iterdir()):
        raise RuntimeError(f"expected an empty disposable workspace: {workspace}")
    shutil.copytree(source, workspace, dirs_exist_ok=True, symlinks=False)
    (workspace / "start.sh").chmod(0o755)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-workspace", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--workspace", type=Path, default=Path("/workspace"))
    args = parser.parse_args()

    source = args.source_workspace.resolve()
    output = args.output_root.resolve()
    workspace = args.workspace.resolve()
    output.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    prepare_workspace(source, workspace)

    plan = {"episodes": []}
    if args.plan:
        plan = json.loads(args.plan.resolve().read_text(encoding="utf-8"))
        if plan.get("private_workflow_used") is not False:
            raise RuntimeError("demo browser plan must explicitly deny workflow use")

    app_url = str(plan.get("app_url") or "http://127.0.0.1:3000/")
    log_path = output / "start.log"
    log = log_path.open("w", encoding="utf-8")
    env = os.environ.copy()
    for key in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    ):
        env.pop(key, None)
    server = subprocess.Popen(
        ["bash", str(workspace / "start.sh")],
        cwd=workspace,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
    )

    recorder = None
    tools = None
    result: dict = {
        "schema": "multimodalcode-gpt-policy-demo-phase-1",
        "source_workspace": str(source),
        "workflow_visible": False,
        "browser_judges": False,
        "episodes": [],
    }
    result_path = output / "result.json"
    try:
        result["http_status"] = wait_until_reachable(app_url)
        state = SimpleNamespace(
            env_observation_persistence_dir=str(output / "trace" / "openhands-observations"),
            workspace=SimpleNamespace(working_dir=str(workspace)),
        )
        recorder = Vision2WebRunRecorder(
            workspace, output / "trace", app_url=app_url, poll_seconds=0.05
        )
        recorder.start()
        tools = Vision2WebBrowserToolSet.create(
            state, trace_root=str(output / "trace")
        )
        by_name = {tool.name: tool for tool in tools}
        navigation = by_name["browser_navigate"].executor(
            BrowserNavigateAction(url=app_url)
        )
        if navigation.is_error:
            raise RuntimeError(navigation.text)
        initial = by_name["browser_get_state"].executor(
            Vision2WebBrowserGetStateAction()
        )
        result["initial_observation"] = {
            "is_error": initial.is_error,
            "text": initial.text,
            "inline_image_count": inline_image_count(initial),
            "llm_content_types": [
                item.__class__.__name__ for item in initial.to_llm_content
            ],
        }
        if initial.is_error:
            raise RuntimeError(initial.text)

        for episode_index, episode in enumerate(plan.get("episodes", [])):
            scenarios = [
                BrowserScenario.model_validate(item)
                for item in episode["scenarios"]
            ]
            observation = by_name["browser_execute_plan"].executor(
                BrowserExecutePlanAction(scenarios=scenarios)
            )
            record = {
                "episode_index": episode_index,
                "name": episode["name"],
                "policy_rationale": episode["policy_rationale"],
                "tool_is_error": observation.is_error,
                "text_chars": len(observation.text),
                "inline_image_count": inline_image_count(observation),
                "llm_content_types": [
                    item.__class__.__name__ for item in observation.to_llm_content
                ],
            }
            if not observation.is_error:
                manifest = decode_manifest(observation.text)
                record["execution_manifest"] = manifest
                record["scenario_statuses"] = [
                    row.get("status") for row in manifest.get("scenario_results", [])
                ]
            else:
                record["tool_error"] = observation.text
                record["scenario_statuses"] = []
            record["is_error"] = bool(
                observation.is_error
                or any(status != "ok" for status in record["scenario_statuses"])
            )
            result["episodes"].append(record)
            result_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        result["versions"] = recorder.finish()
        recorder = None
        result["status"] = (
            "ok" if all(not row["is_error"] for row in result["episodes"]) else "failed"
        )
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "ok" else 1
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        raise
    finally:
        if recorder is not None:
            recorder.finish()
        if tools:
            {tool.name: tool for tool in tools}[
                "browser_execute_plan"
            ].executor.close()
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
