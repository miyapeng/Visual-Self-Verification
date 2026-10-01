"""Passive workspace/version tracing for Vision2Web OpenHands runs."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.request
from urllib.parse import urlparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_IGNORED_DIRS = {
    ".agent_tmp",
    ".cache",
    ".git",
    ".mmcode",
    ".next",
    ".nuxt",
    ".parcel-cache",
    ".playwright-cli",
    ".svelte-kit",
    ".vite",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "prototypes",
    "resources",
    "test_results",
}
_IGNORED_FILES = {".mmcode_case.json"}
_FILE_DIGEST_CACHE: dict[str, tuple[tuple[int, ...], str]] = {}
_FILE_DIGEST_CACHE_LOCK = threading.Lock()
_MAX_FILE_DIGEST_CACHE_ENTRIES = 100_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _program_files(root: Path) -> list[Path]:
    result: list[Path] = []
    if not root.is_dir():
        return result
    for path in root.rglob("*"):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in _IGNORED_DIRS for part in relative.parts):
            continue
        if path.is_file() and not path.is_symlink() and path.name not in _IGNORED_FILES:
            result.append(path)
    return sorted(result)


def program_manifest(root: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path in _program_files(root):
        data: bytes | None = None
        digest: str | None = None
        stat = None
        for _ in range(2):
            try:
                before = path.stat()
            except OSError:
                break
            fingerprint = (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
                before.st_mode,
            )
            cache_key = os.path.abspath(path)
            with _FILE_DIGEST_CACHE_LOCK:
                cached = _FILE_DIGEST_CACHE.get(cache_key)
            if cached is not None and cached[0] == fingerprint:
                digest = cached[1]
                stat = before
                break
            try:
                data = path.read_bytes()
                after = path.stat()
            except OSError:
                break
            after_fingerprint = (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
                after.st_mode,
            )
            if fingerprint != after_fingerprint:
                continue
            digest = hashlib.sha256(data).hexdigest()
            stat = after
            with _FILE_DIGEST_CACHE_LOCK:
                if len(_FILE_DIGEST_CACHE) >= _MAX_FILE_DIGEST_CACHE_ENTRIES:
                    _FILE_DIGEST_CACHE.clear()
                _FILE_DIGEST_CACHE[cache_key] = (after_fingerprint, digest)
            break
        if digest is None or stat is None:
            continue
        rows[path.relative_to(root).as_posix()] = {
            "sha256": digest,
            "bytes": stat.st_size,
            "mode": stat.st_mode & 0o777,
        }
    return rows


def manifest_hash(manifest: dict[str, dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for name, row in sorted(manifest.items()):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(str(row["sha256"]).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def store_program_blobs(
    source: Path,
    trace_root: Path,
    manifest: dict[str, dict[str, Any]],
    *,
    names: list[str] | None = None,
) -> dict[str, Any]:
    """Persist deduplicated source bytes so a manifest can be reconstructed.

    This is a passive recorder artifact. It is never added to model context.
    """

    objects = trace_root.resolve() / "program_objects"
    selected = sorted(names if names is not None else manifest.keys())
    stored = 0
    missing: list[str] = []
    for name in selected:
        row = manifest.get(name)
        if not row:
            continue
        path = source / name
        try:
            data = path.read_bytes()
        except OSError:
            missing.append(name)
            continue
        digest = hashlib.sha256(data).hexdigest()
        if digest != row.get("sha256"):
            missing.append(name)
            continue
        destination = objects / digest[:2] / digest
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o444
            )
        except FileExistsError:
            continue
        try:
            os.write(descriptor, data)
            stored += 1
        finally:
            os.close(descriptor)
    return {
        "program_object_root": str(objects),
        "stored_object_count": stored,
        "missing_objects": missing,
        "reconstructable": not missing,
    }


def copy_program(
    source: Path,
    destination: Path,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy an immutable code checkpoint while excluding task inputs/runtimes."""
    temporary = destination.with_name(
        destination.name + f".tmp-{os.getpid()}-{time.time_ns()}"
    )
    temporary.mkdir(parents=True)
    for path in _program_files(source):
        relative = path.relative_to(source)
        target = temporary / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, target)
        except FileNotFoundError:
            continue
    manifest = program_manifest(temporary)
    record = {
        "schema": "multimodalcode-vision2web-program-version-1",
        "captured_at_utc": utc_now(),
        "source_workspace": str(source),
        "program_sha256": manifest_hash(manifest),
        "file_count": len(manifest),
        "files": manifest,
        "excluded_top_level_inputs": ["prototypes", "resources"],
    }
    if metadata:
        record.update(metadata)
    if destination.exists():
        raise FileExistsError(destination)
    temporary.replace(destination)
    destination.with_suffix(".json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return record


def is_deployed_application_url(url: str) -> bool:
    """Return whether *url* can be the Vision2Web app served in the case.

    Prototype files (``file://``), browser-internal pages, and remote websites
    must never create the first-implementation checkpoint. Vision2Web's public
    runtime contract serves the generated app on loopback port 3000.
    """

    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme in {"http", "https"}
        and (parsed.hostname or "").casefold() in {"localhost", "127.0.0.1", "::1"}
        and port == 3000
    )


