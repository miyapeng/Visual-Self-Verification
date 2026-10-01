#!/usr/bin/env python3
"""Record the browser APIs present in the pinned Vision2Web runtime image."""

from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path


def describe(value: object) -> dict[str, object]:
    try:
        signature = str(inspect.signature(value))
    except (TypeError, ValueError):
        signature = None
    try:
        source = inspect.getsource(value)
    except (OSError, TypeError):
        source = None
    return {
        "module": getattr(value, "__module__", None),
        "qualname": getattr(value, "__qualname__", None),
        "signature": signature,
        "source_file": inspect.getsourcefile(value),
        "source": source,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    from browser_use.browser.profile import BrowserProfile, ProxySettings
    from browser_use.browser.session import BrowserSession
    from browser_use.browser import events as browser_events
    from browser_use.mcp.server import BrowserUseServer
    from openhands.tools.browser_use.definition import BrowserToolSet
    from openhands.tools.browser_use.impl import BrowserToolExecutor

    targets = {
        "BrowserProfile": BrowserProfile,
        "ProxySettings": ProxySettings,
        "BrowserSession": BrowserSession,
        "BrowserUseServer._init_browser_session": BrowserUseServer._init_browser_session,
        "BrowserToolSet.create": BrowserToolSet.create,
        "BrowserToolExecutor.get_state": BrowserToolExecutor.get_state,
        "BrowserUseServer._get_browser_state": BrowserUseServer._get_browser_state,
        "BrowserUseServer._click": BrowserUseServer._click,
        "BrowserUseServer._type_text": BrowserUseServer._type_text,
        "BrowserSession.get_current_page_url": BrowserSession.get_current_page_url,
        "BrowserSession.get_or_create_cdp_session": BrowserSession.get_or_create_cdp_session,
    }
    for name in (
        "get_current_page",
        "get_current_page_url",
        "get_tabs",
        "take_screenshot",
        "get_dom_element_by_index",
    ):
        value = getattr(BrowserSession, name, None)
        if value is not None:
            targets[f"BrowserSession.{name}"] = value
    for name in sorted(dir(browser_events)):
        if name.endswith("Event") and name[0].isupper():
            value = getattr(browser_events, name)
            if inspect.isclass(value):
                targets[f"browser_events.{name}"] = value
    record = {
        "browser_use_version": __import__("importlib.metadata").metadata.version("browser-use"),
        "browser_profile_fields": {
            name: {
                "annotation": str(field.annotation),
                "default": repr(field.default),
                "description": field.description,
            }
            for name, field in BrowserProfile.model_fields.items()
        },
        "browser_session_fields": {
            name: {
                "annotation": str(field.annotation),
                "default": repr(field.default),
                "description": field.description,
            }
            for name, field in BrowserSession.model_fields.items()
        },
        "targets": {name: describe(value) for name, value in targets.items()},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
