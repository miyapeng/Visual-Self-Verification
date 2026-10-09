"""Construct fresh-session verification tasks from recoverable pre-check code.

No inference, workflow inspection, bug labels, or repair answers. A built task
is only a candidate until its original launch method passes a runtime probe.
"""
from __future__ import annotations

import hashlib
import fnmatch
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
from pathlib import Path
from typing import Any

from multimodalcode.agent_harness.cases import AgentCase, load_case
from multimodalcode.agent_harness.vision2web_trace import manifest_hash, utc_now
from multimodalcode.io import read_json, write_json
from .checkpoints import checkpoint_index
from .checks import action_groups
from .episodes import _time, extract_episodes

ROOT = Path(__file__).resolve().parents[3]
PROMPT = ROOT / "configs/prompts/vision2web/checkpoint_verify.txt"
_HISTORY_DIRS = {".git", ".claude", ".playwright-cli", ".mmcode", "test_results"}
_PRIVATE_NAMES = {"workflow.json", "scores.json", "trajectory.json", ".mmcode_case.json"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(name: str) -> Path:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.as_posix() != name:
        raise ValueError(f"Unsafe checkpoint path: {name}")
    return path


def _history(name: str) -> bool:
    path = _relative(name)
    return bool(set(path.parts) & _HISTORY_DIRS) or path.name in _PRIVATE_NAMES


def _screenshot_outputs(command: str) -> list[str]:
    """Literal CLI outputs/simple shell variables only; never execute shell text."""
    assignments = dict(re.findall(r"\b([A-Za-z_]\w*)=[\"']([^\"'\n]+)[\"']", command))
    patterns = []
    for quoted, single, plain in re.findall(
        r"--(?:screenshot|filename)(?:=|\s+)(?:\"([^\"]+)\"|'([^']+)'|([^\s;]+))", command
    ):
        value = quoted or single or plain
        variable = re.fullmatch(r"\$(?:\{(\w+)\}|(\w+))", value)
        if variable:
            value = assignments.get(variable[1] or variable[2], "")
        if value.startswith("/workspace/"):
            patterns.append(re.sub(r"\$\{[^}]+\}|\$[A-Za-z_]\w*", "*",
                                   value.removeprefix("/workspace/")))
    return patterns


def _manifest(checkpoint: dict, expected: str) -> tuple[dict, Path, bool]:
    """Read an exact recorded manifest, never substitute the nearest/final tree."""
    workspace = checkpoint.get("workspace")
    if workspace:
        root = Path(workspace)
        sidecar = root.with_suffix(".json")
        if sidecar.is_file():
            manifest = read_json(sidecar)["files"]
        else:
            versions = read_json(root.parent.parent / "versions.json")
            manifest = versions[checkpoint["source"]]["files"]
    else:
        root = Path(checkpoint["object_root"])
        manifest = read_json(checkpoint["manifest"])
    if manifest_hash(manifest) != expected:
        raise ValueError("recorded_manifest_hash_mismatch")
    for name, row in manifest.items():
        _relative(name)
        if not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", ""))):
            raise ValueError("invalid_file_digest")
    return manifest, root, bool(workspace)


def _source_file(root: Path, name: str, row: dict, is_workspace: bool) -> Path:
    digest = row["sha256"]
    source = root / name if is_workspace else root / digest[:2] / digest
    if not source.resolve().is_relative_to(root.resolve()) or source.is_symlink():
        raise ValueError(f"unsafe_source_file: {name}")
    if not source.is_file() or sha256(source) != digest:
        raise ValueError(f"missing_or_corrupt_source: {name}")
    return source


def _changes(run: dict) -> list[dict]:
    development = run.get("development") or {}
    raw = Path(development.get("root") or ".") / "workspace.events.jsonl"
    # Unlike the analysis projection, use ALL recorded file changes, including
    # README/start.sh. Otherwise the pre-check hash may name the wrong tree.
    if raw.is_file():
        rows = [json.loads(line) for line in raw.read_text().splitlines() if line.strip()]
    else:
        rows = development.get("events") or []
    return sorted(rows, key=lambda row: _time(row.get("timestamp")))


def _before(event: dict, changes: list[dict]) -> str | None:
    if not event.get("timestamp"):
        return None
    digest = None
    for row in changes:
        if _time(row.get("timestamp")) >= _time(event["timestamp"]):
            break
        payload = row.get("payload") or {}
        if row.get("type") == "recorder_started":
            digest = payload.get("initial_program_sha256")
        elif row.get("type") == "workspace_change":
            digest = payload.get("program_sha256")
    return digest


def _public_files(case: AgentCase) -> dict[str, Path]:
    assert case.source_dir is not None
    result = {}
    for directory in ("prototypes", "resources"):
        for source in sorted((case.source_dir / directory).rglob("*")):
            if source.is_file():
                if not source.resolve().is_relative_to(case.source_dir.resolve()):
                    raise ValueError("Public input symlink leaves task directory")
                result[source.relative_to(case.source_dir).as_posix()] = source
    name = {"frontend": "prompt.txt", "website": "prd.md"}.get(case.task_type)
    if name:
        result[name] = case.source_dir / name
    return result


def _launch(launch: dict, files: dict) -> tuple[list[str], str]:
    command, cwd = launch["command"], launch.get("cwd", ".")
    if (not isinstance(command, list) or not command
            or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in command)):
        raise ValueError("invalid_launch_command")
    if cwd != ".":
        _relative(cwd)
        if not any(name.startswith(cwd + "/") for name in files):
            raise ValueError("launch_directory_missing")
    if command == ["bash", "/workspace/start.sh"] and "start.sh" not in files:
        raise ValueError("no_start_sh_at_check")
    return command, cwd


