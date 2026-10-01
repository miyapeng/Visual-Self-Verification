from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from multimodalcode.artifacts import (
    extract_html,
    parse_flame_output,
    save_project_markdown,
)
from multimodalcode.backends import MockBackend
from multimodalcode.cli import build_parser
from multimodalcode.config import ProjectConfig
from multimodalcode.datasets import load_benchmark
from multimodalcode.evaluation import _latest_rows
from multimodalcode.generation import generate_run
from multimodalcode.judge_profiles import (
    parse_ui2code_score,
    parse_web2code_scores,
)
from multimodalcode.official_design2code import (
    _append_checkpoint,
    _read_checkpoint,
)


class ArtifactTests(unittest.TestCase):
    def test_extract_html_prefers_html_fence(self) -> None:
        text = "note\n```css\nbody{}\n```\n```html\n<html>ok</html>\n```"
        self.assertEqual(extract_html(text), "<html>ok</html>")

    def test_project_writer_rejects_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            written = save_project_markdown(
                "# index.html\n```html\nok\n```\n"
                "# ../escape.txt\n```txt\nbad\n```\n",
                temporary,
            )
            self.assertEqual(len(written), 1)
            self.assertEqual((Path(temporary) / "index.html").read_text(), "ok")
            self.assertFalse((Path(temporary).parent / "escape.txt").exists())

    def test_flame_split(self) -> None:
        css, language, code = parse_flame_output(
            "// CSS\nbody { color: red; }\n"
            "// JavaScript XML (JSX)\nexport default function App(){return <div/>}"
        )
        self.assertIn("color", css)
        self.assertEqual(language, "jsx")
        self.assertIn("function App", code)


class JudgeParserTests(unittest.TestCase):
    def test_ui2code_boxed(self) -> None:
        self.assertEqual(parse_ui2code_score("Result: \\boxed{87.5}")["score"], 87.5)

    def test_web2code(self) -> None:
        parsed = parse_web2code_scores("1,2,3,4,5,6,7,8,9,10")
        self.assertEqual(parsed["criteria"], [1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        self.assertAlmostEqual(parsed["visual_structure_and_alignment"], 2.5)

    def test_resume_summary_uses_latest_sample_record(self) -> None:
        rows = [
            {"key": "item::0", "profile": "judge", "status": "error"},
            {"key": "item::0", "profile": "judge", "status": "ok", "score": 1},
            {"key": "item::0", "profile": "other", "status": "ok", "score": 2},
        ]
        latest = _latest_rows(rows)
        self.assertEqual(len(latest), 2)
        self.assertEqual(latest[0]["status"], "ok")

    def test_design2code_checkpoint_uses_latest_valid_line(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint = Path(temporary) / "scores.partial.jsonl"
            _append_checkpoint(
                checkpoint, {"key": "item::0", "status": "error"}
            )
            _append_checkpoint(
                checkpoint, {"key": "item::0", "status": "ok", "score": 0.5}
            )
            with checkpoint.open("a", encoding="utf-8") as handle:
                handle.write('{"key": "interrupted"')
            latest = _read_checkpoint(checkpoint)
            self.assertEqual(latest["item::0"]["status"], "ok")
            self.assertEqual(latest["item::0"]["score"], 0.5)


class EndToEndCoreTests(unittest.TestCase):
    def test_generic_adapter_and_mock_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (root / "data.jsonl").write_text(
                json.dumps(
                    {
                        "id": "sample",
                        "image_path": "image.png",
                        "prompt": "Make a page",
                        "reference": "<html>reference</html>",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            config_path = root / "benchmarks.json"
            config_path.write_text(
                json.dumps(
                    {
                        "benchmarks": {
                            "tiny": {
                                "adapter": "ui2code_jsonl",
                                "data_file": "data.jsonl",
                                "image_root": ".",
                                "output_format": "html",
                                "evaluator": {"type": "ui2code_vlm"},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            project = ProjectConfig(config_path)
            benchmark = project.benchmark("tiny")
            items = load_benchmark(benchmark)
            self.assertEqual(len(items), 1)
            run_dir = root / "run"
            summary = generate_run(
                benchmark,
                MockBackend(),
                run_dir,
                model_name="mock",
                backend_name="mock",
                workers=2,
            )
            self.assertEqual(summary["ok"], 1)
            self.assertTrue((run_dir / "html" / "sample__s0.html").exists())
            resumed = generate_run(
                benchmark,
                MockBackend(),
                run_dir,
                model_name="mock",
                backend_name="mock",
            )
            self.assertEqual(resumed["skipped"], 1)


class CliTests(unittest.TestCase):
    def test_run_accepts_normal_vllm_workflow(self) -> None:
        args = build_parser().parse_args(
            [
                "run",
                "ui2code-real",
                "--output-root",
                "runs/model",
                "--backend",
                "vllm",
                "--model",
                "model",
                "--base-url",
                "http://127.0.0.1:8001/v1",
            ]
        )
        self.assertEqual(args.subcommand, "run")
        self.assertEqual(args.stages, "generate,render,evaluate")
        self.assertIsNone(args.judge_backend)

    def test_render_stage_needs_no_dummy_backend(self) -> None:
        args = build_parser().parse_args(
            [
                "run",
                "ui2code-real",
                "--output-root",
                "runs/model",
                "--stages",
                "render",
            ]
        )
        self.assertIsNone(args.backend)
        self.assertIsNone(args.model)


class BundledBenchmarkTests(unittest.TestCase):
    def test_default_registry_uses_only_bundled_assets(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        project = ProjectConfig()
        expected_counts = {
            "design2code": 484,
            "flame-react-eval": 80,
            "web2code": 1198,
            "ui2code-real": 115,
        }
        self.assertEqual(set(project.names()), set(expected_counts))
        for name, expected_count in expected_counts.items():
            config = project.benchmark(name)
            data_file = config.path("data_file")
            self.assertIsNotNone(data_file)
            self.assertTrue(data_file.is_relative_to(project_root))
            items = load_benchmark(config)
            self.assertEqual(len(items), expected_count)
            self.assertTrue(
                all(
                    Path(image).is_relative_to(project_root) and Path(image).exists()
                    for item in items
                    for image in item.image_paths
                )
            )
        design2code_root = project.benchmark("design2code").evaluator[
            "upstream_root"
        ]
        self.assertFalse(Path(design2code_root).is_absolute())

    def test_bundled_design2code_metric_is_complete_and_compiles(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        metrics = (
            project_root
            / "evaluate"
            / "design2code"
            / "Design2Code"
            / "metrics"
        )
        visual_score = metrics / "visual_score.py"
        self.assertTrue((metrics / "screenshot_single.py").is_file())
        compile(
            visual_score.read_text(encoding="utf-8"),
            str(visual_score),
            "exec",
        )


if __name__ == "__main__":
    unittest.main()
