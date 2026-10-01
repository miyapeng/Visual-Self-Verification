"""Enable OpenHands' unmodified native BrowserToolSet for Vision2Web.

The official condition never imports this module. ``browser_enabled`` and
``guided_vsv`` retain the official CLI agent and add the complete upstream
``BrowserToolSet`` without changing its tool names, schemas, defaults, or
observations.
"""

from __future__ import annotations

import os


def _enable_browser_tools() -> None:
    from openhands.sdk.tool import Tool
    from openhands.tools.browser_use.definition import BrowserToolSet
    from openhands_cli import utils
    from openhands_cli.stores import agent_store

    original_tools = utils.get_default_cli_tools
    original_agent = utils.get_default_cli_agent

    def with_browser_tools(*, use_delegate_tool: bool = False):
        return [
            *original_tools(use_delegate_tool=use_delegate_tool),
            Tool(name=BrowserToolSet.name),
        ]

    def with_browser_agent(llm):  # noqa: ANN001
        agent = original_agent(llm)
        return agent.model_copy(update={"tools": with_browser_tools()})

    # The local conversation store imports these bindings directly.
    utils.get_default_cli_tools = with_browser_tools
    utils.get_default_cli_agent = with_browser_agent
    agent_store.get_default_cli_tools = with_browser_tools
    agent_store.get_default_cli_agent = with_browser_agent


def _close_native_browser() -> None:
    """Release OpenHands' shared browser executor before the CLI process exits.

    ``BrowserToolSet`` keeps its executor in a class variable so parent agents
    and subagents can share one Chromium session.  In a short-lived headless
    run that reference also keeps the executor thread alive after the CLI has
    printed its final message.  Explicitly invoking the upstream executor's
    own ``close`` method gives the process the same deterministic lifetime as
    the browser-free official condition.
    """
    from openhands.tools.browser_use.definition import BrowserToolSet

    executor = BrowserToolSet._shared_executor
    if executor is not None:
        executor.close()


def main() -> None:
    from .vision2web_experiment import normalize_vision2web_mode

    requested_mode = os.environ.get("MULTIMODALCODE_VISION2WEB_MODE", "")
    mode = normalize_vision2web_mode(requested_mode, scaffold="openhands")
    if mode not in {"browser_enabled", "guided_vsv"}:
        raise RuntimeError(
            "This entry point is only valid for browser_enabled/guided_vsv"
        )
    _enable_browser_tools()
    from openhands_cli.entrypoint import main as openhands_main

    try:
        openhands_main()
    finally:
        _close_native_browser()


if __name__ == "__main__":
    main()