def _audited_sources(config_path: Path | None, run_json: Path, run: dict) -> tuple[dict, dict | None]:
    """Opt-in reviewed application snapshots, NOT an approximate whole-workspace fallback.

    The sidecar pins both the trajectory and reconstruction. Any tool calls
    between the version boundary and check must be explicitly reviewed as
    non-mutating. No inference or shell replay is used to guess missing files.
    """
    if config_path is None:
        return {}, None
    config = read_json(config_path)
    source_hash = sha256(run_json)
    if config["source_run_sha256"] != source_hash:
        raise ValueError("audited_source_run_mismatch")
    path = Path(config["reconstruction"])
    if not path.is_absolute():
        path = ROOT / path
    if sha256(path) != config["reconstruction_sha256"]:
        raise ValueError("audited_reconstruction_changed")
    reconstruction = read_json(path)
    if reconstruction["source_run_sha256"] != source_hash:
        raise ValueError("reconstruction_source_run_mismatch")
    versions = {row["version"]: row for row in reconstruction["versions"]}
    events = {e["ordinal"]: e for e in run["timeline"]}
    launch = config["launch"]
    action, result = events[launch["action_ordinal"]], events[launch["result_ordinal"]]
    call_id = action.get("tool_call_id")
    if (not call_id or result.get("kind") != "observation" or result.get("is_error")
            or (result.get("payload") or {}).get("tool_use_id") != call_id
            or shlex.join(launch["command"]) not in (action.get("payload") or {}).get("command", "")):
        raise ValueError("launch_not_supported_by_recorded_call")
    prefix = _relative(config["code_subdir"])
    sources = {}
    for entry in config["checkpoints"]:
        ordinal = entry["action_ordinal"]
        version = versions[entry["version"]]
        boundary = version["after_event"]
        boundary_event = events[boundary]
        if boundary_event.get("kind") == "action":
            replies = [e for e in run["timeline"] if e.get("kind") == "observation"
                       and (e.get("payload") or {}).get("tool_use_id") == boundary_event.get("tool_call_id")]
            if len(replies) != 1 or replies[0].get("is_error"):
                raise ValueError("version_boundary_not_completed")
            boundary = replies[0]["ordinal"]
        if boundary >= ordinal or launch["result_ordinal"] >= ordinal:
            raise ValueError("checkpoint_or_launch_after_check")
        intervening = [e for e in run["timeline"] if boundary < e["ordinal"] < ordinal
                       and e.get("kind") == "action"]
        reviewed = set(entry.get("reviewed_non_mutating_actions", []))
        if ({e["ordinal"] for e in intervening} != reviewed
                or any(e.get("category") == "edit" for e in intervening)):
            raise ValueError("unreviewed_actions_between_checkpoint_and_check")
        source_root = Path(version["workspace"])
        manifest = {}
        code_files = version["code_manifest"]["files"]
        code_digest = hashlib.sha256(json.dumps(code_files, sort_keys=True).encode()).hexdigest()
        if code_digest != version["code_manifest"]["sha256"]:
            raise ValueError("audited_code_manifest_mismatch")
        for name, digest in code_files.items():
            relative = (prefix / _relative(name)).as_posix()
            source = _source_file(source_root, relative, {"sha256": digest}, True)
            manifest[relative] = {"sha256": digest, "bytes": source.stat().st_size,
                                  "mode": source.stat().st_mode & 0o777}
        # Symlinked original resources are copied from the public task, never
        # followed into an old workspace. All other unlisted code is rejected.
        aliases = config.get("public_mappings", {})
        for destination, public_name in aliases.items():
            _relative(destination)
            if public_name not in {"resources", "prototypes"}:
                raise ValueError("only_original_public_materials_can_be_mapped")
        for source in (source_root / prefix).rglob("*"):
            relative = source.relative_to(source_root).as_posix()
            if relative in aliases:
                continue
            if (source.is_file() or source.is_symlink()) and relative not in manifest and not _history(relative):
                raise ValueError(f"unlisted_audited_source: {relative}")
        if ordinal in sources:
            raise ValueError("duplicate_audited_check_action")
        sources[ordinal] = {"manifest": manifest, "root": source_root,
            "checkpoint": {"kind": "audited_application", "version": version["version"],
                "after_event": version["after_event"], "action_ordinal": ordinal,
                "workspace": str(source_root), "scope": reconstruction["scope"],
                "reconstruction": str(path), "reconstruction_sha256": sha256(path),
                "config": str(config_path.resolve()), "config_sha256": sha256(config_path),
                "public_mappings": aliases, "launch_evidence": launch}}
    return sources, launch


