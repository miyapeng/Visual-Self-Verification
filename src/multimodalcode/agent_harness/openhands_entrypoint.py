"""Deterministic OpenHands CLI entry point for offline benchmark containers."""

from __future__ import annotations

import os


def _disable_external_skill_discovery() -> None:
    """Keep the official CLI/tools while avoiding mutable network-fetched skills."""
    from openhands.sdk.context import AgentContext
    from openhands_cli.stores import agent_store

    def build_agent_context(self):  # noqa: ANN001
        skills = agent_store.load_project_skills(agent_store.get_work_dir())
        system_suffix = "\n".join(
            [
                f"Your current working directory is: {agent_store.get_work_dir()}",
                f"User operating system: {agent_store.get_os_description()}",
            ]
        )
        return AgentContext(
            skills=skills,
            system_message_suffix=system_suffix,
            load_user_skills=False,
            load_public_skills=False,
        )

    agent_store.AgentStore._build_agent_context = build_agent_context


def _enable_local_multimodal_model() -> None:
    """Tell OpenHands that the unknown local OpenAI-compatible model has vision."""
    if os.environ.get("MULTIMODALCODE_FORCE_VISION") != "1":
        return
    from openhands.sdk.llm import LLM

    LLM._supports_vision = lambda self: True


def _pin_local_model_limits() -> None:
    """Supply capabilities that LiteLLM cannot infer from a private model name."""
    from openhands_cli.stores import agent_store

    max_input = int(os.environ.get("MULTIMODALCODE_MAX_INPUT_TOKENS", "32768"))
    max_output = int(os.environ.get("MULTIMODALCODE_MAX_OUTPUT_TOKENS", "8192"))
    original = agent_store.AgentStore._apply_runtime_config

    def apply_runtime_config(self, *args, **kwargs):  # noqa: ANN001
        agent = original(self, *args, **kwargs)
        llm = agent.llm.model_copy(
            update={"max_input_tokens": max_input, "max_output_tokens": max_output}
        )
        updates = {"llm": llm}
        condenser = agent.condenser
        if condenser is not None and getattr(condenser, "llm", None) is not None:
            condenser_llm = condenser.llm.model_copy(
                update={"max_input_tokens": max_input, "max_output_tokens": max_output}
            )
            updates["condenser"] = condenser.model_copy(update={"llm": condenser_llm})
        return agent.model_copy(update=updates)

    agent_store.AgentStore._apply_runtime_config = apply_runtime_config


def main() -> None:
    _disable_external_skill_discovery()
    _enable_local_multimodal_model()
    _pin_local_model_limits()
    from openhands_cli.entrypoint import main as openhands_main

    openhands_main()


if __name__ == "__main__":
    main()
