#!/usr/bin/env python3
"""Archived batch-interface capacity probe for historical evidence.

This deterministic probe proves that the development environment can deploy a
generated application, execute one dataset-authored functional objective,
return visual/runtime evidence, observe a workspace edit, and replay the same
objective. It does not call a model and is not evidence of agent initiative.
It is not part of the active official/browser_enabled/guided_vsv protocol.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decode_manifest(observation) -> dict:
    manifest, _ = json.JSONDecoder().raw_decode(observation.text)
    return manifest


def inline_image_count(observation) -> int:
    return sum(
        item.__class__.__name__ == "ImageContent"
        for item in observation.to_llm_content
    )


def relative_artifact(path: str, output_root: Path) -> str:
    target = Path(path).resolve()
    try:
        return target.relative_to(output_root.resolve()).as_posix()
    except ValueError:
        return str(target)


def active_page(dom: str, page_id: str) -> bool:
    pattern = re.compile(
        rf"<[^>]+\bid=[\"']{re.escape(page_id)}[\"'][^>]+\bclass=[\"'][^\"']*\bactive\b[^\"']*[\"']",
        re.IGNORECASE,
    )
    reverse_pattern = re.compile(
        rf"<[^>]+\bclass=[\"'][^\"']*\bactive\b[^\"']*[\"'][^>]+\bid=[\"']{re.escape(page_id)}[\"']",
        re.IGNORECASE,
    )
    return bool(pattern.search(dom) or reverse_pattern.search(dom))


def evaluate_saved_evidence(
    assertion: dict, *, dom: str, url: str, console: object
) -> tuple[bool, dict]:
    kind = assertion["type"]
    if kind == "active_page":
        page_id = assertion["page_id"]
        observed = {"active_page": page_id, "matched": active_page(dom, page_id)}
        return observed["matched"], observed
    if kind == "url_and_text":
        url_token = assertion["url_contains"]
        text = assertion["text_contains"]
        observed = {
            "url_contains": url_token,
            "url_matched": url_token in url,
            "text_contains": text,
            "text_matched": text in dom,
        }
        return bool(observed["url_matched"] and observed["text_matched"]), observed
    raise ValueError(f"unsupported evidence assertion: {kind}")


def summarize_execution(
    observation,
    manifest: dict,
    output_root: Path,
    evidence_assertion: dict,
) -> dict:
    scenario = manifest["scenario_results"][0]
    step = scenario["steps"][-1]
    evidence = step["observation"]["evidence"]
    dom_path = Path(evidence["dom"]["path"])
    dom = dom_path.read_text(encoding="utf-8")
    console = step["observation"].get("console_delta") or []
    oracle_verdict, oracle_observation = evaluate_saved_evidence(
        evidence_assertion,
        dom=dom,
        url=step["observation"]["url"],
        console=console,
    )
    result = {
        "expectation": scenario["expectation"],
        "executor_status": scenario["status"],
        "action_count": len(scenario["steps"]),
        "oracle_observation": oracle_observation,
        "oracle_verdict_from_saved_evidence": oracle_verdict,
        "browser_judged_expectation": False,
        "url": step["observation"]["url"],
        "dom_changed": step["observation"]["dom_change"]["changed"],
        "console_delta": console,
        "screenshot_path": evidence["screenshot"]["path"],
        "screenshot_relative": relative_artifact(
            evidence["screenshot"]["path"], output_root
        ),
        "dom_path": str(dom_path),
        "dom_relative": relative_artifact(str(dom_path), output_root),
        "inline_image_count": inline_image_count(observation),
        "llm_content_types": [
            item.__class__.__name__ for item in observation.to_llm_content
        ],
    }
    return result


def wait_until_reachable(url: str, attempts: int = 1200) -> int:
    opener = build_opener(ProxyHandler({}))
    for _ in range(attempts):
        try:
            with opener.open(url, timeout=1) as response:
                return response.status
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"application did not become reachable: {url}")


def execute_scenario(
    tool,
    scenario: BrowserScenario,
    output_root: Path,
    evidence_assertion: dict,
) -> dict:
    observation = tool.executor(BrowserExecutePlanAction(scenarios=[scenario]))
    if observation.is_error:
        raise RuntimeError(observation.text)
    manifest = decode_manifest(observation)
    return summarize_execution(
        observation, manifest, output_root, evidence_assertion
    )


def prepare_workspace(source: Path, case_root: Path, workspace: Path) -> None:
    if any(workspace.iterdir()):
        raise RuntimeError(
            f"capacity probe requires an empty disposable workspace: {workspace}"
        )
    shutil.copytree(source, workspace, dirs_exist_ok=True)
    resources = case_root / "resources"
    # Historical workspaces may already contain the benchmark-provided
    # resources (often through a symlink). Do not copy the same input twice.
    if resources.is_dir() and not (workspace / "resources").exists():
        shutil.copytree(resources, workspace / "resources")
    (workspace / "start.sh").chmod(0o755)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, default=Path("/workspace"))
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    config_path = args.config.resolve()
    output_root = args.output_root.resolve()
    workspace = args.workspace.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)

    config = read_json(config_path)
    source = (project_root / config["source_workspace"]).resolve()
    workflow_path = (project_root / config["private_workflow"]).resolve()
    case_root = workflow_path.parent
    workflow = read_json(workflow_path)
    selector = config["workflow_selector"]
    group = workflow[selector["group_index"]]
    objective = group["content"][selector["content_index"]]
    if objective["objective"] != selector["expected_objective"]:
        raise RuntimeError("frozen workflow objective no longer matches probe config")
    if config["information_boundary"]["workflow_visible_to_agent_or_model"]:
        raise RuntimeError("capacity probe must keep workflow private")

    prepare_workspace(source, case_root, workspace)
    scenario_payload = dict(config["semantic_scenario"])
    scenario_payload["expectation"] = " ".join(objective["validations"])
    scenario = BrowserScenario.model_validate(scenario_payload)

    trace = output_root / "trace"
    log_path = output_root / "start.log"
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
        ["bash", str(workspace / "start.sh")],
        cwd=workspace,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
    )

    recorder = None
    tools = None
    try:
        status = wait_until_reachable(scenario.reset.url)
        state = SimpleNamespace(
            env_observation_persistence_dir=str(trace / "openhands-observations"),
            workspace=SimpleNamespace(working_dir=str(workspace)),
        )
        recorder = Vision2WebRunRecorder(
            workspace, trace, app_url=scenario.reset.url, poll_seconds=0.05
        )
        recorder.start()
        tools = Vision2WebBrowserToolSet.create(state, trace_root=str(trace))
        by_name = {tool.name: tool for tool in tools}

        navigation = by_name["browser_navigate"].executor(
            BrowserNavigateAction(url=scenario.reset.url)
        )
        if navigation.is_error:
            raise RuntimeError(navigation.text)
        initial = by_name["browser_get_state"].executor(
            Vision2WebBrowserGetStateAction()
        )
        if initial.is_error:
            raise RuntimeError(initial.text)

        before = execute_scenario(
            by_name["browser_execute_plan"],
            scenario,
            output_root,
            config["evidence_assertion"],
        )

        repair = config["manual_repair"]
        repair_path = workspace / repair["relative_path"]
        source_text = repair_path.read_text(encoding="utf-8")
        pre_repair_sha256 = sha256(repair_path)
        replacements = repair.get("replacements") or [
            {"old": repair["old"], "new": repair["new"]}
        ]
        for replacement in replacements:
            occurrences = source_text.count(replacement["old"])
            if occurrences != 1:
                raise RuntimeError(
                    "manual repair anchor count must be one, found "
                    f"{occurrences}: {replacement['old'][:120]!r}"
                )
            source_text = source_text.replace(
                replacement["old"], replacement["new"], 1
            )
        repair_path.write_text(source_text, encoding="utf-8")
        post_repair_sha256 = sha256(repair_path)
        time.sleep(0.2)

        # The generated server does not emit cache-control headers. Keep the
        # committed URL, expectation, and actions unchanged, but ask the same
        # native Chromium session to bypass its cache for the replay.
        after_scenario = scenario.model_copy(
            update={
                "reset": scenario.reset.model_copy(
                    update={"bypass_cache": True}
                )
            },
            deep=True,
        )
        after = execute_scenario(
            by_name["browser_execute_plan"],
            after_scenario,
            output_root,
            config["evidence_assertion"],
        )
        versions = recorder.finish()
        recorder = None

        succeeded = bool(
            not before["oracle_verdict_from_saved_evidence"]
            and after["oracle_verdict_from_saved_evidence"]
            and before["expectation"] == after["expectation"]
            and before["inline_image_count"] >= 2
            and after["inline_image_count"] >= 2
            and versions.get("P_first")
        )
        result = {
            "schema": "multimodalcode-vision2web-real-case-capacity-probe-1",
            "status": "ok" if succeeded else "failed",
            "case_id": config["case_id"],
            "level": config["level"],
            "not_agent_behavior_evidence": True,
            "workflow_oracle": {
                "path": str(workflow_path),
                "sha256": sha256(workflow_path),
                "group_index": selector["group_index"],
                "content_index": selector["content_index"],
                "objective": objective["objective"],
                "actions": objective["actions"],
                "validations": objective["validations"],
                "visible_to_agent_or_model": False,
            },
            "deployment": {
                "command": ["bash", "/workspace/start.sh"],
                "url": scenario.reset.url,
                "http_status": status,
                "log": str(log_path),
            },
            "initial_observation": {
                "inline_image_count": inline_image_count(initial),
                "llm_content_types": [
                    item.__class__.__name__ for item in initial.to_llm_content
                ],
            },
            "check_before_repair": before,
            "manual_repair": {
                "path": repair["relative_path"],
                "description": repair["description"],
                "pre_sha256": pre_repair_sha256,
                "post_sha256": post_repair_sha256,
            },
            "check_after_repair": after,
            "same_committed_expectation": (
                before["expectation"] == after["expectation"]
            ),
            "versions": versions,
            "boundaries": config["information_boundary"],
        }
        result_path = output_root / "result.json"
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if before["oracle_verdict_from_saved_evidence"]:
            raise AssertionError("expected the frozen generated version to fail")
        if not after["oracle_verdict_from_saved_evidence"]:
            raise AssertionError("manual repair did not satisfy the replayed check")
        if before["inline_image_count"] < 2 or after["inline_image_count"] < 2:
            raise AssertionError("reset and action screenshots must enter tool output")
        if not versions.get("P_first"):
            raise AssertionError("first deployed observation was not checkpointed")
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
