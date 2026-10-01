from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = (
    PROJECT_ROOT / "scripts/interactweb_bench/validate_official_shard.py"
)
SPEC = importlib.util.spec_from_file_location("validate_official_shard", VALIDATOR_PATH)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)
AGGREGATOR_PATH = (
    PROJECT_ROOT / "scripts/interactweb_bench/aggregate_official_run.py"
)
AGGREGATOR_SPEC = importlib.util.spec_from_file_location(
    "aggregate_official_run", AGGREGATOR_PATH
)
assert AGGREGATOR_SPEC and AGGREGATOR_SPEC.loader
AGGREGATOR = importlib.util.module_from_spec(AGGREGATOR_SPEC)
AGGREGATOR_SPEC.loader.exec_module(AGGREGATOR)
API_CHECK_PATH = PROJECT_ROOT / "scripts/interactweb_bench/check_official_apis.py"
API_SPEC = importlib.util.spec_from_file_location("check_official_apis", API_CHECK_PATH)
assert API_SPEC and API_SPEC.loader
API_CHECK = importlib.util.module_from_spec(API_SPEC)
API_SPEC.loader.exec_module(API_CHECK)


def _history(status: str, tcr: float) -> dict:
    return {
        "trajectory": [
            {"role": "user", "content": "request"},
            {
                "role": "user",
                "content": "terminal",
                "debug_info": {
                    "is_final": True,
                    "stop_reason": "submitted",
                    "evaluation_detail": {
                        "status": status,
                        "tcr": tcr,
                        "sr": 0,
                        "raw_metrics": {
                            "Details": [{"passed": tcr > 0, "reason": "fixture"}]
                        },
                    },
                    "oracle_slots_used_for_grading": [
                        {"final_weight": 1.0, "assertion_type": "POSITIVE"}
                    ],
                },
            },
        ]
    }


class InteractWebOfficialRunTests(unittest.TestCase):
    def test_validator_accepts_terminal_zero_without_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "shard.jsonl"
            data.write_text(
                json.dumps({"id": "001_P-MIN"})
                + "\n"
                + json.dumps({"id": "002_P-RAM"})
                + "\n",
                encoding="utf-8",
            )
            logs = root / "output/model/logs"
            for task_id, status, tcr in (
                ("001_P-MIN", "CRASHED", 0.0),
                ("002_P-RAM", "FAIL", 0.5),
            ):
                path = logs / task_id / "interaction_history.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(_history(status, tcr)), encoding="utf-8")
            index = root / "output/model/workspaces/002_P-RAM/index.html"
            index.parent.mkdir(parents=True)
            index.write_text("ok", encoding="utf-8")

            report = VALIDATOR.validate(root / "output", "model", data)
            self.assertTrue(report["ok"])
            self.assertEqual(report["terminal_evaluations"], 2)
            self.assertEqual(report["statuses"], {"CRASHED": 1, "FAIL": 1})
            self.assertEqual(report["workspaces_with_index_html"], 1)

    def test_validator_rejects_deferred_or_missing_terminal_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "shard.jsonl"
            data.write_text(json.dumps({"id": "001_P-MIN"}) + "\n", encoding="utf-8")
            log_dir = root / "output/model/logs/001_P-MIN"
            log_dir.mkdir(parents=True)
            (log_dir / "interaction_history.json").write_text(
                json.dumps(
                    {
                        "trajectory": [
                            {
                                "role": "user",
                                "content": "deferred",
                                "debug_info": {
                                    "is_final": True,
                                    "evaluation_detail": {
                                        "status": "DEFERRED",
                                        "tcr": None,
                                    },
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (log_dir / "pending_evaluation.json").write_text("{}", encoding="utf-8")
            report = VALIDATOR.validate(root / "output", "model", data)
            self.assertFalse(report["ok"])
            self.assertEqual(report["missing_or_invalid_terminal_ids"], ["001_P-MIN"])
            self.assertEqual(report["deferred_pending_ids"], ["001_P-MIN"])

    def test_runner_uses_frozen_pipeline_and_released_roles(self) -> None:
        runner = (
            PROJECT_ROOT
            / "scripts/native_benchmarks/run_interactweb_official_full_shard_qwen35_job.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("upstream/src/experiment/run_simulation.py", runner)
        self.assertNotIn("run_trajectory_only.py", runner)
        self.assertIn("--builder_model Qwen3.5-9B", runner)
        self.assertIn("--visual_copilot_model Qwen3.5-9B", runner)
        self.assertIn("--user_model deepseek-v3.2", runner)
        self.assertIn("--webvoyager_model gpt-5-mini", runner)
        self.assertIn("--max-model-len 128000", runner)

    def test_credentials_parser_is_allowlisted_and_requires_private_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "credentials.env"
            path.write_text(
                "USER_MODEL_BASE_URL=https://user.example/v1\n"
                "USER_MODEL_API_KEY=user-secret\n"
                "WEBVOYAGER_BASE_URL=https://judge.example/v1\n"
                "WEBVOYAGER_API_KEY=judge-secret\n",
                encoding="utf-8",
            )
            os.chmod(path, 0o600)
            values = API_CHECK.load_credentials(path)
            self.assertEqual(set(values), API_CHECK.ALLOWED_KEYS)
            os.chmod(path, 0o644)
            with self.assertRaisesRegex(RuntimeError, "chmod 600"):
                API_CHECK.load_credentials(path)

    def test_credentials_parser_does_not_accept_shell_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "credentials.env"
            path.write_text("UNEXPECTED=$(touch /tmp/never-run)\n", encoding="utf-8")
            os.chmod(path, 0o600)
            with self.assertRaisesRegex(RuntimeError, "unsupported credential"):
                API_CHECK.load_credentials(path)

    def test_aggregator_invokes_released_analyzer_on_exact_404_denominator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "all.jsonl"
            dataset_rows = []
            for index in range(404):
                role = ("P-MIN", "P-RAM", "P-INT", "P-CON")[index % 4]
                task_id = f"{index:06d}_{role}"
                dataset_rows.append(
                    {"id": task_id, "difficulty": "easy", "persona": role}
                )
                history_path = (
                    root
                    / "run/shards"
                    / f"shard-{index % 8:02d}"
                    / "interactweb/model/logs"
                    / task_id
                    / "interaction_history.json"
                )
                history_path.parent.mkdir(parents=True)
                history = _history("PASS", 1.0)
                history["trajectory"].insert(
                    1,
                    {
                        "role": "assistant",
                        "content": '<boltAction type="finish">done</boltAction>',
                        "turn": 1,
                    },
                )
                history_path.write_text(json.dumps(history), encoding="utf-8")
            data.write_text(
                "".join(json.dumps(row) + "\n" for row in dataset_rows),
                encoding="utf-8",
            )

            manifest = AGGREGATOR.aggregate(
                root / "run", "model", data, root / "analysis"
            )
            summary = manifest["score_summary_derived_from_released_csv"]
            self.assertEqual(summary["cases"], 404)
            self.assertEqual(summary["global_tcr"], 1.0)
            self.assertEqual(summary["global_clean_tcr"], 1.0)
            self.assertEqual(summary["final_states"], {"PASS": 404})
            self.assertTrue(
                (root / "analysis/official_result_analyze.stdout.txt").is_file()
            )
            self.assertTrue((root / "analysis/logs_summary_with_roles.csv").is_file())


if __name__ == "__main__":
    unittest.main()
