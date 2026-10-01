#!/usr/bin/env python3
"""Persist native browser-use runtime metadata/source from the frozen image."""

from __future__ import annotations

import importlib.metadata
import inspect
import json
import os
import platform
from pathlib import Path


def source(obj) -> str:
    try:
        return inspect.getsource(obj)
    except Exception as exc:
        return f"SOURCE_UNAVAILABLE: {type(exc).__name__}: {exc}\n"


def main() -> int:
    output = Path(os.environ["MMCODE_PROBE_OUTPUT"])
    output.mkdir(parents=True, exist_ok=True)

    from browser_use.browser.session import BrowserSession
    from browser_use.mcp.server import BrowserUseServer
    from openhands.tools.browser_use.impl import BrowserToolExecutor

    metadata = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "uid": os.getuid(),
        "cwd": os.getcwd(),
        "display": os.environ.get("DISPLAY"),
        "chrome_docker_args": os.environ.get("CHROME_DOCKER_ARGS"),
        "versions": {},
        "chromium": BrowserToolExecutor.check_chromium_available(),
    }
    for package in ("browser-use", "openhands", "playwright"):
        try:
            metadata["versions"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            metadata["versions"][package] = None
    (output / "runtime.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )

    targets = {
        "browser_use_server_init.py": getattr(BrowserUseServer, "_init_browser_session"),
        "browser_session_start.py": getattr(BrowserSession, "start", BrowserSession),
        "browser_session_init.py": BrowserSession.__init__,
        "openhands_ensure_initialized.py": BrowserToolExecutor._ensure_initialized,
    }
    for name, target in targets.items():
        (output / name).write_text(source(target), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
