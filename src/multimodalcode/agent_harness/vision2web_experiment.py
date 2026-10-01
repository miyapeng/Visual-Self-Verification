"""Frozen experimental conditions for Vision2Web self-verification."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


VISION2WEB_MODES = ("official", "browser_enabled", "guided_vsv")
LEGACY_VISION2WEB_MODE_ALIASES = {
    "tools": "browser_enabled",
    "self_verify": "guided_vsv",
}
ACCEPTED_VISION2WEB_MODES = (
    *VISION2WEB_MODES,
    *LEGACY_VISION2WEB_MODE_ALIASES,
)
BROWSER_INTERFACE_REVISION = "openhands-native-browser-toolset-v1"
CLAUDE_BROWSER_INTERFACE_REVISION = (
    "official-playwright-cli-claude-skill-v2-context-audit"
)

GUIDED_VSV_INSTRUCTION = """Before final submission, perform a concrete visual self-verification of the application you implemented. Use only the public task requirements, PRD, prototype images, resources, current source code, and observations produced by running your own application.

Follow this procedure after you have obtained the first runnable implementation:

1. Establish the deployed version. Inspect the existing /workspace/start.sh and use it to launch the application at http://localhost:3000. Run it in the background when continued terminal work is needed, wait until the URL responds, and inspect its launch log if startup fails. Do not replace a valid start.sh with an unrelated server command merely for verification.
2. Observe the real application. Open http://localhost:3000 with the browser interface available in the current coding scaffold and inspect the rendered initial state. Make sure actual screenshot pixels enter your context; a screenshot path alone, curl response, raw HTML, HTTP 200, or viewing the provided prototype does not count as observing the implementation.
3. Verify one user-observable requirement at a time. For each check, state the functional objective and expected observable outcome, navigate to the required starting state, and then interact step by step with the available native browser tools. Use browser_get_state before selecting an indexed element because element indices can change after every interaction. Do not create one whole-site mega-plan.
4. Observe the result after each relevant interaction. Request a screenshot with browser_get_state(include_screenshot=true) whenever visual evidence is needed, and inspect the returned URL, page state, accessibility information, and action errors. The browser only executes actions and returns observations; you must decide whether the expected outcome holds.
5. Use evidence before editing. If the observed application conflicts with the requirement or prototype, inspect the responsible code and make the smallest coherent correction. Do not change code solely because you expected a failure, and do not weaken the requirement or expected outcome after seeing the result.
6. Recheck meaningful changes. Ensure the updated code is actually deployed, replay the failed check, and confirm that the observed failure is removed. Also recheck a small, relevant sample of previously successful behavior that the change could affect so that a visual repair does not introduce a functional or visual regression.
7. Continue or stop deliberately. You may perform additional function-scoped checks when they are useful. You decide which requirements to inspect, how many checks to run, whether more editing is necessary, and when the implementation is ready to submit."""

# Backward-compatible import for archived scripts and reports. New experiments
# must use ``GUIDED_VSV_INSTRUCTION`` and the canonical ``guided_vsv`` mode.
SELF_VERIFY_INSTRUCTION = GUIDED_VSV_INSTRUCTION


@dataclass(frozen=True)
class Vision2WebCondition:
    name: str
    browser_interfaces: bool
    instruction_suffix: str


CONDITIONS = {
    "official": Vision2WebCondition("official", False, ""),
    "browser_enabled": Vision2WebCondition("browser_enabled", True, ""),
    "guided_vsv": Vision2WebCondition(
        "guided_vsv", True, GUIDED_VSV_INSTRUCTION
    ),
}


def normalize_vision2web_mode(mode: str, *, scaffold: str | None = None) -> str:
    """Return the canonical experimental condition name.

    ``tools`` and ``self_verify`` are accepted only so archived launch scripts
    remain interpretable. Claude Code's historical ``tools`` condition was an
    exact duplicate of its official condition, whereas OpenHands ``tools``
    enabled the added browser affordance.
    """
    if mode == "tools" and scaffold == "claude_code":
        return "official"
    canonical = LEGACY_VISION2WEB_MODE_ALIASES.get(mode, mode)
    if canonical not in VISION2WEB_MODES:
        raise ValueError(f"Unknown Vision2Web mode: {mode}")
    if scaffold == "claude_code" and canonical == "browser_enabled":
        raise ValueError(
            "Claude Code official already exposes playwright-cli; "
            "browser_enabled would duplicate the official condition"
        )
    return canonical


def prompt_for_mode(official_prompt: str, mode: str) -> str:
    """Return the exact task prompt for one condition.

    Tool availability is represented by the scaffold, not by extra task text.
    Consequently ``official`` and ``browser_enabled`` have byte-identical user
    prompts; ``guided_vsv`` appends the frozen operational procedure.
    """
    canonical = normalize_vision2web_mode(mode)
    condition = CONDITIONS[canonical]
    if not condition.instruction_suffix:
        return official_prompt
    return f"{official_prompt.rstrip()}\n\n{condition.instruction_suffix}"


def prompt_record(
    official_prompt: str,
    mode: str,
    *,
    scaffold: str | None = None,
) -> dict[str, object]:
    canonical = normalize_vision2web_mode(mode, scaffold=scaffold)
    prompt = prompt_for_mode(official_prompt, mode)
    return {
        "mode": canonical,
        "requested_mode": mode,
        "legacy_mode_alias": mode if mode != canonical else None,
        "prompt": prompt,
        "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "official_prompt_sha256": hashlib.sha256(
            official_prompt.encode("utf-8")
        ).hexdigest(),
        "browser_interfaces": CONDITIONS[canonical].browser_interfaces,
        "browser_interface_revision": (
            BROWSER_INTERFACE_REVISION
            if CONDITIONS[canonical].browser_interfaces
            else None
        ),
        "guided_vsv_instruction": CONDITIONS[canonical].instruction_suffix or None,
    }
