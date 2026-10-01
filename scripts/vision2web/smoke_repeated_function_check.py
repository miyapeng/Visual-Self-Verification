#!/usr/bin/env python3
"""Archived legacy batch-interface smoke test.

This is an infrastructure test, not evidence that an agent chooses these actions.
The browser executes one functional episode twice and never judges correctness.
It is not part of the active official/browser_enabled/guided_vsv protocol.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.request import ProxyHandler, build_opener

from openhands.tools.browser_use.definition import BrowserNavigateAction

from multimodalcode.agent_harness.vision2web_browser_plan import (
    BrowserExecutePlanAction,
    BrowserPlannedAction,
    BrowserReset,
    BrowserScenario,
    BrowserTarget,
    Vision2WebBrowserGetStateAction,
    Vision2WebBrowserToolSet,
)
from multimodalcode.agent_harness.vision2web_trace import Vision2WebRunRecorder


URL = "http://127.0.0.1:3000/"
EXPECTATION = "After clicking Run check, the visible result reads Fixed."


def decode_manifest(observation) -> dict:
    manifest, _ = json.JSONDecoder().raw_decode(observation.text)
    return manifest


def summarize_execution(observation, manifest: dict) -> dict:
    scenario = manifest["scenario_results"][0]
    step = scenario["steps"][0]
    evidence = step["observation"]["evidence"]
    dom_path = Path(evidence["dom"]["path"])
    dom = dom_path.read_text(encoding="utf-8")
    observed = "Fixed" if ">Fixed<" in dom else "Broken" if ">Broken<" in dom else "unknown"
    return {
        "expectation": scenario["expectation"],
        "executor_status": scenario["status"],
        "action_count": len(scenario["steps"]),
        "observed_result": observed,
        "browser_judged_expectation": False,
        "url": step["observation"]["url"],
        "viewport": step["observation"]["viewport"],
        "dom_changed": step["observation"]["dom_change"]["changed"],
        "console_delta": step["observation"]["console_delta"],
        "screenshot_path": evidence["screenshot"]["path"],
        "dom_path": str(dom_path),
        "llm_content_types": [
            item.__class__.__name__ for item in observation.to_llm_content
        ],
        "inline_image_count": sum(
            item.__class__.__name__ == "ImageContent"
            for item in observation.to_llm_content
        ),
    }


def functional_episode(tool) -> tuple[object, dict]:
    observation = tool.executor(
        BrowserExecutePlanAction(
            scenarios=[
                BrowserScenario(
                    name="run-check",
                    expectation=EXPECTATION,
                    reset=BrowserReset(
                        url=URL,
                        viewport_width=960,
                        viewport_height=640,
                    ),
                    actions=[
                        BrowserPlannedAction(
                            type="click",
                            target=BrowserTarget(role="button", name="Run check"),
                        )
                    ],
                )
            ]
        )
    )
    if observation.is_error:
        raise RuntimeError(observation.text)
    manifest = decode_manifest(observation)
    return observation, manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_root.resolve()
    output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="mmcode-v2w-repeat-check-") as temporary:
        site = Path(temporary) / "workspace"
        site.mkdir()
        page = site / "index.html"
        page.write_text(
            """<!doctype html><html><body>
            <h1>Repeated functional check</h1>
            <button onclick="document.querySelector('#result').textContent='Broken'; console.error('check-probe')">Run check</button>
            <p id="result">Waiting</p>
            </body></html>""",
            encoding="utf-8",
        )
        start = site / "start.sh"
        start.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "exec python3.12 -m http.server 3000 --directory \"$(dirname \"$0\")\"\n",
            encoding="utf-8",
        )
        start.chmod(0o755)

        log_path = output / "start.log"
        log = log_path.open("w", encoding="utf-8")
        env = os.environ.copy()
        for key in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ):
            env.pop(key, None)
        server = subprocess.Popen(
            ["bash", str(start)],
            cwd=site,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
        )

        recorder = None
        tools = None
        try:
            opener = build_opener(ProxyHandler({}))
            http_status = None
            for _ in range(60):
                try:
                    with opener.open(URL, timeout=1) as response:
                        http_status = response.status
                    break
                except Exception:
                    time.sleep(0.1)
            if http_status != 200:
                raise RuntimeError("start.sh did not make localhost:3000 reachable")

            trace = output / "trace"
            state = SimpleNamespace(
                env_observation_persistence_dir=str(trace / "openhands-observations"),
                workspace=SimpleNamespace(working_dir=str(site)),
            )
            recorder = Vision2WebRunRecorder(site, trace, poll_seconds=0.05)
            recorder.start()
            tools = Vision2WebBrowserToolSet.create(state, trace_root=str(trace))
            by_name = {tool.name: tool for tool in tools}

            navigation = by_name["browser_navigate"].executor(
                BrowserNavigateAction(url=URL)
            )
            if navigation.is_error:
                raise RuntimeError(navigation.text)
            initial = by_name["browser_get_state"].executor(
                Vision2WebBrowserGetStateAction()
            )
            if initial.is_error:
                raise RuntimeError(initial.text)

            before_observation, before_manifest = functional_episode(
                by_name["browser_execute_plan"]
            )
            before = summarize_execution(before_observation, before_manifest)

            source = page.read_text(encoding="utf-8")
            page.write_text(source.replace("textContent='Broken'", "textContent='Fixed'"), encoding="utf-8")
            time.sleep(0.2)

            after_observation, after_manifest = functional_episode(
                by_name["browser_execute_plan"]
            )
            after = summarize_execution(after_observation, after_manifest)

            versions = recorder.finish()
            recorder = None
            result = {
                "schema": "multimodalcode-repeated-functional-check-1",
                "status": "ok",
                "not_agent_behavior_evidence": True,
                "deployment": {
                    "model_available_command": "bash /workspace/start.sh",
                    "tested_command": ["bash", str(start)],
                    "url": URL,
                    "http_status": http_status,
                    "server_pid": server.pid,
                    "log": str(log_path),
                },
                "initial_observation": {
                    "screenshot_bytes": len(initial.screenshot_data or ""),
                    "llm_content_types": [
                        item.__class__.__name__ for item in initial.to_llm_content
                    ],
                    "inline_image_count": sum(
                        item.__class__.__name__ == "ImageContent"
                        for item in initial.to_llm_content
                    ),
                },
                "check_before_edit": before,
                "simulated_policy_edit": {
                    "path": "index.html",
                    "change": "button result: Broken -> Fixed",
                },
                "check_after_edit": after,
                "same_committed_expectation": (
                    before["expectation"] == after["expectation"] == EXPECTATION
                ),
                "versions": versions,
                "policy_boundary": {
                    "browser_only_executes_and_records": True,
                    "browser_does_not_decide_pass_or_repair": True,
                    "the_edit_is_scripted_for_infrastructure_testing_only": True,
                },
            }
            if before["observed_result"] != "Broken":
                raise AssertionError(before)
            if after["observed_result"] != "Fixed":
                raise AssertionError(after)
            if before["inline_image_count"] < 1 or after["inline_image_count"] < 1:
                raise AssertionError("each functional check must return an inline image")
            result_path = output / "result.json"
            result_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(json.dumps(result, ensure_ascii=False))
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