def _listening_socket_inodes(port: int) -> set[str]:
    """Return Linux socket inodes listening on *port* in this container."""

    result: set[str] = set()
    for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            lines = table.read_text(encoding="ascii").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "0A":
                continue
            try:
                local_port = int(fields[1].rsplit(":", 1)[1], 16)
            except (IndexError, ValueError):
                continue
            if local_port == port:
                result.add(fields[9])
    return result


def _listener_processes(socket_inodes: set[str]) -> list[dict[str, Any]]:
    """Identify listener processes without recording their command text."""

    if not socket_inodes:
        return []
    result: list[dict[str, Any]] = []
    try:
        proc_entries = list(Path("/proc").iterdir())
    except OSError:
        return result
    for process_dir in proc_entries:
        if not process_dir.name.isdigit():
            continue
        fd_dir = process_dir / "fd"
        matched = False
        try:
            descriptors = list(fd_dir.iterdir())
        except OSError:
            continue
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except OSError:
                continue
            if target.startswith("socket:[") and target[8:-1] in socket_inodes:
                matched = True
                break
        if not matched:
            continue

        command_sha256 = None
        start_time_ticks = None
        try:
            command_sha256 = hashlib.sha256(
                (process_dir / "cmdline").read_bytes()
            ).hexdigest()
        except OSError:
            pass
        try:
            stat_text = (process_dir / "stat").read_text(encoding="utf-8")
            # Fields after the final ')' start at proc(5) field 3 (state).
            tail = stat_text[stat_text.rfind(")") + 2 :].split()
            start_time_ticks = int(tail[19])
        except (OSError, ValueError, IndexError):
            pass
        result.append(
            {
                "pid": int(process_dir.name),
                "process_start_time_ticks": start_time_ticks,
                "command_sha256": command_sha256,
            }
        )
    return sorted(result, key=lambda row: row["pid"])


def _service_identity(url: str, *, observation_succeeded: bool) -> dict[str, Any]:
    parsed = urlparse(url)
    port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
    inodes = _listening_socket_inodes(port)
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii"
        ).strip()
    except OSError:
        boot_id = None
    return {
        "url": url,
        "host": parsed.hostname,
        "port": port,
        "browser_observation_succeeded": observation_succeeded,
        "listener_found": bool(inodes),
        "listener_socket_count": len(inodes),
        "listener_processes": _listener_processes(inodes),
        "container_boot_id": boot_id,
    }


