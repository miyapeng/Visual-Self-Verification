from __future__ import annotations

import hashlib
import json
import os
import signal
import shutil
import socket
import subprocess
import time
import uuid
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional
from urllib.parse import unquote, urlsplit

from ..io import write_json
from .events import EventLog, EvidenceStore
from .schema import ActionPlan


PROGRAM_RUNTIME_DIRECTORIES = {
    ".cache",
    ".parcel-cache",
    ".vite",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
}

PROGRAM_SOURCE_SUFFIXES = {
    ".astro",
    ".cjs",
    ".css",
    ".htm",
    ".html",
    ".js",
    ".jsx",
    ".json",
    ".less",
    ".md",
    ".mjs",
    ".py",
    ".scss",
    ".svelte",
    ".ts",
    ".tsx",
    ".vue",
}


def _program_files(root: Path) -> List[Path]:
    paths = [root] if root.is_file() else sorted(item for item in root.rglob("*") if item.is_file())
    if root.is_file():
        return paths
    return [
        item
        for item in paths
        if not item.is_symlink()
        and not any(
            part in PROGRAM_RUNTIME_DIRECTORIES or part == ".git"
            for part in item.relative_to(root).parts
        )
    ]


def hash_program(path: str | Path) -> str:
    root = Path(path).resolve()
    digest = hashlib.sha256()
    for item in _program_files(root):
        relative = item.name if root.is_file() else str(item.relative_to(root))
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def runtime_source_context(
    program_path: str | Path,
    loaded_resources: Any,
    *,
    entry_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Map browser-observed same-origin resources to immutable workspace files.

    The mapping is intentionally mechanical. It does not search by task words,
    inspect private tests, or infer relevance from filenames. A document route
    that is served through a single-page-app fallback maps to ``index.html``;
    Vite runtime and dependency URLs are recorded but never exposed as editable
    task source.
    """

    program = Path(program_path).resolve()
    root = program if program.is_dir() else program.parent
    entry_relative = "index.html" if program.is_dir() else program.name
    entry = root / entry_relative
    parsed_entry = urlsplit(str(entry_url or ""))
    entry_origin = (
        parsed_entry.scheme,
        parsed_entry.hostname,
        parsed_entry.port,
    ) if parsed_entry.scheme in {"http", "https"} else None

    rows = loaded_resources if isinstance(loaded_resources, list) else []
    mapped_rows: List[Dict[str, Any]] = []
    unmapped_rows: List[Dict[str, Any]] = []
    paths_in_order: List[str] = []
    resource_urls: Dict[str, List[str]] = {}

    def register(candidate: Path, row: Dict[str, Any]) -> bool:
        try:
            resolved = candidate.resolve()
            relative = resolved.relative_to(root)
        except (OSError, ValueError):
            return False
        if (
            not resolved.is_file()
            or resolved.is_symlink()
            or any(
                part in PROGRAM_RUNTIME_DIRECTORIES or part == ".git"
                for part in relative.parts
            )
            or resolved.suffix.lower() not in PROGRAM_SOURCE_SUFFIXES
        ):
            return False
        name = relative.as_posix()
        if name not in paths_in_order:
            paths_in_order.append(name)
        url = str(row.get("url", ""))
        if url and url not in resource_urls.setdefault(name, []):
            resource_urls[name].append(url)
        mapped_rows.append(
            {
                "sequence": row.get("sequence"),
                "url": url,
                "resource_type": str(row.get("resource_type", "")),
                "path": name,
            }
        )
        return True

    for raw_row in rows:
        if not isinstance(raw_row, dict):
            continue
        row = dict(raw_row)
        raw_url = str(row.get("url", ""))
        parsed = urlsplit(raw_url)
        candidate: Optional[Path] = None
        reason = "not_a_workspace_source"
        if parsed.scheme == "file":
            candidate = Path(unquote(parsed.path))
        elif parsed.scheme in {"http", "https"}:
            origin = (parsed.scheme, parsed.hostname, parsed.port)
            if entry_origin is not None and origin != entry_origin:
                reason = "cross_origin"
            else:
                decoded_path = unquote(parsed.path)
                if decoded_path.startswith("/@fs/"):
                    candidate = Path(decoded_path[len("/@fs/") :])
                else:
                    relative_url = decoded_path.lstrip("/")
                    if relative_url.startswith(("@vite/", "@id/", "node_modules/")):
                        reason = "runtime_or_dependency"
                    elif relative_url:
                        direct = root / relative_url
                        if direct.is_file():
                            candidate = direct
                        elif str(row.get("resource_type", "")) == "document":
                            candidate = entry
                    elif str(row.get("resource_type", "")) == "document":
                        candidate = entry
        if candidate is not None and register(candidate, row):
            continue
        unmapped_rows.append(
            {
                "sequence": row.get("sequence"),
                "url": raw_url,
                "resource_type": str(row.get("resource_type", "")),
                "reason": reason,
            }
        )

    fallback = None
    if not paths_in_order and entry.is_file():
        register(
            entry,
            {
                "sequence": None,
                "url": str(entry_url or entry.as_uri()),
                "resource_type": "document",
            },
        )
        fallback = "entry_file_only"

    files = []
    for relative in paths_in_order:
        path = root / relative
        files.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "resource_urls": resource_urls.get(relative, []),
            }
        )
    return {
        "mapping_version": 1,
        "entry_file": entry_relative,
        "loaded_resource_count": len(rows),
        "mapped_resource_count": len(mapped_rows),
        "files": files,
        "mapped_resources": mapped_rows,
        "unmapped_resources": unmapped_rows,
        "fallback": fallback,
    }


class InteractiveJudge:
    """Frozen-plan Playwright executor with append-only evidence import."""

    def __init__(self, run_dir: str | Path, *, event_log: Optional[EventLog] = None):
        self.run_dir = Path(run_dir).resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.event_log = event_log or EventLog(self.run_dir / "events.jsonl")
        self.evidence_store = EvidenceStore(self.run_dir / "evidence")

    def execute(
        self,
        *,
        case_id: str,
        program_path: str | Path,
        plan: ActionPlan,
        purpose: str = "iteration",
        viewport: tuple[int, int] = (1280, 960),
        timeout_ms: int = 30_000,
        record_video: bool = True,
    ) -> Dict[str, Any]:
        program = Path(program_path).resolve()
        if not program.exists():
            raise FileNotFoundError(program)
        code_version = hash_program(program)
        plan_viewport = plan.metadata.get("viewport")
        if isinstance(plan_viewport, dict):
            viewport = (
                int(plan_viewport.get("width", viewport[0])),
                int(plan_viewport.get("height", viewport[1])),
            )
        execution_id = str(uuid.uuid4())
        execution_dir = self.run_dir / "executions" / case_id / execution_id
        execution_dir.mkdir(parents=True, exist_ok=False)
        plan_path = execution_dir / "plan.json"
        write_json(plan_path, plan.to_dict())
        spec = {
            "caseId": case_id,
            "executionId": execution_id,
            "codeVersion": code_version,
            "purpose": purpose,
            "programPath": str(program),
            "isSite": program.is_dir(),
            "outputDir": str(execution_dir),
            "viewport": {"width": int(viewport[0]), "height": int(viewport[1])},
            "timeoutMs": int(timeout_ms),
            "recordVideo": bool(record_video),
            "plan": plan.to_dict(),
        }
        spec_path = execution_dir / "execution_spec.json"
        result_path = execution_dir / "result.json"
        write_json(spec_path, spec)
        self.event_log.append(
            "verification_started",
            {"execution_id": execution_id, "purpose": purpose, "plan_path": str(plan_path)},
            case_id=case_id,
            code_version=code_version,
        )
        command = self._node_command(spec_path, result_path)
        runtime = plan.metadata.get("web_runtime")
        with self._web_runtime(program, runtime, execution_dir) as runtime_info:
            if runtime_info:
                spec["entryUrl"] = runtime_info["entry_url"]
                spec["serviceLog"] = runtime_info["service_log"]
                write_json(spec_path, spec)
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                env=self._execution_environment(),
                timeout=max(60, timeout_ms * (len(plan.actions) + 2) // 1000),
                check=False,
            )
        if process.returncode != 0:
            executor_result: Dict[str, Any] = {}
            if result_path.is_file():
                try:
                    loaded = json.loads(result_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        executor_result = loaded
                except (OSError, json.JSONDecodeError):
                    executor_result = {}
            error = {
                "execution_id": execution_id,
                "status": "error",
                "returncode": process.returncode,
                "stdout": process.stdout[-4000:],
                "stderr": process.stderr[-8000:],
                "executor_result": executor_result,
            }
            write_json(result_path, error)
            self.event_log.append(
                "verification_failed", error, case_id=case_id, code_version=code_version
            )
            detail = str(executor_result.get("error") or process.stderr.strip())
            raise RuntimeError(
                f"Interactive Playwright executor failed ({process.returncode}): "
                f"{detail}"
            )
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["result_relative_path"] = result_path.relative_to(self.run_dir).as_posix()
        service_log = spec.get("serviceLog")
        if service_log and Path(service_log).is_file():
            result["service_log"] = service_log
        result["runtime_source_context"] = runtime_source_context(
            program,
            result.get("loaded_resources", []),
            entry_url=spec.get("entryUrl"),
        )
        result["evidence_manifest"] = self._import_evidence(
            result, case_id=case_id, code_version=code_version
        )
        write_json(result_path, result)
        self.event_log.append(
            "verification_completed",
            {
                "execution_id": execution_id,
                "purpose": purpose,
                "score": result.get("score"),
                "result_path": str(result_path),
            },
            case_id=case_id,
            code_version=code_version,
        )
        return result

    @contextmanager
    def _web_runtime(
        self,
        program: Path,
        raw_runtime: Any,
        execution_dir: Path,
    ) -> Iterator[Optional[Dict[str, str]]]:
        """Start a public task server for the duration of one frozen replay.

        The command is an argv vector from the public case manifest. No shell is
        involved. ``{port}`` and ``{program}`` are the only substitutions.
        """

        if not raw_runtime:
            yield None
            return
        if not program.is_dir() or not isinstance(raw_runtime, dict):
            raise ValueError("web_runtime requires a program directory and object metadata")
        raw_command = raw_runtime.get("command")
        if not isinstance(raw_command, list) or not raw_command or not all(
            isinstance(part, str) and part for part in raw_command
        ):
            raise ValueError("web_runtime.command must be a non-empty argv list")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        command = [
            part.replace("{port}", str(port)).replace("{program}", str(program))
            for part in raw_command
        ]
        entry_path = str(raw_runtime.get("entry_path") or "/")
        if not entry_path.startswith("/"):
            entry_path = "/" + entry_path
        entry_url = f"http://127.0.0.1:{port}{entry_path}"
        service_log = execution_dir / "service.log"
        environment = self._execution_environment()
        environment["NO_PROXY"] = "127.0.0.1,localhost"
        environment["no_proxy"] = "127.0.0.1,localhost"
        process: Optional[subprocess.Popen[Any]] = None
        with service_log.open("w", encoding="utf-8") as log_handle:
            process = subprocess.Popen(
                command,
                cwd=program,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                env=environment,
                start_new_session=True,
            )
            startup_timeout = float(raw_runtime.get("startup_timeout", 45))
            deadline = time.monotonic() + startup_timeout
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            last_error = ""
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    last_error = f"service exited with code {process.returncode}"
                    break
                try:
                    with opener.open(entry_url, timeout=1) as response:
                        if int(response.status) < 500:
                            break
                except Exception as exc:  # server may be compiling or not bound yet
                    last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(0.2)
            else:
                last_error = f"service did not become ready within {startup_timeout:.0f}s"
            ready = False
            if process.poll() is None:
                try:
                    with opener.open(entry_url, timeout=2) as response:
                        ready = int(response.status) < 500
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
            if not ready:
                log_handle.flush()
                excerpt = service_log.read_text(encoding="utf-8", errors="replace")[-6000:]
                self._stop_runtime(process)
                raise RuntimeError(
                    f"Public web runtime failed to start: {last_error}\n{excerpt}"
                )
            try:
                yield {
                    "entry_url": entry_url,
                    "service_log": str(service_log),
                }
            finally:
                self._stop_runtime(process)

    @staticmethod
    def _stop_runtime(process: Optional[subprocess.Popen[Any]]) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)

    def certify(self, **kwargs: Any) -> Dict[str, Any]:
        kwargs["purpose"] = "certification"
        result = self.execute(**kwargs)
        if result.get("code_version") != hash_program(kwargs["program_path"]):
            raise RuntimeError("Program changed during certification")
        result["fresh_certification"] = True
        write_json(Path(result["result_path"]), result)
        return result

    def _node_command(self, spec_path: Path, result_path: Path) -> List[str]:
        try:
            import playwright
        except ImportError as exc:
            raise RuntimeError("Interactive judge requires the installed Playwright package") from exc
        driver_dir = Path(playwright.__file__).resolve().parent / "driver"
        node_bin = driver_dir / "node"
        driver_package = driver_dir / "package"
        script = Path(__file__).resolve().parents[3] / "scripts" / "interactive_judge_playwright.js"
        if not node_bin.is_file() or not driver_package.is_dir() or not script.is_file():
            raise RuntimeError("Playwright Node driver or interactive judge script is missing")
        return [str(node_bin), str(script), str(driver_package), str(spec_path), str(result_path)]

    @staticmethod
    def _execution_environment() -> Dict[str, str]:
        environment = dict(os.environ)
        if environment.get("PLAYWRIGHT_BROWSERS_PATH"):
            return environment
        project_root = Path(__file__).resolve().parents[3]
        isolated_browsers = project_root / ".local/runtime" / "research" / "playwright"
        if isolated_browsers.is_dir():
            environment["PLAYWRIGHT_BROWSERS_PATH"] = str(isolated_browsers)
        return environment

    def _import_evidence(
        self, result: Dict[str, Any], *, case_id: str, code_version: str
    ) -> List[Dict[str, Any]]:
        imported: List[Dict[str, Any]] = []
        seen: set[tuple[str, Optional[str]]] = set()
        for action in result.get("actions", []):
            checklist_id = action.get("checklist_id")
            for kind, path in action.get("evidence", {}).items():
                if not path or not Path(path).is_file():
                    continue
                key = (str(path), checklist_id)
                if key in seen:
                    continue
                seen.add(key)
                imported.append(
                    self.evidence_store.add_file(
                        path,
                        kind=kind,
                        case_id=case_id,
                        code_version=code_version,
                        checklist_id=checklist_id,
                        metadata={
                            "execution_id": result.get("execution_id"),
                            "action_index": action.get("index"),
                        },
                    )
                )
        for path, kind in (
            (result.get("console_path"), "console"),
            (result.get("service_log"), "service_log"),
            (result.get("video_path"), "video"),
        ):
            if path and Path(path).is_file():
                imported.append(
                    self.evidence_store.add_file(
                        path,
                        kind=kind,
                        case_id=case_id,
                        code_version=code_version,
                        metadata={"execution_id": result.get("execution_id")},
                    )
                )
        return imported
