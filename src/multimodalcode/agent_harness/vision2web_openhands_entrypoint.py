"""Archived experiment-1 OpenHands entry point.

It remains only for already-running pre-refactor smoke jobs. Active runs use
``vision2web_openhands_browser_entrypoint``.
"""

from __future__ import annotations

import os


def _enable_browser_interfaces() -> None:
    from openhands.sdk.tool import Tool
    from openhands_cli import utils
    from openhands_cli.stores import agent_store

    from .vision2web_browser_tools import Vision2WebBrowserToolSet

    trace_root = os.environ.get("MULTIMODALCODE_BROWSER_TRACE_ROOT")
    if not trace_root:
        raise RuntimeError("MULTIMODALCODE_BROWSER_TRACE_ROOT is required")

    original_tools = utils.get_default_cli_tools
    original_agent = utils.get_default_cli_agent

    def with_browser_tools(*, use_delegate_tool: bool = False):
        tools = original_tools(use_delegate_tool=use_delegate_tool)
        tools.append(
            Tool(
                name=Vision2WebBrowserToolSet.name,
                params={"trace_root": trace_root},
            )
        )
        return tools

    def with_browser_agent(llm):  # noqa: ANN001
        agent = original_agent(llm)
        return agent.model_copy(update={"tools": with_browser_tools()})

    # agent_store imports these callables by name, so patch both bindings.
    utils.get_default_cli_tools = with_browser_tools
    utils.get_default_cli_agent = with_browser_agent
    agent_store.get_default_cli_tools = with_browser_tools
    agent_store.get_default_cli_agent = with_browser_agent


def main() -> None:
    from .vision2web_experiment import normalize_vision2web_mode

    requested_mode = os.environ.get("MULTIMODALCODE_VISION2WEB_MODE", "")
    mode = normalize_vision2web_mode(requested_mode, scaffold="openhands")
    if mode not in {"browser_enabled", "guided_vsv"}:
        raise RuntimeError(
            "The browser-enabled entry point is only valid for "
            "browser_enabled/guided_vsv"
        )
    _enable_browser_interfaces()
    from openhands_cli.entrypoint import main as openhands_main

    openhands_main()


if __name__ == "__main__":
    main()
