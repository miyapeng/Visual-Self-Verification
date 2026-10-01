from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts/interactweb_bench/collect_full_run.py"
SPEC = importlib.util.spec_from_file_location("collect_full_run", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class InteractWebFullRunCollectionTests(unittest.TestCase):
    def test_distinguishes_terminal_model_failure_from_worker_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_dir = root / "data"
            data_dir.mkdir()
            rows = [
                {"id": "001_P-MIN", "difficulty": "easy", "persona": "P-MIN"},
                {"id": "002_P-RAM", "difficulty": "middle", "persona": "P-RAM"},
            ]
            (data_dir / "shard-00-of-01.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            model_root = root / "run/shards/shard-00/interactweb/model"
            log_one = model_root / "logs/001_P-MIN"
            _write_json(log_one / "interaction_history.json", {"trajectory": []})
            _write_json(
                log_one / "pending_evaluation.json",
                {"status": "pending", "stop_reason": "max_turns_reached"},
            )
            log_two = model_root / "logs/002_P-RAM"
            _write_json(log_two / "history.json", {"messages": []})
            shard_log = root / "run/shards/shard-00/job.log"
            shard_log.parent.mkdir(parents=True, exist_ok=True)
            shard_log.write_text(
                "Uncaught exception during task execution: [Errno 21] Is a directory: "
                "'/tmp/002_P-RAM/'\n",
                encoding="utf-8",
            )

            report = MODULE.collect(root / "run", data_dir, "model")
            self.assertEqual(report["summary"]["terminal_trajectories"], 1)
            self.assertEqual(report["summary"]["pending_official_judge"], 1)
            self.assertEqual(
                report["summary"]["generation_statuses"],
                {
                    "generation_error_without_artifact": 1,
                    "terminal_without_artifact": 1,
                },
            )
            self.assertEqual(
                report["cases"][1]["official_judge_status"],
                "not_applicable_generation_error",
            )

    def test_real_full_run_has_frozen_404_case_denominator(self) -> None:
        run_root = (
            PROJECT_ROOT
            / "runs/native_benchmarks/qwen35-9b-interactweb-full-20260820"
        )
        if not run_root.is_dir():
            self.skipTest("full InteractWeb run is not present")
        report = MODULE.collect(
            run_root,
            PROJECT_ROOT / "data/interactweb_bench/shards-full-08",
            "Qwen3.5-9B",
            PROJECT_ROOT / "evaluate/interactweb_bench/upstream/config.yaml",
            PROJECT_ROOT
            / "scripts/native_benchmarks/run_interactweb_full_shard_qwen35_job.sh",
            PROJECT_ROOT
            / "evaluate/interactweb_bench/upstream/scripts/deploy_qwen_3_5_9B.sh",
        )
        self.assertEqual(report["summary"]["expected_cases"], 404)
        self.assertEqual(report["summary"]["terminal_trajectories"], 402)
        self.assertEqual(report["summary"]["pending_official_judge"], 402)
        self.assertEqual(report["summary"]["workspaces_with_index_html"], 400)
        self.assertEqual(report["summary"]["trajectory_and_index_html"], 399)
        self.assertEqual(report["summary"]["unclassified_or_incomplete_ids"], [])
        self.assertFalse(report["summary"]["matches_released_reference_models"])
        self.assertEqual(
            report["summary"]["model_role_mismatches"],
            {
                "user_model": {
                    "released": "deepseek-v3.2",
                    "observed": ["Qwen3.5-9B"],
                },
                "webvoyager_model": {
                    "released": "gpt-5-mini",
                    "observed": ["Qwen3.5-9B"],
                },
            },
        )
        self.assertEqual(report["summary"]["cases_invoking_user_simulator"], 38)
        self.assertEqual(report["summary"]["clarification_actions"], 59)
        self.assertEqual(
            report["summary"]["runtime_configuration_comparison"]["max_model_len"],
            {"observed": 262144, "released": 128000, "matches": False},
        )


if __name__ == "__main__":
    unittest.main()
