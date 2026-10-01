from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "research_goal"
    / "audit_controlled_comparison.py"
)
SPEC = importlib.util.spec_from_file_location("audit_controlled_comparison", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ControlledComparisonAuditTests(unittest.TestCase):
    def test_execution_is_retained_when_semantic_condition_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "results"
            per_case = root / "per_case"
            case_root = root / "cases" / "example"
            per_case.mkdir(parents=True)
            (case_root / "semantic_policy").mkdir(parents=True)
            (case_root / "semantic_policy" / "model_calls.jsonl").write_text(
                json.dumps({"input_tokens_estimated": 123}) + "\n",
                encoding="utf-8",
            )
            conditions = {
                "baseline": {
                    "status": "error",
                    "version": "v1",
                    "plan_sha256": "plan",
                    "error": "judgment contract failed",
                },
                "generic": {
                    "status": "error",
                    "version": "v1",
                    "plan_sha256": "plan",
                    "error": "revision contract failed",
                },
                "full": {
                    "status": "complete",
                    "version": "v1",
                    "plan_sha256": "plan",
                    "pass_count": 1,
                    "fail_count": 1,
                },
            }
            result = {
                "case_id": "example",
                "benchmark": "fixture",
                "status": "partial",
                "same_base_policy": True,
                "official_evaluator_visible": False,
                "initial_version": "v1",
                "conditions": conditions,
                "comparisons": {
                    "generic_vs_baseline": {"available": False},
                    "full_vs_baseline": {"available": False},
                },
            }
            result_path = per_case / "example.json"
            result_path.write_text(json.dumps(result), encoding="utf-8")
            for condition in ("baseline", "full"):
                execution = (
                    case_root
                    / "browser"
                    / "executions"
                    / condition
                    / "execution"
                    / "result.json"
                )
                execution.parent.mkdir(parents=True, exist_ok=True)
                execution.write_text(
                    json.dumps(
                        {
                            "purpose": f"controlled-comparison-{condition}",
                            "execution_id": condition,
                            "status": "complete",
                            "code_version": "v1",
                            "actions": [{"status": "pass"}, {"status": "fail"}],
                        }
                    ),
                    encoding="utf-8",
                )

            audited = MODULE._audit_case(result_path)
            self.assertTrue(audited["structurally_valid"])
            self.assertEqual(
                audited["conditions"]["baseline"]["execution_count"], 1
            )
            self.assertEqual(
                audited["conditions"]["baseline"]["executions"][0]["action_count"],
                2,
            )
            self.assertEqual(audited["conditions"]["generic"]["execution_count"], 0)
            self.assertEqual(audited["max_single_text_input_tokens_estimated"], 123)


if __name__ == "__main__":
    unittest.main()