def capture_observation_binding(
    workspace: Path,
    trace_root: Path,
    *,
    url: str,
    observation_succeeded: bool,
) -> dict[str, Any] | None:
    """Bind a deployed-app observation to code and service state.

    The binding is passive sidecar metadata. It is never inserted into a tool
    result or model prompt. Full source trees remain limited to ``P_first`` and
    ``P_final``; intermediate versions store a deduplicated content manifest.
    Recorder failures are represented in the binding instead of interrupting
    the agent.
    """

    if not is_deployed_application_url(url):
        return None
    started_ns = time.perf_counter_ns()
    captured_at = utc_now()
    workspace = workspace.resolve()
    trace_root = trace_root.resolve()
    errors: list[str] = []

    manifest: dict[str, dict[str, Any]] = {}
    program_sha256 = None
    file_manifest_sha256 = None
    manifest_path = None
    object_record: dict[str, Any] | None = None
    try:
        manifest = program_manifest(workspace)
        program_sha256 = manifest_hash(manifest)
        canonical = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        file_manifest_sha256 = hashlib.sha256(canonical).hexdigest()
        manifests = trace_root / "manifests"
        manifests.mkdir(parents=True, exist_ok=True)
        destination = manifests / f"{file_manifest_sha256}.json"
        try:
            descriptor = os.open(
                destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
            )
        except FileExistsError:
            pass
        else:
            try:
                os.write(descriptor, canonical)
            finally:
                os.close(descriptor)
        manifest_path = str(destination)
        object_record = store_program_blobs(workspace, trace_root, manifest)
    except OSError as exc:
        errors.append(f"manifest: {type(exc).__name__}: {exc}")

    try:
        deployment = _service_identity(
            url, observation_succeeded=observation_succeeded
        )
    except (OSError, ValueError) as exc:
        deployment = {
            "url": url,
            "browser_observation_succeeded": observation_succeeded,
        }
        errors.append(f"service_identity: {type(exc).__name__}: {exc}")

    result: dict[str, Any] = {
        "schema": "multimodalcode-observation-binding-1",
        "captured_at_utc": captured_at,
        "program_sha256": program_sha256,
        "file_manifest_sha256": file_manifest_sha256,
        "file_manifest_path": manifest_path,
        "file_count": len(manifest),
        "program_objects": object_record,
        "deployment": deployment,
        "recorder_latency_ms": round(
            (time.perf_counter_ns() - started_ns) / 1_000_000, 3
        ),
    }
    if errors:
        result["recorder_errors"] = errors
    return result