def build_tasks(run_json: Path, output: Path, *, case: AgentCase | None = None,
                action_ordinals: set[int] | None = None, sources_config: Path | None = None) -> dict[str, Any]:
    """One sample per distinct pre-check code hash; keep all origin call IDs."""
    run_json, output = run_json.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError(output)
    run = read_json(run_json)
    if not isinstance(run.get("timeline"), list):
        raise ValueError("Expected normalized run.json with timeline and development")
    case = case or load_case("vision2web", run["case_id"])
    if case.benchmark != "vision2web" or case.case_id != run["case_id"]:
        raise ValueError("Source trajectory and public task must match")
    episodes = extract_episodes(run_json)["episodes"]
    groups = [(episode, group) for episode in episodes for group in action_groups(episode)]
    # Close/cleanup and status-only calls inside a broad span are not check starts.
    groups = [(e, g) for e, g in groups if g["execution"]["evidence_returned"] and
              (e["verification_kind"] == "functional" or g["execution"]["image_consumed_ordinals"])]
    known = {g["action_ordinal"] for _, g in groups}
    audited, audited_launch = _audited_sources(sources_config, run_json, run)
    if audited and not set(audited) <= known:
        raise ValueError("Audited mapping must refer to extracted check actions")
    if audited and action_ordinals is None:
        action_ordinals = set(audited)
    if action_ordinals and not action_ordinals <= known:
        raise ValueError(f"Not an extracted check action: {sorted(action_ordinals - known)}")
    if action_ordinals:
        groups = [(e, g) for e, g in groups if g["action_ordinal"] in action_ordinals]
    checkpoints, changes = checkpoint_index(run_json), _changes(run)
    events = {e["ordinal"]: e for e in run["timeline"]}
    original = case.prompt
    suffix = PROMPT.read_text().strip()
    public = _public_files(case)
    # Known generated screenshots are previous observations, not task input.
    # Identify them from real image-consumption events, not a filename guess.
    evidence_ordinals = {i for episode in episodes for i in episode["evidence_ordinals"]}
    evidence_files = set()
    for ordinal in evidence_ordinals:
        event = events[ordinal]
        value = str((event.get("payload") or {}).get("file_path") or "")
        if event.get("category") == "view-image" and value.startswith("/workspace/"):
            relative = value.removeprefix("/workspace/")
            if relative not in public:
                evidence_files.add(relative)
    evidence_patterns = []
    for event in run["timeline"]:
        command = str((event.get("payload") or {}).get("command") or "")
        if event.get("kind") != "action" or "screenshot" not in command:
            continue
        # Explicit output options in recorded Chrome/playwright-cli commands;
        # screenshots not subsequently Read are still non-input artifacts.
        evidence_patterns.extend(_screenshot_outputs(command))
    output.mkdir(parents=True)
    samples, excluded, by_hash, by_input = [], [], {}, {}
    source_digest = sha256(run_json)
    for episode, group in groups:
        ordinal = group["action_ordinal"]
        origin = {"episode_id": episode["episode_id"], "action_ordinal": ordinal,
                  "timestamp": events[ordinal].get("timestamp"),
                  "verification_kind": episode["verification_kind"]}
        digest = _before(events[ordinal], changes)
        origin["source_program_sha256"] = digest
        try:
            if not digest:
                raise ValueError("pre_check_version_unknown")
            if digest in by_hash and ordinal not in audited:
                by_hash[digest]["origins"].append(origin)
                continue
            if ordinal in audited:
                source = audited[ordinal]
                manifest, source_root, is_workspace = source["manifest"], source["root"], True
                checkpoint, launch = source["checkpoint"], audited_launch
            else:
                checkpoint = checkpoints.get(digest)
                if not checkpoint or not checkpoint.get("reconstructable"):
                    raise ValueError("exact_pre_check_snapshot_unavailable")
                manifest, source_root, is_workspace = _manifest(checkpoint, digest)
                launch = audited_launch or {"command": ["bash", "/workspace/start.sh"], "cwd": "."}
            command, cwd = _launch(launch, manifest)
            rendered_suffix = suffix.replace("{launch_command}", shlex.join(command)).replace(
                "{launch_cwd}", str(Path("/workspace") / cwd))
            effective = original + "\n\n" + rendered_suffix
            # Validate all historical bytes before filtering browser/session logs.
            files = {name: _source_file(source_root, name, row, is_workspace)
                     for name, row in manifest.items()}
            retained = {name: row for name, row in manifest.items()
                        if not _history(name) and name not in evidence_files
                        and (name in public or not any(fnmatch.fnmatchcase(name, p) for p in evidence_patterns))}
            for name in retained.keys() & public.keys():
                if retained[name]["sha256"] != sha256(public[name]):
                    raise ValueError(f"public_input_was_modified: {name}")
            input_digest = manifest_hash(retained)
            if input_digest in by_input:
                by_input[input_digest]["origins"].append(origin)
                by_hash[digest] = by_input[input_digest]
                continue
            sample_id = case.case_id.replace("/", "__") + "__" + hashlib.sha256(
                f"{source_digest}:{input_digest}".encode()).hexdigest()[:12]
            destination = output / "samples" / sample_id
            workspace = destination / "workspace"
            workspace.mkdir(parents=True)
            for name, row in retained.items():
                target = workspace / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(files[name], target)
                os.chmod(target, int(row.get("mode", 0o644)) & 0o777)
            for name, source in public.items():
                target = workspace / name
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
            for destination_name, public_name in checkpoint.get("public_mappings", {}).items():
                for name, source in public.items():
                    if not name.startswith(public_name + "/"):
                        continue
                    target = workspace / destination_name / name.removeprefix(public_name + "/")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists() and sha256(target) != sha256(source):
                        raise ValueError("public_mapping_would_replace_code")
                    shutil.copy2(source, target)
            (destination / "prompt.txt").write_text(effective, encoding="utf-8")
            package = {p.relative_to(workspace).as_posix(): sha256(p)
                       for p in sorted(workspace.rglob("*")) if p.is_file()}
            write_json(destination / "task.json", {
                "schema": "vision2web-checkpoint-task-1", "task_id": sample_id,
                "case_id": case.case_id, "protocol": "checkpoint_verify",
                "workspace": "workspace", "prompt": "prompt.txt",
                "fresh_session": True, "repair_allowed": True,
                "launch_command": command, "launch_cwd": cwd,
                "app_url": "http://127.0.0.1:3000/",
                "original_prompt_sha256": hashlib.sha256(original.encode()).hexdigest(),
                "verify_prompt_sha256": hashlib.sha256(rendered_suffix.encode()).hexdigest(),
                "effective_prompt_sha256": hashlib.sha256(effective.encode()).hexdigest(),
                "workspace_files": package,
                "runtime_reset": "fresh container and browser; no historical live process/session",
            })
            row = {"task_id": sample_id, "path": str(destination.relative_to(output)),
                   "checkpoint_kind": checkpoint.get("kind", "exact_workspace"),
                   "version": checkpoint.get("version"),
                   "source_program_sha256": digest, "input_program_sha256": input_digest,
                   "origins": [origin]}
            provenance = {"source_run": str(run_json), "source_run_sha256": source_digest,
                          "source_model": run.get("model"), "source_framework": run.get("framework"),
                          "source_mode": run.get("mode"), "checkpoint": checkpoint,
                          "source_files": manifest,
                          "excluded_history_files": sorted(set(manifest) - set(retained))}
            write_json(destination / "provenance.json", provenance)
            samples.append(row)
            by_hash[digest] = row
            by_input[input_digest] = row
        except (ValueError, KeyError, OSError) as exc:
            excluded.append({**origin, "program_sha256": digest, "reason": str(exc)})
    result = {"schema": "vision2web-checkpoint-construction-1", "created_at": utc_now(),
              "source_run": str(run_json), "source_run_sha256": source_digest,
              "case_id": case.case_id, "candidate_count": len(groups),
              "sample_count": len(samples), "samples": samples, "excluded": excluded,
              "admission": "Candidates only. Require runtime-check.json status=pass before inference.",
              "selection": "Exact checkpoints or explicitly audited application versions; no nearest/final fallback. Deduplicated by retained code; no correctness filtering."}
    write_json(output / "manifest.json", result)
    return result


