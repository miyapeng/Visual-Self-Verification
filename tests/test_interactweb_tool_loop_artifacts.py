from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class InteractWebToolLoopArtifactTests(unittest.TestCase):
    def test_frozen_sources_match_valid_run_hashes(self) -> None:
        expected = {
            PROJECT_ROOT / "scripts/research_goal/interactweb_tool_loop.py":
                "a7bc7b8e1358f81ad7db4d04d05acc9bac65679b6e81b1772d4d62931781eaca",
            PROJECT_ROOT / "configs/research/interactweb_tool_loop_preregistration.md":
                "426aecb5d01c0fd560bfe70d2e20022ef0db24a90ca6fd872f6da9f6fcc245d9",
            PROJECT_ROOT / "configs/research/interactweb_tool_loop_contexts/cases/interactweb-000045-navigation-repair/contexts/iteration-00.json":
                "240439de0154cfb9ba094942595ae234336a7b15eac26a43e7d1e663940a3905",
            PROJECT_ROOT / "configs/research/interactweb_tool_loop_contexts/cases/interactweb-000062-question-bank-repair/contexts/iteration-00.json":
                "eb6fe3bfbb33a7900226aced7c7730cbb3284b480c1837848cde2f73d8cd9c93",
        }
        self.assertEqual(
            {str(path.relative_to(PROJECT_ROOT)): _sha256(path) for path in expected},
            {str(path.relative_to(PROJECT_ROOT)): digest for path, digest in expected.items()},
        )

    def test_frozen_contexts_are_public_single_failure_certificates(self) -> None:
        context_root = (
            PROJECT_ROOT
            / "configs/research/interactweb_tool_loop_contexts/cases"
        )
        case_ids = {
            "interactweb-000045-navigation-repair",
            "interactweb-000062-question-bank-repair",
        }
        for case_id in case_ids:
            context = json.loads(
                (
                    context_root / case_id / "contexts/iteration-00.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(context["policy"], "guarded_frontier")
            self.assertEqual(len(context["records"]), 1)
            record = context["records"][0]
            self.assertEqual(record["kind"], "verification_residual")
            self.assertEqual(record["status"], "fail")
            self.assertTrue(Path(record["image_path"]).is_file())

    def test_valid_compact_results_are_complete_and_checker_safe(self) -> None:
        result_root = (
            PROJECT_ROOT
            / "runs/research/active_visual_verification/interactweb-tool-loop-qwen35-001"
        )
        rows = [
            json.loads((result_root / name).read_text(encoding="utf-8"))
            for name in ("valid_case45.summary.json", "valid_case62.summary.json")
        ]
        self.assertEqual({row["schema"] for row in rows}, {"iwtool1"})
        self.assertTrue(all(row["check"] for row in rows))
        self.assertTrue(all(row["api"] == 8 and row["bash"] == 8 for row in rows))
        self.assertTrue(all(not row["r"] and not row["imp"] for row in rows))


if __name__ == "__main__":
    unittest.main()