def append_trace_event(
    trace_root: Path,
    event_type: str,
    payload: dict[str, Any],
    *,
    filename: str = "workspace.events.jsonl",
) -> dict[str, Any]:
    """Append one compact event from an agent child process.

    Opening with ``O_APPEND`` keeps each JSON line atomic with respect to the
    passive recorder process, which writes to the same file.
    """

    row = {
        "timestamp": utc_now(),
        "monotonic_ns": time.monotonic_ns(),
        "type": event_type,
        "payload": payload,
    }
    destination = trace_root.resolve() / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    descriptor = os.open(destination, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(descriptor, data)
    finally:
        os.close(descriptor)
    return row


def capture_first_application_observation(
    workspace: Path,
    trace_root: Path,
    *,
    framework: str,
    tool: str,
    url: str,
    screenshot_sha256: str | None = None,
    evidence_path: str | None = None,
) -> dict[str, Any] | None:
    """Checkpoint the program on the first visual observation delivered to policy.

    This hook is called by the browser interface immediately before its
    successful tool observation is returned. It does not start, inspect, or
    judge the application and therefore cannot force a verification loop.
    """

    if not is_deployed_application_url(url):
        return None
    workspace = workspace.resolve()
    trace_root = trace_root.resolve()
    versions = trace_root / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    first_path = versions / "P_first"
    metadata_path = first_path.with_suffix(".json")
    if first_path.is_dir() and metadata_path.is_file():
        return json.loads(metadata_path.read_text(encoding="utf-8"))

    lock_path = versions / ".P_first.lock"
    try:
        lock_path.mkdir()
    except FileExistsError:
        # Another browser observation is writing the same immutable checkpoint.
        for _ in range(100):
            if first_path.is_dir() and metadata_path.is_file():
                return json.loads(metadata_path.read_text(encoding="utf-8"))
            time.sleep(0.01)
        return None

    try:
        if first_path.exists():
            return (
                json.loads(metadata_path.read_text(encoding="utf-8"))
                if metadata_path.is_file()
                else None
            )
        observation = {
            "definition": "first_model_observation_of_deployed_application",
            "framework": framework,
            "tool": tool,
            "url": url,
            "screenshot_sha256": screenshot_sha256,
            "evidence_path": evidence_path,
        }
        record = copy_program(
            workspace,
            first_path,
            metadata={"checkpoint": observation},
        )
        append_trace_event(
            trace_root,
            "first_application_observation_version",
            {"path": str(first_path), **record},
        )
        return record
    except OSError as exc:
        append_trace_event(
            trace_root,
            "first_application_observation_capture_error",
            {"error": f"{type(exc).__name__}: {exc}", "url": url, "tool": tool},
        )
        return None
    finally:
        try:
            lock_path.rmdir()
        except OSError:
            pass


class Vision2WebRunRecorder:
    """Observe edits and deployment without injecting feedback into the agent."""

    def __init__(
        self,
        workspace: Path,
        root: Path,
        *,
        app_url: str = "http://127.0.0.1:3000/",
        poll_seconds: float = 0.5,
    ):
        self.workspace = workspace.resolve()
        self.root = root.resolve()
        self.versions = self.root / "versions"
        self.events_path = self.root / "workspace.events.jsonl"
        self.app_url = app_url
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._manifest: dict[str, dict[str, Any]] = {}
        self._reachable = False
        self._write_lock = threading.Lock()

    def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.versions.mkdir(parents=True, exist_ok=True)
        self._manifest = program_manifest(self.workspace)
        initial_objects = store_program_blobs(
            self.workspace, self.root, self._manifest
        )
        self._append(
            "recorder_started",
            {
                "workspace": str(self.workspace),
                "app_url": self.app_url,
                "initial_program_sha256": manifest_hash(self._manifest),
                "program_objects": initial_objects,
            },
        )
        self._thread = threading.Thread(
            target=self._run, name="vision2web-run-recorder", daemon=True
        )
        self._thread.start()

    def finish(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(5.0, self.poll_seconds * 4))
        # Flush an edit that happened immediately before the agent exited.  The
        # raw trajectory preserves the Bash/file-editor action itself; this
        # final manifest comparison ensures the corresponding persistent
        # workspace change is recorded before P_final even when it falls inside
        # the regular polling interval.
        self._poll_files()
        final_path = self.versions / "P_final"
        final_record = copy_program(self.workspace, final_path)
        self._append(
            "final_submission_version",
            {"path": str(final_path), **final_record},
        )
        first_path = self.versions / "P_first"
        first_metadata = first_path.with_suffix(".json")
        first_record = (
            json.loads(first_metadata.read_text(encoding="utf-8"))
            if first_path.is_dir() and first_metadata.is_file()
            else None
        )
        summary = {
            "schema": "multimodalcode-vision2web-version-pair-2",
            "P_first": (
                {"path": str(first_path), **first_record}
                if first_record
                else None
            ),
            "P_final": {"path": str(final_path), **final_record},
            "first_application_observed": first_record is not None,
            "checkpoint_definition": (
                "P_first is the workspace when the first browser observation of the "
                "deployed loopback:3000 application is prepared for the coding policy"
            ),
            "observer_only": True,
        }
        (self.root / "versions.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return summary

    def _run(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            self._poll_files()
            self._poll_deployment()

    def _poll_files(self) -> None:
        current = program_manifest(self.workspace)
        if current == self._manifest:
            return
        old_names = set(self._manifest)
        new_names = set(current)
        created = sorted(new_names - old_names)
        deleted = sorted(old_names - new_names)
        modified = sorted(
            name
            for name in old_names & new_names
            if self._manifest[name]["sha256"] != current[name]["sha256"]
        )
        self._manifest = current
        object_record = store_program_blobs(
            self.workspace,
            self.root,
            current,
            names=created + modified,
        )
        self._append(
            "workspace_change",
            {
                "created": created,
                "modified": modified,
                "deleted": deleted,
                "program_sha256": manifest_hash(current),
                "program_objects": object_record,
            },
        )

    def _poll_deployment(self) -> None:
        request = urllib.request.Request(self.app_url, method="GET")
        reachable = False
        status = None
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=0.4) as response:
                status = int(response.status)
                reachable = status < 500
        except Exception:
            reachable = False
        if reachable != self._reachable:
            self._reachable = reachable
            self._append(
                "deployment_ready" if reachable else "deployment_unreachable",
                {"url": self.app_url, "http_status": status},
            )

    def _append(self, event_type: str, payload: dict[str, Any]) -> None:
        row = {
            "timestamp": utc_now(),
            "monotonic_ns": time.monotonic_ns(),
            "type": event_type,
            "payload": payload,
        }
        with self._write_lock:
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")


class NativeOpenHandsBrowserRecorder:
    """Passively mirror native visual observations from OpenHands' JSONL.

    The recorder tails the CLI output written by the parent process. It never
    wraps a browser executor and never changes a tool call or observation seen
    by the model. Its only side effects are analysis artifacts and the immutable
    first-observed program checkpoint.
    """

    def __init__(
        self,
        raw_trajectory: Path,
        workspace: Path,
        trace_root: Path,
        *,
        poll_seconds: float = 0.05,
    ):
        self.raw_trajectory = raw_trajectory.resolve()
        self.workspace = workspace.resolve()
        self.trace_root = trace_root.resolve()
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._offset = 0
        self._buffer = b""
        self._seen: set[str] = set()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name="vision2web-native-browser-recorder",
            daemon=True,
        )
        self._thread.start()

    def finish(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(5.0, self.poll_seconds * 4))
        self._poll()

    def _run(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            self._poll()

    def _poll(self) -> None:
        try:
            with self.raw_trajectory.open("rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read()
                self._offset = handle.tell()
        except OSError:
            return
        if not chunk:
            return
        self._buffer += chunk
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            self._record_event(event)

    def _record_event(self, event: dict[str, Any]) -> None:
        if (
            event.get("kind") != "ObservationEvent"
            or event.get("tool_name") != "browser_get_state"
        ):
            return
        event_id = str(event.get("id") or "")
        if event_id and event_id in self._seen:
            return
        observation = event.get("observation") or {}
        if observation.get("is_error"):
            return
        screenshot_data = observation.get("screenshot_data")
        if not isinstance(screenshot_data, str) or not screenshot_data:
            return
        url = ""
        for block in observation.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            try:
                state = json.loads(str(block.get("text") or ""))
            except json.JSONDecodeError:
                continue
            url = str(state.get("url") or "")
            if url:
                break
        if not is_deployed_application_url(url):
            return
        try:
            screenshot = base64.b64decode(screenshot_data, validate=True)
        except (ValueError, TypeError):
            return
        digest = hashlib.sha256(screenshot).hexdigest()
        evidence = self.trace_root / "evidence" / f"native-{event_id or digest[:16]}"
        evidence.mkdir(parents=True, exist_ok=True)
        screenshot_path = evidence / "screenshot.png"
        screenshot_path.write_bytes(screenshot)
        binding = capture_observation_binding(
            self.workspace,
            self.trace_root,
            url=url,
            observation_succeeded=True,
        )
        capture_first_application_observation(
            self.workspace,
            self.trace_root,
            framework="openhands",
            tool="browser_get_state",
            url=url,
            screenshot_sha256=digest,
            evidence_path=str(screenshot_path),
        )
        append_trace_event(
            self.trace_root,
            "model_visual_observation_prepared",
            {
                "tool": "browser_get_state",
                "url": url,
                "source_event_id": event_id or None,
                "screenshot_sha256": digest,
                "screenshot_path": str(screenshot_path),
                "inline_image_content": True,
                "state_source": "unmodified_openhands_jsonl",
                "observation_binding": binding,
            },
            filename="browser.events.jsonl",
        )
        if event_id:
            self._seen.add(event_id)


def build_development_timeline(
    raw_trajectory: Path,
    trace_root: Path,
    *,
    attempt_trajectories: list[Path] | None = None,
) -> dict[str, Any]:
    """Merge all OpenHands attempts, workspace, and browser events in UTC.

    ``raw_trajectory`` remains the canonical final-attempt stream used by the
    benchmark adapter.  Development analysis additionally needs earlier retry
    attempts because their edits persist in the shared workspace.  Callers may
    therefore provide the preserved attempt streams without changing official
    inference or evaluation artifacts.
    """
    rows: list[dict[str, Any]] = []

    def add(source: str, row: dict[str, Any]) -> None:
        timestamp = str(row.get("timestamp") or "")
        rows.append({"source": source, **row, "timestamp": timestamp})

    for name, source in (
        ("workspace.events.jsonl", "workspace_observer"),
        ("browser.events.jsonl", "browser_interface"),
    ):
        path = trace_root / name
        if path.is_file():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip():
                    add(source, json.loads(line))

    streams = attempt_trajectories or [raw_trajectory]
    for attempt_index, stream in enumerate(streams, start=1):
        if not stream.is_file():
            continue
        for sequence, line in enumerate(
            stream.read_text(encoding="utf-8", errors="replace").splitlines()
        ):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("kind")
            tool = event.get("tool_name")
            action = event.get("action") or {}
            event_type = "agent_event"
            if kind == "ActionEvent":
                if tool == "file_editor" and action.get("command") in {
                    "create",
                    "str_replace",
                    "insert",
                    "undo_edit",
                }:
                    event_type = "edit_action"
                elif isinstance(tool, str) and tool.startswith("browser_"):
                    event_type = "browser_tool_call"
                elif tool == "finish" or action.get("kind") == "FinishAction":
                    event_type = "submit_action"
                elif tool == "terminal":
                    command = str(action.get("command", ""))
                    event_type = (
                        "deploy_action"
                        if re.search(r"start\.sh|npm\s+run\s+(dev|start)|vite", command)
                        else "terminal_action"
                    )
                else:
                    event_type = "agent_action"
            elif kind == "ObservationEvent":
                event_type = "agent_observation"
            add(
                "openhands",
                {
                    "timestamp": event.get("timestamp"),
                    "type": event_type,
                    "attempt": attempt_index,
                    "sequence_in_raw": sequence,
                    "payload": {
                        "event_id": event.get("id"),
                        "tool": tool,
                        "action": action if kind == "ActionEvent" else None,
                        "observation": event.get("observation") if kind == "ObservationEvent" else None,
                    },
                },
            )

    def sort_key(row: dict[str, Any]) -> tuple[str, int, int]:
        stamp = row.get("timestamp") or "9999"
        # OpenHands emits naive ISO strings; all benchmark containers use UTC.
        if stamp and stamp[-1] not in {"Z"} and "+" not in stamp[10:]:
            stamp += "+00:00"
        return (stamp, int(row.get("monotonic_ns", 0)), int(row.get("sequence_in_raw", 0)))

    rows.sort(key=sort_key)
    output = trace_root / "development_timeline.jsonl"
    with output.open("w", encoding="utf-8") as handle:
        for sequence, row in enumerate(rows):
            handle.write(json.dumps({"sequence": sequence, **row}, ensure_ascii=False) + "\n")
    summary = {
        "schema": "multimodalcode-vision2web-development-timeline-1",
        "event_count": len(rows),
        "sources": sorted({row["source"] for row in rows}),
        "openhands_attempts": len(streams),
        "path": str(output),
    }
    (trace_root / "timeline_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def _claude_content_blocks(event: dict[str, Any]) -> list[dict[str, Any]]:
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        content = event.get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _claude_action_type(tool: str, action_input: dict[str, Any]) -> str:
    normalized = tool.casefold()
    if normalized in {"edit", "write", "multiedit", "notebookedit"}:
        return "edit_action"
    if normalized == "bash":
        command = str(action_input.get("command", ""))
        if re.search(
            r"(?:^|\s)(?:(?:[^\s;&|]*/)?playwright-cli|browser[_-]execute[_-]plan)\b",
            command,
        ):
            return "browser_tool_call"
        if re.search(r"start\.sh|npm\s+run\s+(?:dev|start)|(?:^|\s)vite\b", command):
            return "deploy_action"
        return "terminal_action"
    return "agent_action"


def build_claude_development_timeline(
    raw_trajectory: Path,
    trace_root: Path,
    *,
    attempt_trajectories: list[Path] | None = None,
    capture_paths: list[Path] | None = None,
) -> dict[str, Any]:
    """Merge Claude stream-json, passive workspace, and browser chronology.

    Claude Code does not guarantee timestamps in every stream-json event.  The
    direct-container runner therefore records the UTC receive time of each raw
    stdout line in a sidecar.  The canonical raw stream remains unchanged.
    """

    rows: list[dict[str, Any]] = []

    def add(source: str, row: dict[str, Any]) -> None:
        rows.append({"source": source, **row, "timestamp": str(row.get("timestamp") or "")})

    for name, source in (
        ("workspace.events.jsonl", "workspace_observer"),
        ("browser.events.jsonl", "browser_interface"),
    ):
        path = trace_root / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                add(source, json.loads(line))
            except json.JSONDecodeError:
                continue

    streams = attempt_trajectories or [raw_trajectory]
    captures = capture_paths or []
    for attempt_index, stream in enumerate(streams, start=1):
        if not stream.is_file():
            continue
        capture_by_sequence: dict[int, dict[str, Any]] = {}
        if attempt_index <= len(captures) and captures[attempt_index - 1].is_file():
            for line in captures[attempt_index - 1].read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                try:
                    captured = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if captured.get("stream") == "stdout":
                    capture_by_sequence[int(captured.get("sequence", -1))] = captured

        for sequence, line in enumerate(
            stream.read_text(encoding="utf-8", errors="replace").splitlines()
        ):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            captured = capture_by_sequence.get(sequence, {})
            timestamp = event.get("timestamp") or captured.get("timestamp")
            monotonic_ns = int(captured.get("monotonic_ns", 0))
            blocks = _claude_content_blocks(event)
            tool_blocks = [block for block in blocks if block.get("type") == "tool_use"]
            if tool_blocks:
                for block_index, block in enumerate(tool_blocks):
                    tool = str(block.get("name") or "")
                    action_input = block.get("input")
                    if not isinstance(action_input, dict):
                        action_input = {}
                    add(
                        "claude_code",
                        {
                            "timestamp": timestamp,
                            "monotonic_ns": monotonic_ns,
                            "type": _claude_action_type(tool, action_input),
                            "attempt": attempt_index,
                            "sequence_in_raw": sequence,
                            "subsequence": block_index,
                            "payload": {
                                "event_type": event.get("type"),
                                "tool": tool,
                                "tool_use_id": block.get("id"),
                                "action": action_input,
                            },
                        },
                    )
                continue

            event_type = str(event.get("type") or "agent_event")
            if event_type == "user" and any(
                block.get("type") == "tool_result" for block in blocks
            ):
                row_type = "agent_observation"
            elif event_type == "assistant":
                row_type = "agent_message"
            elif event_type == "result":
                row_type = "submit_action"
            else:
                row_type = "agent_event"
            add(
                "claude_code",
                {
                    "timestamp": timestamp,
                    "monotonic_ns": monotonic_ns,
                    "type": row_type,
                    "attempt": attempt_index,
                    "sequence_in_raw": sequence,
                    "payload": {
                        "event_type": event.get("type"),
                        "subtype": event.get("subtype"),
                        "is_error": event.get("is_error"),
                    },
                },
            )

    def sort_key(row: dict[str, Any]) -> tuple[str, int, int, int]:
        stamp = row.get("timestamp") or "9999"
        if stamp and stamp[-1] != "Z" and "+" not in stamp[10:]:
            stamp += "+00:00"
        return (
            stamp,
            int(row.get("monotonic_ns", 0)),
            int(row.get("sequence_in_raw", 0)),
            int(row.get("subsequence", 0)),
        )

    rows.sort(key=sort_key)
    output = trace_root / "development_timeline.jsonl"
    with output.open("w", encoding="utf-8") as handle:
        for sequence, row in enumerate(rows):
            handle.write(json.dumps({"sequence": sequence, **row}, ensure_ascii=False) + "\n")
    summary = {
        "schema": "multimodalcode-vision2web-development-timeline-1",
        "framework": "claude_code",
        "event_count": len(rows),
        "sources": sorted({row["source"] for row in rows}),
        "claude_attempts": len(streams),
        "path": str(output),
    }
    (trace_root / "timeline_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary
