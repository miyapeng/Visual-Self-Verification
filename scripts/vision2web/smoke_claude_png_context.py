#!/usr/bin/env python3
"""Deterministic Claude Code + playwright-cli image-context smoke.

This is an infrastructure test, not an agent-behavior experiment: the prompt
explicitly requests each browser action so a pass says nothing about whether a
coding model would inspect an application proactively.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from multimodalcode.agent_harness.cases import AgentCase
from multimodalcode.agent_harness.claude_code_runner import run_claude_code


PROMPT = """This is a deterministic browser-tool infrastructure check, not a coding task.
The application is already running at http://localhost:3000.
Use Bash to run exactly /usr/bin/playwright-cli open http://localhost:3000 and /usr/bin/playwright-cli snapshot. Do not use npx and do not substitute another browser library.
Then run /usr/bin/playwright-cli screenshot --filename=/workspace/mmcode-browser-probe.png.
Use the Read tool to read /workspace/mmcode-browser-probe.png so its image content is included in your context.
Do not edit any files. After the Read tool returns, reply exactly: TOOL_CHECK_DONE
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path("/workspace"))
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    output = args.output_root.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    (workspace / "index.html").write_text(
        """<!doctype html><html><body>
        <label for="name">Name</label><input id="name">
        <button onclick="document.querySelector('#status').textContent='Clicked'">Check</button>
        <p id="status">Ready</p></body></html>""",
        encoding="utf-8",
    )
    (workspace / "start.sh").write_text(
        "#!/usr/bin/env bash\npython3.12 -m http.server 3000 --directory /workspace\n",
        encoding="utf-8",
    )
    (workspace / "start.sh").chmod(0o755)
    server_log = (output / "http-server.log").open("w", encoding="utf-8")
    server = subprocess.Popen(
        ["python3.12", "-m", "http.server", "3000", "--directory", str(workspace)],
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )
    try:
        opener = build_opener(ProxyHandler({}))
        for _ in range(50):
            try:
                with opener.open("http://127.0.0.1:3000", timeout=1):
                    break
            except Exception:
                time.sleep(0.1)
        else:
            raise RuntimeError("deterministic HTTP app did not become reachable")

        raw = output / "claude.events.jsonl"
        result = run_claude_code(
            AgentCase(
                benchmark="vision2web",
                case_id="infrastructure/claude-png-context",
                task_type="infrastructure",
                prompt=PROMPT,
            ),
            workspace,
            raw,
            output / "claude.stderr.log",
            model=args.model,
            base_url=args.base_url,
            api_key="EMPTY",
            timeout=900,
            vision2web_mode="official",
            max_retries=0,
        )
        context = result["browser_context"]
        passed = bool(
            result["status"] == "success"
            and context["model_context_image_confirmed"]
            and result["versions"]["P_first"]
        )
        record = {
            "schema": "multimodalcode-vision2web-claude-png-smoke-1",
            "status": "ok" if passed else "failed",
            "not_agent_behavior_evidence": True,
            "result": result,
        }
        (output / "result.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(record, ensure_ascii=False))
        return 0 if passed else 1
    finally:
        server.terminate()
        server.wait(timeout=10)
        server_log.close()


if __name__ == "__main__":
    raise SystemExit(main())