def verify_package(sample: Path) -> dict:
    task = read_json(sample / "task.json")
    if sha256(sample / "prompt.txt") != task["effective_prompt_sha256"]:
        raise ValueError("Prompt no longer matches frozen task")
    workspace = sample / "workspace"
    actual = {p.relative_to(workspace).as_posix(): sha256(p)
              for p in workspace.rglob("*") if p.is_file() and not p.is_symlink()}
    if actual != task["workspace_files"] or any(p.is_symlink() for p in workspace.rglob("*")):
        raise ValueError("Workspace no longer matches frozen task")
    _launch({"command": task["launch_command"], "cwd": task.get("launch_cwd", ".")}, actual)
    return task


def stage_task(sample: Path, workspace: Path) -> dict:
    """Copy only the agent-visible input; never history, provenance or probe output."""
    task = verify_package(sample)
    if workspace.exists() and any(workspace.iterdir()):
        raise FileExistsError(f"Refusing nonempty workspace: {workspace}")
    shutil.copytree(sample / "workspace", workspace, dirs_exist_ok=True)
    return task


def prepare_agent_case(sample: Path, workspace: Path, runtime_check: Path, *,
                       handoff: bool = False) -> AgentCase:
    """Entry for the existing runners: fresh workspace + task, no resumed session.

    The successful boot probe is bound to the immutable task package and is not
    shown to the agent. Its final code must NOT replace the initial checkpoint.
    """
    task = verify_package(sample)
    probe = read_json(runtime_check)
    if (probe.get("status") != "pass" or probe.get("task_id") != task["task_id"]
            or probe.get("task_sha256") != sha256(sample / "task.json")):
        raise ValueError("A successful runtime check for this exact task is required")
    prompt = (sample / "prompt.txt").read_text()
    metadata = {"protocol": "checkpoint_verify", "task_id": task["task_id"],
                "original_prompt_sha256": task["original_prompt_sha256"],
                "effective_prompt_sha256": task["effective_prompt_sha256"]}
    if handoff:
        # Recover the frozen public instruction prefix, not today's dataset
        # prompt and not the previous model's reasoning or verification advice.
        original = next((prompt[:m.start()] for m in re.finditer("\n\n", prompt)
                         if hashlib.sha256(prompt[:m.start()].encode()).hexdigest()
                         == task["original_prompt_sha256"]), None)
        if original is None:
            raise ValueError("Frozen original task prompt cannot be recovered")
        template = ROOT / "configs/prompts/vision2web/verification_handoff.txt"
        prompt = template.read_text().strip().format(
            task_specification=original, launch_command=shlex.join(task["launch_command"]),
            launch_cwd=str(Path("/workspace") / task.get("launch_cwd", ".")))
        metadata.update(protocol="verification_handoff", analysis_only=True,
                        source_task_sha256=sha256(sample / "task.json"),
                        prompt_template_sha256=sha256(template),
                        effective_prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
    stage_task(sample, workspace)
    return AgentCase(
        benchmark="vision2web", case_id=task["case_id"],
        task_type=task["case_id"].split("/", 1)[0],
        prompt=prompt,
        image_paths=tuple(sorted((workspace / "prototypes").glob("*"))),
        source_dir=workspace,
        metadata=metadata,
    )


def run_verification_handoff(sample: Path, runtime_check: Path, output: Path, *,
                             framework: str, model: str, base_url: str, api_key: str,
                             timeout: int, workspace: Path = Path("/workspace")) -> dict:
    """Optional analysis: one fresh verifier session, no automatic A continuation.

    Native tools/recorders are unchanged. No external deploy/check/repair loop,
    no stage routing, no retries that would grant extra verification budget.
    """
    if framework not in {"claude_code", "openhands"} or timeout <= 0:
        raise ValueError("Choose claude_code/openhands and a positive verification budget")
    output, workspace = output.resolve(), workspace.resolve()
    if output.exists():
        raise FileExistsError(output)
    if output.is_relative_to(workspace) or sample.resolve().is_relative_to(workspace):
        raise ValueError("Frozen inputs and run artifacts must stay outside the agent workspace")
    case = prepare_agent_case(sample, workspace, runtime_check, handoff=True)
    output.mkdir(parents=True)
    write_json(output / "input.json", {**case.to_dict(), "framework": framework,
                                      "model": model, "timeout_seconds": timeout,
                                      "source_sample": str(sample.resolve())})
    raw = output / f"{framework}.events.jsonl"
    kwargs = dict(model=model, base_url=base_url, api_key=api_key, timeout=timeout, max_retries=0)
    if framework == "claude_code":
        from multimodalcode.agent_harness.claude_code_runner import run_claude_code
        runner, kwargs["vision2web_mode"] = run_claude_code, "official"
    else:
        from multimodalcode.agent_harness.openhands_runner import run_openhands
        from multimodalcode.agent_harness.registry import VENDORED_OPENHANDS_ROOT
        runner = run_openhands
        kwargs.update(vision2web_mode="browser_enabled", runtime_root=VENDORED_OPENHANDS_ROOT)
    try:
        outcome = runner(case, workspace, raw, output / "stderr.log", **kwargs)
    except Exception as exc:
        # Do not serialize exception text that may contain an API credential.
        write_json(output / "result.json", {"protocol": "verification_handoff", "status": "failed",
                                            "error_type": type(exc).__name__})
        raise
    result = {**outcome, "protocol": "verification_handoff", "analysis_only": True,
              "model": model, "framework": framework, "task_id": case.metadata["task_id"],
              "raw_trajectory": str(raw), "input": str(output / "input.json"),
              "automatic_return_to_A": False,
              "completion_meaning": "Verification session ended; not task correctness or whole-task submission."}
    write_json(output / "result.json", result)
    return result


def validate_runtime(sample: Path, output: Path, *, workspace: Path = Path("/workspace"),
                     timeout: float = 180) -> dict:
    """Run in a fresh task container. Boot/screenshot check only; no correctness judge."""
    if workspace.resolve() != Path("/workspace"):
        raise ValueError("Run the original launch method in a fresh container at /workspace")
    if sample.resolve().is_relative_to(workspace) or output.resolve().is_relative_to(workspace):
        raise ValueError("Frozen inputs and offline probe evidence must stay outside /workspace")
    if output.exists():
        raise FileExistsError(output)
    task = verify_package(sample)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 3000))  # refuse to capture another task's server
    stage_task(sample, workspace)
    output.mkdir(parents=True)
    result = {"task_id": task["task_id"], "status": "fail", "checked_at": utc_now(),
              "task_sha256": sha256(sample / "task.json"), "command": task["launch_command"],
              "cwd": str(workspace / task.get("launch_cwd", ".")),
              "runtime": "fresh task container, /workspace", "correctness_assessed": False}
    process = None
    with (output / "server.log").open("w") as log:
        try:
            from playwright.sync_api import sync_playwright
            process = subprocess.Popen(task["launch_command"], cwd=workspace / task.get("launch_cwd", "."),
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            from .replay import _wait_ready
            _wait_ready(task["app_url"], timeout)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 900})
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
                    response = page.goto(task["app_url"], wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(1500)
                    page.screenshot(path=str(output / "initial.png"), full_page=True, timeout=30000)
                    (output / "initial.html").write_text(page.content(), encoding="utf-8")
                    visible_document = page.locator("body").evaluate(
                        "b => Boolean(b.innerText.trim() || b.querySelector('img,svg,canvas,video,input,button'))")
                    if not response or response.status >= 400 or not visible_document:
                        raise ValueError("No rendered application document")
                    result.update(status="pass", url=page.url, http_status=response.status,
                                  title=page.title(), screenshot="initial.png",
                                  screenshot_sha256=sha256(output / "initial.png"), console_errors=errors)
                finally:
                    browser.close()
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                except ProcessLookupError:
                    pass
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
    write_json(output / "runtime-check.json", result)
    return result
