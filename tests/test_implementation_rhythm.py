from __future__ import annotations

import unittest

from multimodalcode.research.implementation_rhythm import audit_case


def action(tool: str, categories: list[str], digest: str) -> dict:
    return {
        "tool": tool,
        "categories": categories,
        "content": {"sha256": digest, "chars": 1, "snippet": digest},
        "paths": [],
    }


def event(
    sequence: int,
    *,
    actions: list[dict] | None = None,
    edit_units: int = 0,
    observation_status: str | None = None,
    verification_categories: list[str] | None = None,
    public: bool = False,
) -> dict:
    return {
        "sequence": sequence,
        "source_ref": f"fixture#{sequence}",
        "program_version": {"id": f"v{sequence}"},
        "public_request": {"sha256": "public"} if public else None,
        "edit": ({"units": edit_units, "program_units": edit_units} if edit_units else None),
        "executed_action": actions,
        "observation": (
            {
                "status": observation_status,
                "verification_categories": verification_categories or [],
            }
            if observation_status is not None
            else None
        ),
    }


def case(benchmark: str = "swe_mm", *, evaluator: bool = False) -> dict:
    return {
        "benchmark": benchmark,
        "task": "fixture",
        "source_trajectory": "fixture.json",
        "generation_status": "generated",
        "trajectory_available": True,
        "submit_actions": 1,
        "external_outcome": {
            "availability": "available" if evaluator else "unavailable",
            "status": "resolved" if evaluator else None,
        },
    }


class ImplementationRhythmTests(unittest.TestCase):
    def test_no_active_verification_can_be_evaluator_only(self) -> None:
        rows = [
            event(0, public=True),
            event(1, actions=[action("shell", ["program_edit"], "edit")], edit_units=1),
        ]
        result = audit_case(rows, case(evaluator=True))
        self.assertEqual(result["rhythm"], "no_active_verification")
        self.assertTrue(result["flags"]["post_submission_evaluator_only"])
        self.assertIsNone(result["edit_fraction_before_first_active_verification"])

    def test_final_version_only_verification(self) -> None:
        rows = [
            event(0, actions=[action("shell", ["program_edit"], "e1")], edit_units=1),
            event(1, actions=[action("shell", ["program_edit"], "e2")], edit_units=1),
            event(2, actions=[action("shell", ["executable_check"], "test")]),
            event(3, observation_status="check_pass", verification_categories=["executable_check"]),
        ]
        result = audit_case(rows, case())
        self.assertEqual(result["rhythm"], "final_version_only_verification")
        self.assertEqual(result["implementation_block_count"], 1)
        self.assertEqual(result["edit_fraction_before_first_active_verification"], 1.0)

    def test_failure_edit_same_check_pass_is_confirmed_repair(self) -> None:
        rows = [
            event(0, actions=[action("shell", ["program_edit"], "e1")], edit_units=1),
            event(1, actions=[action("shell", ["executable_check"], "test")]),
            event(2, observation_status="concrete_failure", verification_categories=["executable_check"]),
            event(3, actions=[action("shell", ["program_edit"], "e2")], edit_units=1),
            event(4, actions=[action("shell", ["executable_check"], "test")]),
            event(5, observation_status="check_pass", verification_categories=["executable_check"]),
        ]
        result = audit_case(rows, case())
        self.assertEqual(result["verify_edit_reverify_cycle_count"], 1)
        self.assertEqual(
            result["failure_repair_outcomes"], {"confirmed_same_check_repair": 1}
        )
        self.assertEqual(result["edit_fraction_before_first_active_verification"], 0.5)

    def test_route_only_visual_replay_remains_unknown(self) -> None:
        verify = action("screenshot_validated", ["executable_check", "generated_visual_check"], "slash")
        rows = [
            event(0, actions=[action("file", ["program_edit"], "e1")], edit_units=1),
            event(1, actions=[verify]),
            event(2, observation_status="concrete_failure", verification_categories=["executable_check", "generated_visual_check"]),
            event(3, actions=[action("file", ["program_edit"], "e2")], edit_units=1),
            event(4, actions=[verify]),
            event(5, observation_status="check_pass", verification_categories=["executable_check", "generated_visual_check"]),
        ]
        result = audit_case(rows, case("interactweb"))
        self.assertEqual(
            result["failure_repair_outcomes"], {"unknown_weak_check_identity": 1}
        )
        self.assertEqual(result["implementation_block_count"], 2)

    def test_reasoning_mentions_do_not_become_active_checks(self) -> None:
        rows = [
            event(0, actions=[action("think", ["executable_check", "generated_visual_check"], "prose")]),
            event(1, actions=[action("terminal", ["program_edit"], "edit")], edit_units=1),
        ]
        result = audit_case(rows, case("vision2web"))
        self.assertEqual(result["active_verification_count"], 0)
        self.assertEqual(result["rhythm"], "no_active_verification")


if __name__ == "__main__":
    unittest.main()
