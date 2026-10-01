#!/usr/bin/env python3
"""CPU-only smoke test for the unmodified native OpenHands BrowserToolSet."""

from __future__ import annotations

import argparse
import faulthandler
import json
import subprocess
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.request import build_opener, ProxyHandler

from openhands.tools.browser_use.definition import (
    BrowserClickAction,
    BrowserGetStateAction,
    BrowserNavigateAction,
    BrowserToolSet,
    BrowserTypeAction,
)

from multimodalcode.agent_harness.vision2web_trace import Vision2WebRunRecorder


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    progress = args.output.with_suffix(".progress.jsonl")
    stack_dump = args.output.with_suffix(".stacks.log")
    stack_handle = stack_dump.open("w", encoding="utf-8")
    faulthandler.dump_traceback_later(30, repeat=True, file=stack_handle)

    def mark(stage: str, **payload) -> None:
        with progress.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "stage": stage,
                        **payload,
                    }
                )
                + "\n"
            )

    mark("started")

    try:
        with tempfile.TemporaryDirectory(prefix="mmcode-v2w-browser-") as temporary:
            root = Path(temporary)
            site = root / "site"
            site.mkdir()
            (site / "index.html").write_text(
                """<!doctype html><html><body>
                <button id="go" onclick="document.querySelector('#result').textContent='Fixed'; console.error('probe-error')">Go</button>
                <label>Message <input id="message"></label>
                <button id="echo" onclick="document.querySelector('#echo-result').textContent=document.querySelector('#message').value">Echo</button>
                <p id="result">Waiting</p>
                <p id="echo-result">Empty</p>
                </body></html>""",
                encoding="utf-8",
            )
            server_log = (root / "server.log").open("w", encoding="utf-8")
            server = subprocess.Popen(
                ["python3.12", "-m", "http.server", "3000", "--directory", str(site)],
                stdout=server_log,
                stderr=subprocess.STDOUT,
            )
            try:
                mark("http_server_started", pid=server.pid)
                opener = build_opener(ProxyHandler({}))
                for _ in range(50):
                    try:
                        with opener.open("http://127.0.0.1:3000", timeout=1):
                            break
                    except Exception:
                        time.sleep(0.1)
                mark("http_server_ready")
                trace = args.output.parent / f"{args.output.stem}_trace"
                state = SimpleNamespace(
                    env_observation_persistence_dir=str(trace / "openhands-observations"),
                    workspace=SimpleNamespace(working_dir=str(site)),
                )
                recorder = Vision2WebRunRecorder(site, trace, poll_seconds=0.1)
                recorder.start()
                mark("creating_tools", chromium_usable=BrowserToolSet.is_usable())
                tools = BrowserToolSet.create(state)
                mark("tools_created", tools=[tool.name for tool in tools])
                by_name = {tool.name: tool for tool in tools}
                assert {
                    "browser_navigate",
                    "browser_get_state",
                    "browser_click",
                    "browser_type",
                    "browser_scroll",
                    "browser_get_content",
                    "browser_go_back",
                }.issubset(by_name)
                navigation = by_name["browser_navigate"].executor(
                    BrowserNavigateAction(url="http://127.0.0.1:3000")
                )
                assert not navigation.is_error, navigation.text
                state_observation = by_name["browser_get_state"].executor(
                    BrowserGetStateAction(include_screenshot=True)
                )
                mark("state_returned", is_error=state_observation.is_error)
                assert not state_observation.is_error, state_observation.text
                assert state_observation.screenshot_data
                assert any(
                    item.__class__.__name__ == "ImageContent"
                    for item in state_observation.to_llm_content
                )
                # The passive observer records filesystem effects regardless of
                # whether a coding policy used file_editor, shell redirection,
                # heredoc, sed, or another terminal-side writer.
                subprocess.run(
                    [
                        "sh",
                        "-c",
                        "printf '\\n<!-- after-model-observation -->\\n' >> index.html",
                    ],
                    cwd=site,
                    check=True,
                )
                time.sleep(0.3)

                def element_index(observation, text: str) -> int:
                    payload = json.loads(observation.text)
                    for element in payload.get("interactive_elements", []):
                        if text.casefold() in str(element.get("text") or "").casefold():
                            return int(element["index"])
                    raise AssertionError(f"No interactive element matching {text!r}")

                go_index = element_index(state_observation, "Go")
                click = by_name["browser_click"].executor(
                    BrowserClickAction(index=go_index)
                )
                assert not click.is_error, click.text
                after_click = by_name["browser_get_state"].executor(
                    BrowserGetStateAction(include_screenshot=True)
                )
                assert not after_click.is_error and "Fixed" in after_click.text

                message_index = element_index(after_click, "Message")
                typed = by_name["browser_type"].executor(
                    BrowserTypeAction(index=message_index, text="Mobile OK")
                )
                assert not typed.is_error, typed.text
                after_type = by_name["browser_get_state"].executor(
                    BrowserGetStateAction(include_screenshot=True)
                )
                echo_index = element_index(after_type, "Echo")
                echo = by_name["browser_click"].executor(
                    BrowserClickAction(index=echo_index)
                )
                assert not echo.is_error, echo.text
                final_state = by_name["browser_get_state"].executor(
                    BrowserGetStateAction(include_screenshot=True)
                )
                assert not final_state.is_error and "Mobile OK" in final_state.text
                result = {
                    "status": "ok",
                    "tool_names": [tool.name for tool in tools],
                    "native_state_has_screenshot": bool(
                        state_observation.screenshot_data
                    ),
                    "state_inline_image_count": sum(
                        item.__class__.__name__ == "ImageContent"
                        for observation in (state_observation, after_click, after_type, final_state)
                        for item in observation.to_llm_content
                    ),
                    "click_succeeded": "Fixed" in after_click.text,
                    "type_and_echo_succeeded": "Mobile OK" in final_state.text,
                }
                versions = recorder.finish()
                recorder = None
                result["P_final"] = versions["P_final"]
                result["shell_edit_recorded"] = "after-model-observation" in (
                    Path(versions["P_final"]["path"]) / "index.html"
                ).read_text()
                result["workspace_changes"] = sum(
                    json.loads(line).get("type") == "workspace_change"
                    for line in (trace / "workspace.events.jsonl").read_text().splitlines()
                    if line.strip()
                )
                args.output.write_text(
                    json.dumps(result, indent=2) + "\n", encoding="utf-8"
                )
                mark("result_written", output=str(args.output))
                print(json.dumps(result))
                # All native definitions share one executor; close it once.
                by_name["browser_get_state"].executor.close()
                mark("tools_closed")
            finally:
                if "recorder" in locals() and recorder is not None:
                    recorder.finish()
                server.terminate()
                server.wait(timeout=10)
                server_log.close()
                mark("http_server_stopped")
        faulthandler.cancel_dump_traceback_later()
        stack_handle.close()
        return 0
    except Exception as exc:
        record = {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
        args.output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        mark("failed", error=record["error"])
        faulthandler.cancel_dump_traceback_later()
        stack_handle.close()
        raise


if __name__ == "__main__":
    raise SystemExit(main())
