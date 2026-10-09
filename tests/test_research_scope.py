from __future__ import annotations

import json
import subprocess
import unittest
import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ResearchScopeTests(unittest.TestCase):
    def test_active_scope_is_exact_and_interactweb_is_archived(self) -> None:
        scope = json.loads(
            (PROJECT_ROOT / "configs/research_scope.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [row["id"] for row in scope["active_benchmarks"]],
            ["vision2web", "frontalk", "swe_bench_multimodal"],
        )
        self.assertEqual(scope["active_benchmarks"][0]["scope"], "level_1_level_2_and_level_3")
        self.assertEqual(
            scope["implementation_priority"],
            ["vision2web", "frontalk", "swe_bench_multimodal"],
        )
        archived = {row["id"]: row for row in scope["archived_benchmarks"]}
        self.assertEqual(
            archived["interactweb_bench"]["status"], "archived_out_of_scope"
        )
        self.assertFalse(archived["interactweb_bench"]["include_in_active_aggregates"])
        self.assertFalse(archived["interactweb_bench"]["resume_automatically"])
        self.assertTrue(
            archived["interactweb_bench"][
                "reintroduction_requires_explicit_user_reversal"
            ]
        )

    def test_interactweb_is_absent_from_active_native_registry(self) -> None:
        registry = json.loads(
            (PROJECT_ROOT / "configs/native_benchmarks.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("interactweb_bench", registry)
        self.assertIn("frontalk", registry)

    def test_historical_launcher_stops_before_submission(self) -> None:
        launcher = (
            PROJECT_ROOT
            / "scripts/native_benchmarks/submit_interactweb_full_qwen35.sh"
        )
        result = subprocess.run(
            ["bash", str(launcher)],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("archived and outside the current paper scope", result.stderr)

    def test_official_snapshot_and_historical_outputs_are_preserved(self) -> None:
        self.assertTrue(
            (PROJECT_ROOT / "evaluate/interactweb_bench/run_official.py").is_file()
        )
        self.assertTrue((PROJECT_ROOT / "data/interactweb_bench/manifest.json").is_file())
        self.assertTrue(
            (
                PROJECT_ROOT
                / "runs/native_benchmarks/qwen35-9b-interactweb-full-20260820"
            ).is_dir()
        )

    def test_self_verify_runner_rejects_archived_manifest(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "self_verify_run_scope_test", PROJECT_ROOT / "src/multimodalcode/research/self_verify_cli.py"
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        manifest = (
            PROJECT_ROOT / "runs/research/self_verify_public_cases-004/cases.jsonl"
        )
        with self.assertRaisesRegex(ValueError, "archived and outside"):
            module.load_cases(manifest, [], None)

    def test_legacy_forced_vision2web_loop_has_an_explicit_archive_guard(self) -> None:
        source = (PROJECT_ROOT / "src/multimodalcode/research/self_verify_cli.py").read_text(encoding="utf-8")
        self.assertIn("--allow-archived-forced-vision2web-loop", source)
        self.assertIn("legacy externally orchestrated Vision2Web", source)
        self.assertIn("Use scripts/agents/run.py with --vision2web-mode", source)


if __name__ == "__main__":
    unittest.main()
