import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from multimodalcode.agent_harness import claude_code_runner


ROOT = Path(__file__).resolve().parents[1]


def test_zero_deadline_waits_for_real_process_exit(tmp_path, monkeypatch):
    waits = []
    original = subprocess.Popen

    class RecordingProcess(original):
        def wait(self, timeout=None):
            waits.append(timeout)
            return super().wait(timeout=timeout)

    monkeypatch.setattr(claude_code_runner.subprocess, "Popen", RecordingProcess)
    code, timed_out = claude_code_runner._run_attempt(
        [sys.executable, "-c", "import time; time.sleep(0.05); print('finished')"],
        workspace=tmp_path, env={}, raw_path=tmp_path / "stdout",
        stderr_path=tmp_path / "stderr", capture_path=tmp_path / "capture",
        timeout=0,
    )
    assert (code, timed_out) == (0, False)
    assert waits and waits[0] is None
    assert (tmp_path / "stdout").read_text().strip() == "finished"


@pytest.mark.parametrize("seconds", [7200, 0])
def test_submitter_passes_and_records_task_deadline(tmp_path, monkeypatch, seconds):
    spec = importlib.util.spec_from_file_location(
        "deadline_submitter", ROOT / "scripts/vision2web/submit_self_verify.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    submitted = []

    def submit(arguments):
        submitted.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "submitted")

    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(module, "selected_cases", lambda *_: ["webpage/home_instead"])
    monkeypatch.setattr(module, "server_ready", lambda *_: (True, "test endpoint"))
    monkeypatch.setattr(module, "worker_proxy_arguments", lambda: [])
    monkeypatch.setattr(module, "query", lambda *_: ("NOT_FOUND", ""))
    monkeypatch.setattr(module, "clusterx", submit)
    arguments = ["submit", "--framework", "claude_code", "--phase", "full",
                 "--modes", "official", "--run-label", "diagnostic", "--once"]
    if seconds == 0:
        arguments += ["--wall-time", "0"]
    monkeypatch.setattr(sys, "argv", arguments)
    assert module.main() == 0
    assert len(submitted) == 1 and submitted[0][-1] == str(seconds)
    state = json.loads((tmp_path / "runs/vision2web_generation/diagnostic"
                       / "controller-claude-code-full.json").read_text())
    assert state["wall_time_seconds"] == seconds
    assert len(state["jobs"]) == 1

    # Never silently change the budget of a run that has already been submitted.
    monkeypatch.setattr(sys, "argv", arguments[:arguments.index("--once")]
                        + ["--once", "--wall-time", "3600"])
    with pytest.raises(SystemExit):
        module.main()
    assert len(submitted) == 1
