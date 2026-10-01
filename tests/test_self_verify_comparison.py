from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from multimodalcode.research.interactive import hash_program


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "research_goal"
    / "compare_self_verify_conditions.py"
)
SPEC = importlib.util.spec_from_file_location("compare_self_verify_conditions", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ControlledComparisonTests(unittest.TestCase):
    def test_paired_acceptance_reports_missing_condition_without_fabricating_score(self) -> None:
        comparison = MODULE._paired_acceptance(
            {"baseline": object()}, baseline="baseline", candidate="generic"
        )
        self.assertFalse(comparison["available"])
        self.assertIsNone(comparison["accepted"])
        self.assertEqual(comparison["missing_conditions"], ["generic"])

    def test_comparison_keeps_bounded_error_when_plan_and_checkpoint_are_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            obligations = Path(temporary) / "obligations.json"
            obligations.write_text("{}", encoding="utf-8")
            result = {
                "status": "error",
                "plans": [{"checks": []}],
                "initial_version": "initial",
                "accepted_version": "accepted",
                "final_program_version": "accepted",
            }
            self.assertEqual(
                MODULE._comparison_eligibility(
                    result, obligations_path=obligations
                ),
                (True, "eligible"),
            )

    def test_comparison_rejects_planless_or_unrestored_source_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            obligations = Path(temporary) / "obligations.json"
            obligations.write_text("{}", encoding="utf-8")
            base = {
                "initial_version": "initial",
                "accepted_version": "accepted",
                "final_program_version": "accepted",
            }
            self.assertEqual(
                MODULE._comparison_eligibility(base, obligations_path=obligations)[1],
                "requires_exactly_one_frozen_plan",
            )
            unrestored = {**base, "plans": [{}], "final_program_version": "candidate"}
            self.assertEqual(
                MODULE._comparison_eligibility(
                    unrestored, obligations_path=obligations
                )[1],
                "final_workspace_not_bound_to_accepted_checkpoint",
            )

    def test_rebuild_full_applies_only_accepted_patches_and_binds_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            source_file = source / "index.html"
            source_file.write_text("<p>initial</p>", encoding="utf-8")
            initial = hash_program(source)

            expected = root / "expected"
            expected.mkdir()
            (expected / "index.html").write_text("<p>accepted</p>", encoding="utf-8")
            accepted = hash_program(expected)
            result = {
                "initial_version": initial,
                "accepted_version": accepted,
                "patches": [
                    {
                        "candidate_version": "ignored-rejected-hash",
                        "revision_contract": {
                            "files": {},
                            "edits": [
                                {
                                    "path": "index.html",
                                    "old": "<p>initial</p>",
                                    "new": "<p>rejected</p>",
                                }
                            ],
                        },
                        "acceptance": {"accepted": False},
                    },
                    {
                        "candidate_version": accepted,
                        "revision_contract": {
                            "files": {},
                            "edits": [
                                {
                                    "path": "index.html",
                                    "old": "<p>initial</p>",
                                    "new": "<p>accepted</p>",
                                }
                            ],
                        },
                        "acceptance": {"accepted": True},
                    },
                ],
            }
            tool = MODULE._rebuild_full(
                source=source,
                destination=root / "rebuilt",
                checkpoint_root=root / "checkpoints",
                result=result,
            )
            self.assertEqual(hash_program(tool.workspace), accepted)
            self.assertEqual(
                (tool.workspace / "index.html").read_text(encoding="utf-8"),
                "<p>accepted</p>",
            )


if __name__ == "__main__":
    unittest.main()
