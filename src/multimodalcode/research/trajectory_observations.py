"""Leakage-safe observational adapters for existing benchmark trajectories.

The adapters deliberately retain only compact references to public model inputs,
actions, edits, and observations.  Official evaluator fields are never copied
from benchmark rows into the normalized event stream.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


EVENT_SCHEMA = "multimodalcode-minimal-observation-event-1"
CASE_SCHEMA = "multimodalcode-trajectory-case-audit-1"

_EDIT_RE = re.compile(
    r"\bapply_patch\b|\bsed\s+-i\b|\b(?:cat|tee)\b[^\n]*(?:>|>>)"
    r"|\b(?:cp|mv|rm)\s+[^\n]+"
    r"|\bgit\s+(?:checkout|restore|stash\s+(?:pop|apply))\b"
    r"|(?:^|\s)(?:>|>>)(?:\s|$)",
    re.I | re.M,
)
_TEST_RE = re.compile(
    r"\bpytest\b|\b(?:npm|pnpm|yarn)\s+(?:test|run\s+(?:test|build|check|lint|"
    r"rollup|compile|typecheck|validate))\b"
    r"|\b(?:jest|mocha)\b|\b(?:npx\s+)?playwright\s+test\b|\bmake\s+(?:test|check)\b"
    r"|\b(?:python\d*|node)\b[^\n]*(?:test|check|smoke|repro)"
    r"|\bcurl\b[^\n]*(?:localhost|127\.0\.0\.1)"
    r"|\bbash\b[^\n]*(?:start|test|check|smoke)[^\n]*\.sh",
    re.I,
)
_INSTALL_RE = re.compile(r"\b(?:npm|pnpm|yarn)\s+(?:i|install)\b|\bpip\s+install\b", re.I)
_INSPECT_RE = re.compile(
    r"(?:^|[;&|]\s*)\s*(?:ls|find|rg|grep|sed\s+-n|cat|head|tail|git\s+(?:status|diff))\b",
    re.I | re.M,
)
_VISUAL_RE = re.compile(r"visual_tool|screenshot|render-html|view-image", re.I)
_SUBMIT_RE = re.compile(r"COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT|<boltAction\s+type=[\"']finish", re.I)
_RETURN_CODE_RE = re.compile(r"<returncode>\s*(-?\d+)\s*</returncode>", re.I)
_CONCRETE_TEXT_FAILURE_RE = re.compile(
    r"(?:^|\n)\s*(?:Traceback \(most recent call last\):|FATAL(?: ERROR)?[: ]|npm ERR!|npm error code\b|"
    r"(?:Syntax|Type|Reference|Assertion|Import|ModuleNotFound)?Error:)"
    r"|No replacement was performed|Invalid [`'\"]?(?:path|command|tool)[`'\"]? parameter"
    r"|tests? failed|FAILED \([^\n]*\)|\b\d+\s+failed\b"
    r"|\bEmpty Page:\s*True\b|\bVisual Audit Failed:|\bInternal Audit Error:"
    r"|\bRuntime Error:|\bTechnical Error:|\bCRITICAL:\s*Failed"
    r"|\bInternal server error:|\bFailed to parse source\b",
    re.I,
)
_AUDIT_TIMEOUT_RE = re.compile(r"\bAudit timed out after\b|\bcommand timed out\b", re.I)
_STATUS_FAILED_RE = re.compile(r"\bStatus:\s*(?:failed|failure|error)\b", re.I)
_STATUS_SUCCESS_RE = re.compile(r"\bStatus:\s*success\b", re.I)
_PROXY_407_RE = re.compile(r"407\s*\(Proxy Authentication Required\)", re.I)
_EMPTY_PAGE_FALSE_RE = re.compile(r"\bEmpty Page:\s*False\b", re.I)
_FILE_ACTION_RE = re.compile(r"<boltAction\s+type=[\"']file[\"']([^>]*)>", re.I)
_BOLT_ACTION_RE = re.compile(r"<boltAction\s+type=[\"']([^\"']+)[\"']([^>]*)>", re.I)
_FILE_PATH_RE = re.compile(r"filePath=[\"']([^\"']+)[\"']", re.I)
_ABS_PATH_RE = re.compile(r"(?<![\w.-])(/[\w.@+~/-]+(?:\.[A-Za-z0-9_-]+)?)")
_REDIRECT_TARGET_RE = re.compile(r"(?:>|>>)\s*(['\"]?)([^\s'\"]+)\1")
_CD_PATH_RE = re.compile(r"(?:^|[;&|]\s*)\s*cd\s+([^\s;&|]+)", re.I)
_MARKDOWN_FILE_RE = re.compile(r"^##\s+([^\n`]+)", re.M)
_PYTHON_OPEN_WRITE_RE = re.compile(
    r"(?:with\s+)?open\(\s*(?P<quote>['\"])(?P<path>[^'\"]+)(?P=quote)\s*,\s*"
    r"(?P<mode_quote>['\"])(?P<mode>[^'\"]*[wax+][^'\"]*)(?P=mode_quote)",
    re.I,
)
_PYTHON_PATH_WRITE_RE = re.compile(
    r"Path\(\s*(?P<quote>['\"])(?P<path>[^'\"]+)(?P=quote)\s*\)\s*\.\s*"
    r"(?:write_text|write_bytes|touch)\s*\(",
    re.I,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_stringify(item) for item in value)
    if isinstance(value, dict):
        if value.get("type") == "text":
            return _stringify(value.get("text"))
        if value.get("type") == "image_url":
            image = value.get("image_url") or {}
            url = _stringify(image.get("url") if isinstance(image, dict) else image)
            return (
                "[image-url "
                f"sha256={hashlib.sha256(url.encode('utf-8', errors='replace')).hexdigest()} "
                f"encoded_chars={len(url)}]"
            )
        return "\n".join(
            f"{key}: {_stringify(item)}" for key, item in sorted(value.items())
        )
    return str(value)


def _compact(text: str, limit: int = 180) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _text_ref(value: Any, *, snippet: bool = True) -> dict[str, Any]:
    text = _stringify(value)
    result: dict[str, Any] = {
        "sha256": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(),
        "chars": len(text),
    }
    if snippet and text:
        result["snippet"] = _compact(text)
    return result


def _paths(text: str, extras: Iterable[str] = ()) -> list[str]:
    candidates = list(extras) + _ABS_PATH_RE.findall(text)
    candidates.extend(match.group(2) for match in _REDIRECT_TARGET_RE.finditer(text))
    result: list[str] = []
    for candidate in candidates:
        candidate = candidate.rstrip(".,:;)")
        if candidate and candidate not in result:
            result.append(candidate)
        if len(result) == 20:
            break
    return result


def _auxiliary_path(path: str) -> bool:
    normalized = path.strip("'\"").replace("\\", "/").lower()
    name = normalized.rsplit("/", 1)[-1]
    return bool(
        normalized.startswith("/tmp/")
        or "/tmp/" in normalized
        or normalized == "/dev/null"
        or re.fullmatch(r"&\d+", normalized) is not None
        or name in {"patch.txt", "patch.diff", "patch.patch"}
        or name.endswith((".patch", ".diff"))
        or name.endswith(
            (".backup", ".bak", ".orig", ".fix_attempt", ".saved", ".debug")
        )
        or re.search(r"(?:\.backup\d*|\.bak\d*|\.original|\.fixed)(?:\.[a-z0-9_-]+)?$", name)
        or name in {"changes_summary.md", "change_summary.md", "final_summary.md"}
        or name.startswith(
            (
                "edit_file",
                "apply_fix",
                "fix_script",
                "fix_",
                "before_fix",
                "reproduce.",
                "fix_understanding",
                "backup_",
                "patch_",
            )
        )
        or name == "fix.py"
        or name.startswith(("test_", "repro_", "reproduce_", "debug_"))
        or re.search(r"(?:^|[_-])(?:test|repro)\.[a-z0-9]+$", name)
    )


def _verification_path(path: str) -> bool:
    """Return whether a write target belongs to the verification state.

    A repository is not a single revision for evidence purposes.  Production
    code and the tests/fixtures used to certify it are separate state axes.  In
    particular, a temporary write under ``test/fixtures`` must not make a build
    of otherwise unchanged production code look stale; it *does* change the
    verification environment and is tracked separately.
    """

    normalized = path.strip("'\"").replace("\\", "/").lower()
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    name = parts[-1] if parts else normalized
    verification_dirs = {
        "test",
        "tests",
        "testing",
        "__tests__",
        "spec",
        "specs",
        "fixture",
        "fixtures",
    }
    return bool(
        verification_dirs.intersection(parts)
        or name.startswith(
            (
                "test_",
                "repro_",
                "reproduce_",
                "verify_",
                "verification_",
                "final_verification",
                "final_verify",
                "smoke_",
                "manual_test",
                "debug_",
                "check_",
                "verify_",
                "verification_",
                "compare_fix",
                "demonstration",
                "simple_test",
                "lib_test",
                "comprehensive_test",
                "pr_test",
                "edge_case_test",
            )
        )
        or re.search(r"(?:^|[._-])(?:test|spec)\.[a-z0-9]+$", name)
    )


def _write_semantic_categories(write_paths: list[str]) -> set[str]:
    """Classify write targets without conflating code, tests, and artifacts."""

    if not write_paths:
        # A write-like action with no recoverable path is conservatively a
        # production edit.  This preserves the old behaviour for opaque tools.
        return {"program_edit"}
    categories: set[str] = set()
    for path in write_paths:
        if _verification_path(path):
            categories.add("verification_edit")
        elif not _auxiliary_path(path):
            categories.add("program_edit")
    return categories


def _command_surface(text: str) -> str:
    """Remove here-document payloads before classifying shell semantics."""
    lines = text.splitlines()
    if not lines:
        return ""
    first = lines[0]
    match = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", first)
    if not match:
        return text
    delimiter = match.group(1)
    closing = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == delimiter),
        None,
    )
    tail = lines[closing + 1 :] if closing is not None else []
    return "\n".join([first, *tail])


def _executed_heredoc_payload(text: str) -> str:
    """Return a heredoc program only when the same command executes it.

    Coding agents frequently create a temporary Python patcher and invoke it in
    the tail of one shell action.  Looking only at the shell surface sees the
    temporary file write but misses the production files changed by the
    patcher.  We inspect only literal write targets and never execute payloads.
    """

    lines = text.splitlines()
    if not lines:
        return ""
    first = lines[0]
    match = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", first)
    if not match:
        return ""
    delimiter = match.group(1)
    closing = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == delimiter),
        None,
    )
    if closing is None:
        return ""
    payload = "\n".join(lines[1:closing])
    tail = "\n".join(lines[closing + 1 :])
    inline_executor = bool(
        re.search(r"\b(?:python\d*|node|ruby|perl|bash|sh)\b[^\n]*<<", first, re.I)
    )
    target_match = re.search(r"(?:>|>>)\s*(['\"]?)([^\s'\"]+)\1\s*<<", first)
    target = target_match.group(2) if target_match else ""
    target_executed = bool(
        target
        and re.search(
            rf"\b(?:python\d*|node|ruby|perl|bash|sh)\b[^\n]*"
            rf"(?:{re.escape(target)}|{re.escape(Path(target).name)})(?:\s|$)",
            tail,
            re.I,
        )
    )
    return payload if inline_executor or target_executed else ""


def _indirect_write_paths(text: str) -> list[str]:
    """Extract literal files mutated by an executed heredoc patcher."""

    payload = _executed_heredoc_payload(text)
    if not payload:
        return []
    first = text.splitlines()[0]
    target_match = re.search(r"(?:>|>>)\s*(['\"]?)([^\s'\"]+)\1\s*<<", first)
    target = target_match.group(2).lower() if target_match else ""
    python_payload = bool(
        re.search(r"\bpython\d*\b[^\n]*<<", first, re.I) or target.endswith(".py")
    )
    shell_payload = bool(
        re.search(r"\b(?:bash|sh)\b[^\n]*<<", first, re.I)
        or target.endswith((".sh", ".bash"))
    )
    paths: list[str] = []
    if python_payload:
        for pattern in (_PYTHON_OPEN_WRITE_RE, _PYTHON_PATH_WRITE_RE):
            for match in pattern.finditer(payload):
                path = match.group("path")
                if path not in paths:
                    paths.append(path)
    # Shell patchers embedded in a heredoc are less common, but their direct
    # redirections and sed/cp/mv targets are still recoverable by our existing
    # surface parser.
    payload_surface = _command_surface(payload)
    if shell_payload and re.search(
        r"\bsed\s+-i\b|\b(?:cp|mv)\b|(?:>|>>)", payload_surface, re.I
    ):
        for path in _edit_paths(payload_surface, _paths(payload_surface)):
            if path not in paths:
                paths.append(path)
    return paths


def _edit_paths(surface: str, discovered_paths: list[str]) -> list[str]:
    """Return likely write targets, excluding paths used only as cwd/input.

    Shell trajectories commonly use ``cd /testbed && git diff ... > patch.txt``.
    Treating every absolute path in that command as a write target advances the
    program version even though only the auxiliary patch file changed.  For a
    pure redirection write, the redirect target is the relevant path; mutating
    commands such as ``sed -i`` retain their non-cwd operands.
    """

    cwd_paths = {match.group(1).strip("'\"") for match in _CD_PATH_RE.finditer(surface)}
    redirections = [match.group(2) for match in _REDIRECT_TARGET_RE.finditer(surface)]
    mutation_targets = _mutation_target_paths(surface)
    if mutation_targets:
        return mutation_targets
    mutates_existing_path = bool(
        re.search(
            r"\bsed\s+-i\b|\b(?:cp|mv|rm)\b|"
            r"\bgit\s+(?:checkout|restore|stash\s+(?:pop|apply))\b",
            surface,
            re.I,
        )
    )
    if redirections and not mutates_existing_path:
        return redirections
    return [path for path in discovered_paths if path not in cwd_paths]


def _looks_like_path_token(token: str) -> bool:
    token = token.strip("'\"")
    return bool(
        token
        and token not in {"HEAD", "--", ".", ".."}
        and not token.startswith("-")
        and re.match(r"^\d*(?:>|<)", token) is None
        and (
            "/" in token
            or token.startswith(".")
            or re.search(r"\.[A-Za-z0-9_-]{1,12}$", token)
        )
    )


def _mutation_target_paths(surface: str) -> list[str]:
    """Recover common relative write targets from shell command surfaces."""

    targets: list[str] = []
    for segment in re.split(r"(?:&&|\|\||;|\n)", surface):
        segment = segment.strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment, comments=False, posix=True)
        except ValueError:
            continue
        if not tokens:
            continue
        # Strip a leading `cd path` fragment if a malformed split leaves one.
        command_index = 0
        if tokens[0] == "cd":
            continue
        command = Path(tokens[command_index]).name
        candidates: list[str] = []
        if command == "sed" and any(token == "-i" or token.startswith("-i") for token in tokens[1:]):
            path_tokens = [token for token in tokens[1:] if _looks_like_path_token(token)]
            if path_tokens:
                candidates.append(path_tokens[-1])
        elif command in {"cp", "mv"}:
            operands = [token for token in tokens[1:] if not token.startswith("-")]
            if len(operands) >= 2:
                candidates.append(operands[-1])
        elif command == "rm":
            candidates.extend(
                token for token in tokens[1:] if _looks_like_path_token(token)
            )
        elif command == "git" and len(tokens) >= 2:
            operation = tokens[1]
            if operation in {"checkout", "restore"}:
                start = tokens.index("--") + 1 if "--" in tokens else 2
                path_tokens = [
                    token for token in tokens[start:] if _looks_like_path_token(token)
                ]
                candidates.extend(path_tokens or ["<working-tree>"])
            elif operation == "stash" and len(tokens) >= 3 and tokens[2] in {"pop", "apply"}:
                candidates.append("<working-tree>")
        for candidate in candidates:
            if candidate not in targets:
                targets.append(candidate)
    return targets


def _walk_dicts(item: Any) -> Iterable[dict[str, Any]]:
    if isinstance(item, dict):
        yield item
        for child in item.values():
            yield from _walk_dicts(child)
    elif isinstance(item, list):
        for child in item:
            yield from _walk_dicts(child)


def _observation_classification(value: Any, error_code: Any = None) -> dict[str, Any]:
    """Classify evidence actually present in an observation.

    ``failed`` is deliberately not synonymous with a concrete program failure:
    the released InteractWeb visual auditor also returns that status when its
    eight-step exploration budget expires.  Likewise, a non-empty page with an
    isolated proxy-authentication error is environment noise rather than proof
    that the generated program failed.
    """

    text = _stringify(value)
    basis: list[str] = []
    return_codes = [int(match.group(1)) for match in _RETURN_CODE_RE.finditer(text)]
    if any(code != 0 for code in return_codes):
        basis.append("nonzero_returncode")
    elif return_codes:
        basis.append("zero_returncode")
    if error_code not in (None, "", 0, "0", False):
        basis.append("structured_error_code")
    if _CONCRETE_TEXT_FAILURE_RE.search(text):
        basis.append("concrete_error_text")
    if _AUDIT_TIMEOUT_RE.search(text):
        basis.append("audit_timeout")
    if _STATUS_FAILED_RE.search(text):
        basis.append("text_failed_status")
    if _STATUS_SUCCESS_RE.search(text):
        basis.append("text_success_status")

    structured = list(_walk_dicts(value))
    for item in structured:
        status = str(item.get("status", "")).lower()
        if status in {"failed", "failure", "error"}:
            basis.append("structured_failed_status")
        elif status in {"success", "passed", "pass", "ok"}:
            basis.append("structured_success_status")
        if item.get("start_error") is True:
            basis.append("structured_start_error")
        elif item.get("start_error") is False and re.search(
            r"^\s*Success\s*$", _stringify(item.get("start_results")), re.I
        ):
            basis.append("structured_start_success")
        if _stringify(item.get("install_error")).strip():
            basis.append("structured_install_error")
        logs = item.get("logs")
        if logs not in (None, "", []):
            log_text = _stringify(logs)
            log_dicts = list(_walk_dicts(logs))
            has_error_log = any(
                str(log.get("type", "")).lower()
                in {"error", "fatal", "severe", "uncaught"}
                or str(log.get("level", "")).lower()
                in {"error", "fatal", "severe"}
                for log in log_dicts
            )
            if has_error_log or _CONCRETE_TEXT_FAILURE_RE.search(log_text):
                basis.append("structured_browser_error_log")
        thought = _stringify(item.get("thought"))
        if re.search(
            r"\b(?:not working|does not work|doesn't work|has not worked|hasn't worked|"
            r"failed to|could not|unable to|still (?:shows|showing|remains))\b",
            thought,
            re.I,
        ):
            basis.append("self_reported_check_failure")

    basis = sorted(set(basis))
    proxy_only_nonempty = bool(
        _PROXY_407_RE.search(text)
        and _EMPTY_PAGE_FALSE_RE.search(text)
        and not re.search(
            r"\b(?:500 \(Internal Server Error\)|Internal server error:|Failed to parse source|"
            r"Visual Audit Failed:|Internal Audit Error:|Empty Page:\s*True|npm error code|"
            r"Traceback \(most recent call last\):)\b",
            text,
            re.I,
        )
        and "nonzero_returncode" not in basis
        and "structured_error_code" not in basis
        and "structured_install_error" not in basis
        and "self_reported_check_failure" not in basis
    )
    if proxy_only_nonempty:
        return {
            "status": "environment_noise",
            "basis": sorted(set(basis + ["external_proxy_407_nonempty_page"])),
        }

    hard_failure_basis = {
        "nonzero_returncode",
        "structured_error_code",
        "structured_install_error",
        "self_reported_check_failure",
        "concrete_error_text",
        "structured_browser_error_log",
    }
    # start_error is concrete unless the special non-empty proxy case above
    # explains it.  A failed status alone, however, may only mean audit budget
    # exhaustion and is therefore kept inconclusive.
    if "structured_start_error" in basis:
        hard_failure_basis.add("structured_start_error")
    if hard_failure_basis.intersection(basis):
        return {"status": "concrete_failure", "basis": basis}
    if any(
        marker in basis
        for marker in ("audit_timeout", "text_failed_status", "structured_failed_status")
    ):
        return {"status": "inconclusive", "basis": basis}
    if any(
        marker in basis
        for marker in (
            "text_success_status",
            "structured_success_status",
            "structured_start_success",
        )
    ) or re.search(r"\bEnvironment Ready\.\s*Verify UI or Submit\.\b", text, re.I):
        return {"status": "check_pass", "basis": basis or ["explicit_success_text"]}
    return {"status": "neutral", "basis": basis}


def _failure_basis(value: Any, error_code: Any = None) -> list[str]:
    classification = _observation_classification(value, error_code)
    if classification["status"] != "concrete_failure":
        return []
    return list(classification["basis"])


def _action(
    tool: str | None,
    text: str,
    *,
    explicit: str | None = None,
    path_extras: Iterable[str] = (),
) -> dict[str, Any]:
    tool_name = str(tool or explicit or "unknown")
    lowered_tool = tool_name.lower()
    surface = _command_surface(text)
    indirect_write_paths = _indirect_write_paths(text)
    mutation_target_paths = _mutation_target_paths(surface)
    paths = _paths(
        surface,
        [*path_extras, *indirect_write_paths, *mutation_target_paths],
    )
    first_line = surface.strip().splitlines()[0].lower() if surface.strip() else ""
    categories: list[str] = []

    is_file_edit = lowered_tool == "file_editor" and first_line in {
        "create",
        "str_replace",
        "insert",
        "undo_edit",
    }
    is_file_view = lowered_tool == "file_editor" and first_line == "view"
    if is_file_edit or _EDIT_RE.search(surface) or indirect_write_paths:
        categories.append("edit")
        write_paths = paths if is_file_edit else _edit_paths(surface, paths)
        for path in indirect_write_paths:
            if path not in write_paths:
                write_paths.append(path)
        categories.extend(_write_semantic_categories(write_paths))
    if is_file_view or ("edit" not in categories and _INSPECT_RE.search(surface)):
        categories.append("inspect")
    if lowered_tool in {"terminal", "shell", "bash"}:
        categories.append("shell")
    if _TEST_RE.search(surface):
        categories.append("executable_check")
    if _INSTALL_RE.search(surface):
        categories.append("install")

    # A model may mention "screenshot" or "view image" in a reasoning/finish
    # message without executing anything.  Only explicit visual tools, or a
    # shell command whose executable surface invokes a visual utility, are
    # agent-active visual checks.  This prevents Vision2Web ``think`` events
    # that merely describe prototype screenshots from being promoted to
    # browser verification.
    visual = lowered_tool in {"visual_tool", "browser", "browsergym"} or bool(
        lowered_tool in {"terminal", "shell", "bash"} and _VISUAL_RE.search(surface)
    )
    if visual:
        reference_path = bool(
            re.search(r"/(?:prototypes?|resources?|issue_images?)/", surface, re.I)
        )
        categories.append("reference_visual_inspection" if reference_path else "generated_visual_check")
    if lowered_tool in {"finish", "submit"} or _SUBMIT_RE.search(text):
        categories.append("submit")
    if lowered_tool in {"ask_user", "clarify"}:
        categories.append("clarify")
    if explicit == "screenshot_validated":
        categories.append("generated_visual_check")
        categories.append("executable_check")
    if explicit == "file" and "edit" not in categories:
        categories.append("edit")
        categories.extend(_write_semantic_categories(paths))
    if explicit == "shell" and "shell" not in categories:
        categories.append("shell")
    if explicit == "finish" and "submit" not in categories:
        categories.append("submit")
    if explicit == "ask_user" and "clarify" not in categories:
        categories.append("clarify")

    return {
        "tool": tool_name,
        "categories": sorted(set(categories)),
        "content": _text_ref(text),
        "paths": paths,
    }


def _outcome(availability: str, status: str | None, source: str) -> dict[str, Any]:
    return {"availability": availability, "status": status, "source": source}


@dataclass
class _CaseBuilder:
    benchmark: str
    task: str
    source_trajectory: str | None
    external_outcome: dict[str, Any]
    generation_status: str
    verification_channel: str
    events: list[dict[str, Any]] = field(default_factory=list)
    version: int = 0
    verification_version: int = 0
    visible_events: int = 0
    visible_chars: int = 0
    counters: Counter[str] = field(default_factory=Counter)
    saw_failure: bool = False
    saw_visual_check: bool = False
    last_edit_sequence: int | None = None
    last_verification_edit_sequence: int | None = None
    last_executable_check_sequence: int | None = None
    last_executable_check_version: int | None = None
    last_executable_check_verification_version: int | None = None
    last_visual_check_sequence: int | None = None
    last_visual_check_version: int | None = None
    last_failure_sequence: int | None = None
    last_failure_version: int | None = None
    last_passing_check_sequence: int | None = None
    last_passing_check_version: int | None = None
    last_passing_executable_check_sequence: int | None = None
    last_passing_executable_check_version: int | None = None
    last_passing_executable_check_verification_version: int | None = None
    last_passing_visual_check_sequence: int | None = None
    last_passing_visual_check_version: int | None = None
    last_observation_status: str | None = None
    last_observation_status_sequence: int | None = None
    last_observation_status_version: int | None = None
    pending_check_categories: set[str] = field(default_factory=set)
    pending_check_version: int | None = None
    pending_check_verification_version: int | None = None
    last_submit_sequence: int | None = None

    def absorb(self, value: Any) -> None:
        text = _stringify(value)
        self.visible_events += 1
        self.visible_chars += len(text)

    def emit(
        self,
        *,
        source_ref: str,
        visible_value: Any = "",
        public_request: Any | None = None,
        edit: dict[str, Any] | None = None,
        actions: list[dict[str, Any]] | None = None,
        observation: Any | None = None,
        error_code: Any = None,
    ) -> None:
        actions = actions or []
        event_sequence = len(self.events)
        categories = {category for action in actions for category in action["categories"]}
        if edit is not None and "program_units" not in edit and "verification_units" not in edit:
            # Backward-compatible path for adapters/fixtures that predate the
            # two-axis edit schema: their edits were production edits.
            program_edit_units = int(edit.get("units", 1))
            verification_edit_units = 0
        else:
            program_edit_units = int((edit or {}).get("program_units", 0))
            verification_edit_units = int((edit or {}).get("verification_units", 0))
        if program_edit_units:
            self.version += 1
            self.pending_check_categories.clear()
            self.pending_check_version = None
            self.pending_check_verification_version = None
            self.last_edit_sequence = event_sequence
            self.counters["program_edit_events"] += 1
            self.counters["program_edit_units"] += program_edit_units
            if self.saw_failure:
                self.counters["edit_after_failure"] += 1
            if self.saw_visual_check:
                self.counters["edit_after_visual_check"] += 1
        if verification_edit_units:
            self.verification_version += 1
            self.pending_check_categories.clear()
            self.pending_check_version = None
            self.pending_check_verification_version = None
            self.last_verification_edit_sequence = event_sequence
            self.counters["verification_edit_events"] += 1
            self.counters["verification_edit_units"] += verification_edit_units
        if edit is not None:
            self.counters["edit_events"] += 1
            self.counters["edit_units"] += int(edit.get("units", 1))
        for category in categories:
            self.counters[f"action:{category}"] += 1
        if "generated_visual_check" in categories:
            self.saw_visual_check = True
            self.last_visual_check_sequence = event_sequence
            self.last_visual_check_version = self.version
        if "executable_check" in categories:
            self.last_executable_check_sequence = event_sequence
            self.last_executable_check_version = self.version
            self.last_executable_check_verification_version = self.verification_version
        check_categories = categories.intersection(
            {"executable_check", "generated_visual_check"}
        )
        if check_categories:
            self.pending_check_categories = set(check_categories)
            self.pending_check_version = self.version
            self.pending_check_verification_version = self.verification_version
        if "submit" in categories:
            self.last_submit_sequence = event_sequence

        observation_ref = None
        if observation is not None:
            observation_text = _stringify(observation)
            classification = _observation_classification(observation, error_code)
            verification_categories = (
                sorted(self.pending_check_categories)
                if (
                    self.pending_check_version == self.version
                    and self.pending_check_verification_version == self.verification_version
                )
                else []
            )
            if (
                classification["status"] == "neutral"
                and verification_categories
                and "zero_returncode" in classification["basis"]
            ):
                classification = {
                    "status": "check_pass",
                    "basis": sorted(
                        set(classification["basis"] + ["checked_command_zero_returncode"])
                    ),
                }
            status = str(classification["status"])
            basis = list(classification["basis"])
            observation_ref = {
                "content": _text_ref(observation_text),
                "status": status,
                "failure_signal": status == "concrete_failure",
                "failure_basis": basis,
                "verification_categories": verification_categories,
            }
            self.counters["observations"] += 1
            self.counters[f"observation_status:{status}"] += 1
            if status != "neutral":
                self.last_observation_status = status
                self.last_observation_status_sequence = event_sequence
                self.last_observation_status_version = self.version
            if status == "concrete_failure":
                self.counters["failure_observations"] += 1
                self.saw_failure = True
                self.last_failure_sequence = event_sequence
                self.last_failure_version = self.version
            elif status == "check_pass" and verification_categories:
                self.counters["passing_check_observations"] += 1
                self.last_passing_check_sequence = event_sequence
                self.last_passing_check_version = self.version
                if "executable_check" in verification_categories:
                    self.last_passing_executable_check_sequence = event_sequence
                    self.last_passing_executable_check_version = self.version
                    self.last_passing_executable_check_verification_version = (
                        self.verification_version
                    )
                if "generated_visual_check" in verification_categories:
                    self.last_passing_visual_check_sequence = event_sequence
                    self.last_passing_visual_check_version = self.version
            self.pending_check_categories.clear()
            self.pending_check_version = None
            self.pending_check_verification_version = None

        record = {
            "schema": EVENT_SCHEMA,
            "benchmark": self.benchmark,
            "task": self.task,
            "sequence": len(self.events),
            "source_ref": source_ref,
            "program_version": {"id": f"v{self.version}", "kind": "edit_ordinal"},
            "verification_version": {
                "id": f"t{self.verification_version}",
                "kind": "verification_edit_ordinal",
            },
            "public_request": _text_ref(public_request) if public_request is not None else None,
            "model_context": {
                "approx_visible_events_before": self.visible_events,
                "approx_visible_chars_before": self.visible_chars,
                "exactness": "raw-history proxy; scaffold compaction may differ",
            },
            "edit": edit,
            "executed_action": actions or None,
            "observation": observation_ref,
            "external_outcome": self.external_outcome,
        }
        self.events.append(record)
        if public_request is not None:
            self.counters["public_requests"] += 1
        self.absorb(visible_value)

    def finish(self) -> dict[str, Any]:
        edit_boundary = self.last_edit_sequence if self.last_edit_sequence is not None else -1
        evidence_state_boundary = max(
            edit_boundary,
            self.last_verification_edit_sequence
            if self.last_verification_edit_sequence is not None
            else -1,
        )
        fresh_executable_check = bool(
            self.last_executable_check_sequence is not None
            and self.last_executable_check_sequence >= edit_boundary
            and self.last_executable_check_version == self.version
        )
        fresh_visual_check = bool(
            self.last_visual_check_sequence is not None
            and self.last_visual_check_sequence >= edit_boundary
            and self.last_visual_check_version == self.version
        )
        failure_signal_after_final_edit = bool(
            self.last_failure_sequence is not None
            and self.last_failure_sequence > edit_boundary
            and self.last_failure_version == self.version
        )
        fresh_passing_executable_check = bool(
            self.last_passing_executable_check_sequence is not None
            and self.last_passing_executable_check_sequence >= edit_boundary
            and self.last_passing_executable_check_version == self.version
        )
        fresh_executable_check_on_evidence_state = bool(
            fresh_executable_check
            and self.last_executable_check_sequence is not None
            and self.last_executable_check_sequence >= evidence_state_boundary
            and self.last_executable_check_verification_version
            == self.verification_version
        )
        fresh_passing_executable_check_on_evidence_state = bool(
            fresh_passing_executable_check
            and self.last_passing_executable_check_sequence is not None
            and self.last_passing_executable_check_sequence >= evidence_state_boundary
            and self.last_passing_executable_check_verification_version
            == self.verification_version
        )
        fresh_passing_visual_check = bool(
            self.last_passing_visual_check_sequence is not None
            and self.last_passing_visual_check_sequence >= edit_boundary
            and self.last_passing_visual_check_version == self.version
        )
        later_passing_check_after_final_failure = bool(
            failure_signal_after_final_edit
            and self.last_passing_check_sequence is not None
            and self.last_passing_check_version == self.version
            and self.last_passing_check_sequence
            > (self.last_failure_sequence if self.last_failure_sequence is not None else -1)
        )
        unresolved_concrete_failure = bool(
            failure_signal_after_final_edit and not later_passing_check_after_final_failure
        )
        repair_after_failure_without_recheck = bool(
            self.counters["edit_after_failure"]
            and (
                self.last_executable_check_sequence is None
                or self.last_executable_check_sequence < edit_boundary
            )
        )
        return {
            "schema": CASE_SCHEMA,
            "benchmark": self.benchmark,
            "task": self.task,
            "source_trajectory": self.source_trajectory,
            "trajectory_available": bool(self.source_trajectory),
            "generation_status": self.generation_status,
            "verification_channel": self.verification_channel,
            "external_outcome": self.external_outcome,
            "event_count": len(self.events),
            "program_versions": self.version + 1,
            "verification_versions": self.verification_version + 1,
            "public_request_count": self.counters["public_requests"],
            "edit_events": self.counters["edit_events"],
            "edit_units": self.counters["edit_units"],
            "program_edit_events": self.counters["program_edit_events"],
            "program_edit_units": self.counters["program_edit_units"],
            "verification_edit_events": self.counters["verification_edit_events"],
            "verification_edit_units": self.counters["verification_edit_units"],
            "inspect_actions": self.counters["action:inspect"],
            "shell_actions": self.counters["action:shell"],
            "executable_check_actions": self.counters["action:executable_check"],
            "reference_visual_inspection_actions": self.counters[
                "action:reference_visual_inspection"
            ],
            "generated_visual_check_actions": self.counters[
                "action:generated_visual_check"
            ],
            "submit_actions": self.counters["action:submit"],
            "clarify_actions": self.counters["action:clarify"],
            "observation_count": self.counters["observations"],
            "failure_observation_count": self.counters["failure_observations"],
            "concrete_failure_observation_count": self.counters[
                "observation_status:concrete_failure"
            ],
            "inconclusive_observation_count": self.counters[
                "observation_status:inconclusive"
            ],
            "environment_noise_observation_count": self.counters[
                "observation_status:environment_noise"
            ],
            "check_pass_observation_count": self.counters[
                "observation_status:check_pass"
            ],
            "passing_check_observation_count": self.counters[
                "passing_check_observations"
            ],
            "edit_after_failure_count": self.counters["edit_after_failure"],
            "edit_after_visual_check_count": self.counters["edit_after_visual_check"],
            "fresh_executable_check_on_final_version": fresh_executable_check,
            "fresh_executable_check_on_final_evidence_state": (
                fresh_executable_check_on_evidence_state
            ),
            "fresh_visual_check_on_final_version": fresh_visual_check,
            "fresh_passing_executable_check_on_final_version": fresh_passing_executable_check,
            "fresh_passing_executable_check_on_final_evidence_state": (
                fresh_passing_executable_check_on_evidence_state
            ),
            "fresh_passing_visual_check_on_final_version": fresh_passing_visual_check,
            "failure_signal_after_final_edit": failure_signal_after_final_edit,
            "concrete_failure_after_final_edit": failure_signal_after_final_edit,
            "later_passing_check_after_final_failure": later_passing_check_after_final_failure,
            "unresolved_concrete_failure_on_final_version": unresolved_concrete_failure,
            "last_verification_observation_status_on_final_version": (
                self.last_observation_status
                if self.last_observation_status_version == self.version
                else None
            ),
            "repair_after_failure_without_recheck": repair_after_failure_without_recheck,
            "submitted_without_fresh_executable_check": bool(
                self.last_submit_sequence is not None and not fresh_executable_check
            ),
            "submitted_without_fresh_executable_check_on_final_evidence_state": bool(
                self.last_submit_sequence is not None
                and not fresh_executable_check_on_evidence_state
            ),
            "submitted_after_final_failure_signal": bool(
                failure_signal_after_final_edit
                and self.last_submit_sequence is not None
                and self.last_submit_sequence
                > (
                    self.last_failure_sequence
                    if self.last_failure_sequence is not None
                    else -1
                )
            ),
            "submitted_after_unresolved_concrete_failure": bool(
                unresolved_concrete_failure
                and self.last_submit_sequence is not None
                and self.last_submit_sequence
                > (self.last_failure_sequence if self.last_failure_sequence is not None else -1)
            ),
            "approx_visible_chars": self.visible_chars,
        }


def _normalized_events(builder: _CaseBuilder, trajectory: dict[str, Any], source: Path) -> None:
    for raw in trajectory.get("events", []):
        sequence = raw.get("sequence", len(builder.events))
        source_ref = f"{source}#events/{sequence}"
        actor = str(raw.get("actor", "unknown"))
        text = _stringify(raw.get("text"))
        if actor == "system":
            builder.absorb(text)
            continue
        if actor == "user":
            builder.emit(
                source_ref=source_ref,
                visible_value=text,
                public_request=text,
            )
            continue
        if actor in {"assistant", "agent"}:
            commands = [str(value) for value in raw.get("tools", []) if value]
            if raw.get("tool"):
                commands = [text]
                tool = str(raw.get("tool"))
            else:
                tool = "shell"
            actions = [_action(tool, command) for command in commands]
            has_edit = any(
                {"program_edit", "verification_edit"}.intersection(action["categories"])
                for action in actions
            )
            edit = None
            if has_edit:
                program_units = sum(
                    "program_edit" in action["categories"] for action in actions
                )
                verification_units = sum(
                    "verification_edit" in action["categories"] for action in actions
                )
                edit = {
                    "units": program_units + verification_units,
                    "program_units": program_units,
                    "verification_units": verification_units,
                    "paths": sorted({path for action in actions for path in action["paths"]})[:20],
                    "content": _text_ref("\n".join(commands)),
                }
            builder.emit(
                source_ref=source_ref,
                visible_value=text + "\n" + "\n".join(commands),
                edit=edit,
                actions=actions,
            )
            continue
        if actor in {"tool", "environment"}:
            builder.emit(
                source_ref=source_ref,
                visible_value=text,
                observation=text,
                error_code=raw.get("error_code") or raw.get("exit_status"),
            )
            continue
        builder.absorb(text)


def analyze_swe_mm(run_root: Path, clean_summary: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    summary = json.loads(clean_summary.read_text(encoding="utf-8"))
    expected = {str(row["instance_id"]): row for row in summary["cases"]}
    trajectories: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in run_root.glob("agents/*/swe-mm/*/trajectory.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        trajectories[str(data.get("case_id"))] = (path, data)
    all_events: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    for task, result in sorted(expected.items()):
        item = trajectories.get(task)
        outcome = _outcome("available", str(result.get("outcome")), str(clean_summary))
        builder = _CaseBuilder(
            "swe_mm",
            task,
            str(item[0]) if item else None,
            outcome,
            "generated" if result.get("generation_complete") else "missing_generation",
            "native shell and image/file tools",
        )
        if item:
            _normalized_events(builder, item[1], item[0])
        all_events.extend(builder.events)
        cases.append(builder.finish())
    return all_events, cases


def analyze_vision2web(run_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    controller_path = run_root / "controller_state.json"
    controller = json.loads(controller_path.read_text(encoding="utf-8"))
    expected = controller["jobs"]
    trajectories: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in run_root.glob("agents/*/vision2web/official/*/trajectory.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        trajectories[str(data.get("case_id"))] = (path, data)
    all_events: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    for task, job in sorted(expected.items()):
        item = trajectories.get(task)
        builder = _CaseBuilder(
            "vision2web",
            task,
            str(item[0]) if item else None,
            _outcome("unavailable", None, "official Vision2Web judge deferred"),
            str(job.get("result_status") or "unknown"),
            "OpenHands native terminal, file, and visual tools",
        )
        if item:
            _normalized_events(builder, item[1], item[0])
        all_events.extend(builder.events)
        cases.append(builder.finish())
    return all_events, cases


def _interact_actions(content: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    actions: list[dict[str, Any]] = []
    file_paths: list[str] = []
    file_units = 0
    for match in _BOLT_ACTION_RE.finditer(content):
        action_type = match.group(1).lower()
        attributes = match.group(2)
        body_end = content.find("</boltAction>", match.end())
        body = content[match.end() : body_end] if body_end >= 0 else ""
        path_match = _FILE_PATH_RE.search(attributes)
        extras = [path_match.group(1)] if path_match else []
        action = _action(
            action_type,
            body,
            explicit=action_type,
            path_extras=extras,
        )
        actions.append(action)
        if action_type == "file" and "program_edit" in action["categories"]:
            file_units += 1
            file_paths.extend(action["paths"])
    edit = None
    if file_units:
        edit = {
            "units": file_units,
            "program_units": file_units,
            "verification_units": 0,
            "paths": sorted(set(file_paths))[:20],
            "content": _text_ref(content),
        }
    return actions, edit


def analyze_interactweb(
    run_root: Path, public_data: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # Whitelist only agent-visible/task-routing fields.  Ground truth, oracle,
    # and evaluation checklist fields in the same JSONL are intentionally ignored.
    expected: dict[str, dict[str, Any]] = {}
    for line in public_data.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        expected[str(row["id"])] = {
            "instruction": row.get("instruction", ""),
            "difficulty": row.get("difficulty"),
            "persona": row.get("persona"),
        }
    histories = {
        path.parent.name: path
        for path in run_root.glob(
            "shards/shard-*/interactweb/*/logs/*/interaction_history.json"
        )
    }
    pending = {
        path.parent.name
        for path in run_root.glob(
            "shards/shard-*/interactweb/*/logs/*/pending_evaluation.json"
        )
    }
    indexes = {
        path.parent.name
        for path in run_root.glob(
            "shards/shard-*/interactweb/*/workspaces/*/index.html"
        )
    }
    all_events: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    for task, public in sorted(expected.items()):
        history_path = histories.get(task)
        if history_path and task in indexes:
            generation_status = "trajectory_and_index"
        elif history_path:
            generation_status = "trajectory_without_index"
        elif task in indexes:
            generation_status = "index_without_trajectory"
        else:
            generation_status = "missing_trajectory_and_index"
        builder = _CaseBuilder(
            "interactweb",
            task,
            str(history_path) if history_path else None,
            _outcome("unavailable", None, "official InteractWeb judge deferred"),
            generation_status,
            "released Clarify/Implement/Verify/Submit loop",
        )
        if history_path:
            history = json.loads(history_path.read_text(encoding="utf-8"))
            for index, raw in enumerate(history.get("trajectory", [])):
                role = str(raw.get("role", "unknown"))
                content = _stringify(raw.get("content"))
                source_ref = f"{history_path}#trajectory/{index}"
                if role == "system":
                    builder.absorb(content)
                elif role == "user":
                    debug = raw.get("debug_info") or {}
                    if debug.get("is_final"):
                        # Terminal evaluator/deferred-judge records are emitted
                        # after the coding policy has stopped.  They are neither
                        # policy context nor a new public requirement.
                        continue
                    if debug:
                        # The released loop serializes tool/browser feedback as
                        # user-role messages.  Only `content` was actually sent
                        # back to the coding policy.  Debug traces are excluded:
                        # they contain the visual copilot's private chain and
                        # must not be promoted to policy-visible evidence.
                        builder.emit(
                            source_ref=source_ref,
                            visible_value=content,
                            observation=content,
                        )
                    else:
                        builder.emit(
                            source_ref=source_ref,
                            visible_value=content,
                            public_request=content,
                        )
                elif role == "assistant":
                    actions, edit = _interact_actions(content)
                    builder.emit(
                        source_ref=source_ref,
                        visible_value=content,
                        edit=edit,
                        actions=actions,
                    )
                else:
                    builder.absorb(content)
        else:
            # Preserve missingness while still recording the public request.  No
            # hidden benchmark field is included.
            builder.emit(
                source_ref=f"{public_data}#id={task}/instruction",
                visible_value=public["instruction"],
                public_request=public["instruction"],
            )
        case = builder.finish()
        case["evaluation_pending_record"] = task in pending
        case["workspace_index"] = task in indexes
        case["difficulty"] = public["difficulty"]
        case["persona"] = public["persona"]
        all_events.extend(builder.events)
        cases.append(case)
    return all_events, cases


def analyze_frontalk(
    run_root: Path, public_data: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    expected: list[str] = []
    for line in public_data.read_text(encoding="utf-8").splitlines():
        if line.strip():
            expected.append(str(json.loads(line)["id"]))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    messages_path = run_root / "messages.jsonl"
    for line in messages_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        task, message = json.loads(line)
        grouped[str(task)].append(message)
    all_events: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    for task in expected:
        messages = grouped.get(task, [])
        final_index = run_root / "t.9" / task / "index.html"
        builder = _CaseBuilder(
            "frontalk",
            task,
            str(messages_path) if messages else None,
            _outcome("unavailable", None, "official FronTalk WebVoyager judge deferred"),
            "complete" if final_index.is_file() and messages else "incomplete",
            "released ten-turn generation has no execution tool",
        )
        for index, message in enumerate(messages):
            role = str(message.get("role", "unknown"))
            content = _stringify(message.get("content"))
            source_ref = f"{messages_path}#dialogue={task}/message={index}"
            if role == "system":
                builder.absorb(content)
            elif role == "user":
                builder.emit(
                    source_ref=source_ref,
                    visible_value=content,
                    public_request=content,
                )
            elif role == "assistant":
                paths = [value.strip() for value in _MARKDOWN_FILE_RE.findall(content)][:20]
                action = {
                    "tool": "model_generation",
                    "categories": ["edit", "program_edit"],
                    "content": _text_ref(content),
                    "paths": paths,
                }
                builder.emit(
                    source_ref=source_ref,
                    visible_value=content,
                    edit={"units": max(1, len(paths)), "paths": paths, "content": _text_ref(content)},
                    actions=[action],
                )
            else:
                builder.absorb(content)
        case = builder.finish()
        case["final_index"] = final_index.is_file()
        all_events.extend(builder.events)
        cases.append(case)
    return all_events, cases


def aggregate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = [
        "trajectory_available",
        "edit_events",
        "program_edit_events",
        "verification_edit_events",
        "inspect_actions",
        "shell_actions",
        "executable_check_actions",
        "reference_visual_inspection_actions",
        "generated_visual_check_actions",
        "submit_actions",
        "failure_observation_count",
        "edit_after_failure_count",
        "edit_after_visual_check_count",
        "fresh_executable_check_on_final_version",
        "fresh_executable_check_on_final_evidence_state",
        "fresh_visual_check_on_final_version",
        "fresh_passing_executable_check_on_final_version",
        "fresh_passing_executable_check_on_final_evidence_state",
        "fresh_passing_visual_check_on_final_version",
        "failure_signal_after_final_edit",
        "concrete_failure_after_final_edit",
        "later_passing_check_after_final_failure",
        "unresolved_concrete_failure_on_final_version",
        "repair_after_failure_without_recheck",
        "submitted_without_fresh_executable_check",
        "submitted_without_fresh_executable_check_on_final_evidence_state",
        "submitted_after_final_failure_signal",
        "submitted_after_unresolved_concrete_failure",
    ]
    result: dict[str, Any] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        grouped[case["benchmark"]].append(case)
    for benchmark, rows in sorted(grouped.items()):
        outcomes = Counter(str(row["external_outcome"].get("status")) for row in rows)
        availability = Counter(row["external_outcome"]["availability"] for row in rows)
        generation = Counter(row["generation_status"] for row in rows)
        counts = {metric: sum(bool(row.get(metric)) for row in rows) for metric in metrics}
        conditional: dict[str, Any] = {}
        if availability.get("available"):
            for metric in metrics:
                table: dict[str, Any] = {}
                for flag in (False, True):
                    subset = [row for row in rows if bool(row.get(metric)) is flag]
                    resolved = sum(row["external_outcome"].get("status") == "resolved" for row in subset)
                    table[str(flag).lower()] = {
                        "cases": len(subset),
                        "resolved": resolved,
                        "resolved_rate": resolved / len(subset) if subset else None,
                    }
                conditional[metric] = table
        result[benchmark] = {
            "cases": len(rows),
            "metric_case_counts": counts,
            "metric_case_rates": {key: value / len(rows) for key, value in counts.items()},
            "generation_statuses": dict(sorted(generation.items())),
            "external_outcome_availability": dict(sorted(availability.items())),
            "external_outcomes": dict(sorted(outcomes.items())),
            "last_verification_observation_statuses": dict(
                sorted(
                    Counter(
                        str(row.get("last_verification_observation_status_on_final_version"))
                        for row in rows
                    ).items()
                )
            ),
            "resolved_conditioned_on_observed_behavior": conditional,
            "mean_events": sum(row["event_count"] for row in rows) / len(rows),
            "mean_approx_visible_chars": sum(row["approx_visible_chars"] for row in rows) / len(rows),
        }
    return result


def analyze_all(project_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Analyze only the canonical active research scope.

    Historical InteractWeb parsing remains available through
    :func:`analyze_interactweb` for provenance checks, but is deliberately not
    part of this default aggregate. All three Vision2Web levels are retained.
    """
    sources = {
        "frontalk": {
            "code_revision": "527c4b8b1ae0a7be33cec4e8958794d8a56cc660",
            "data_revision": "dccf7e98dbf3ebbeda9032397cefcb00d630bd8a",
        },
        "swe_mm": {
            "data_revision": "3548373bb5f604b55600884af34ac33e5a90ef66",
            "evaluator_revision": "7e578260da58400f307e435e43d1d2ab29d686f6",
            "scaffold_revision": "a83fcae82d2a08f0ee0c688f9d137b3566c097f8",
        },
        "vision2web": {
            "code_revision": "577f9397b3db8fc6d828adde254a830caa65d515",
            "data_revision": "8f03299d92b9bd852e93852d0c21e8a4848ab661",
        },
    }
    adapters = [
        analyze_frontalk(
            project_root / "runs/native_benchmarks/qwen35-9b-local-support-frontalk-text-full-20260818/frontalk",
            project_root / "data/frontalk/data.jsonl",
        ),
        analyze_swe_mm(
            project_root / "runs/swe_mm_official/qwen35-9b-mini-official-dev-s2",
            project_root
            / "runs/swe_mm_official/qwen35-9b-mini-official-dev-s2/submission_repaired_inputfix1/clean_local_summary.json",
        ),
        analyze_vision2web(
            project_root / "runs/vision2web_generation/qwen35-9b-openhands-official-v2"
        ),
    ]
    events = [event for adapter_events, _ in adapters for event in adapter_events]
    cases = [case for _, adapter_cases in adapters for case in adapter_cases]
    summary = {
        "schema": "multimodalcode-observational-trajectory-audit-2",
        "created_at_utc": _now(),
        "project_root": str(project_root),
        "sources": sources,
        "case_count": len(cases),
        "event_count": len(events),
        "benchmarks": aggregate_cases(cases),
        "interpretation_constraints": [
            "Only SWE-MM currently has case-level external evaluator outcomes.",
            "Vision2Web result_status is generation/runtime status, not an official score.",
            "FronTalk's released generation protocol has no execution tool; zero checks are a harness property.",
            "Context character counts are raw-history proxies, not exact post-compaction model tokens.",
            "Only observation content actually returned to the coding policy is classified; private debug traces are excluded.",
            "Concrete failure, inconclusive audit timeout, environment noise, and passing evidence are distinct statuses.",
            "InteractWeb-Bench is archived and excluded from this active aggregate.",
        ],
    }
    return events, cases, summary
