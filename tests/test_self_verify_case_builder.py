from __future__ import annotations

import contextlib
import importlib.util
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from multimodalcode.research.tools import SafeFileToolExecutor


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "research_goal"
    / "build_self_verify_cases.py"
)
SPEC = importlib.util.spec_from_file_location("build_self_verify_cases", SCRIPT)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)

MATERIALIZE_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "research_goal"
    / "materialize_self_verify_cases.py"
)
MATERIALIZE_SPEC = importlib.util.spec_from_file_location(
    "materialize_self_verify_cases", MATERIALIZE_SCRIPT
)
assert MATERIALIZE_SPEC and MATERIALIZE_SPEC.loader
materializer = importlib.util.module_from_spec(MATERIALIZE_SPEC)
MATERIALIZE_SPEC.loader.exec_module(materializer)

RECONSTRUCT_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "research_goal"
    / "reconstruct_interact_first_runnable.py"
)
RECONSTRUCT_SPEC = importlib.util.spec_from_file_location(
    "reconstruct_interact_first_runnable", RECONSTRUCT_SCRIPT
)
assert RECONSTRUCT_SPEC and RECONSTRUCT_SPEC.loader
reconstructor = importlib.util.module_from_spec(RECONSTRUCT_SPEC)
RECONSTRUCT_SPEC.loader.exec_module(reconstructor)


class PublicCaseBuilderTests(unittest.TestCase):
    def test_vision_vite_adapter_requires_and_hash_binds_frozen_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data" / "frontend" / "vite-demo"
            (data / "prototypes").mkdir(parents=True)
            (data / "prompt.txt").write_text("PUBLIC VITE TASK", encoding="utf-8")
            (data / "prototypes" / "home.png").write_bytes(b"image")
            generated = root / "generation" / "frontend__vite-demo"
            workspace = generated / "workspace"
            workspace.mkdir(parents=True)
            (workspace / "index.html").write_text("<div id='root'></div>", encoding="utf-8")
            package = workspace / "package.json"
            package.write_text(
                json.dumps({"scripts": {"dev": "vite"}, "dependencies": {"vite": "5.4.21"}}),
                encoding="utf-8",
            )
            (generated / "result.json").write_text(
                json.dumps({"status": "success", "scaffold": "fixture"}),
                encoding="utf-8",
            )
            digest = builder.sha256(package)
            dependency_root = root / "dependencies" / digest
            (dependency_root / "node_modules" / "vite").mkdir(parents=True)
            (dependency_root / "package.json").write_bytes(package.read_bytes())
            (dependency_root / "package-lock.json").write_text(
                '{"lockfileVersion":3}', encoding="utf-8"
            )
            dependency_file = dependency_root / "node_modules" / "vite" / "package.json"
            dependency_file.write_text("{}", encoding="utf-8")
            (dependency_root / "node_modules.sha256").write_text(
                f"{builder.sha256(dependency_file)}  node_modules/vite/package.json\n",
                encoding="utf-8",
            )

            row = builder.vision_case(
                "frontend/vite-demo",
                generation_root=root / "generation",
                data_root=root / "data",
                dependency_cache_root=root / "dependencies",
            )
            self.assertEqual(row["web_runtime"]["adapter"], "frozen_vite_dev_script")
            self.assertEqual(
                row["web_runtime"]["dependency_package_sha256"], digest
            )
            materializer.verify_dependency_manifest(
                dependency_root, dependency_root / "node_modules.sha256"
            )
            target = root / "materialized"
            dependency_source = Path(row["web_runtime"]["dependency_path"])
            materializer.copy_program(
                workspace, target, dependency_source=dependency_source
            )
            self.assertTrue((target / "node_modules").is_symlink())
            self.assertEqual(
                os.readlink(target / "node_modules"), str(dependency_source.resolve())
            )

    def test_vision_adapter_uses_public_task_and_never_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data" / "frontend" / "demo"
            (data / "prototypes").mkdir(parents=True)
            (data / "prompt.txt").write_text("PUBLIC REQUIREMENT", encoding="utf-8")
            (data / "workflow.json").write_text(
                json.dumps({"hidden": "PRIVATE WORKFLOW POISON"}), encoding="utf-8"
            )
            (data / "prototypes" / "home.jpg").write_bytes(b"public-image")
            generated = root / "generation" / "frontend__demo"
            workspace = generated / "workspace"
            workspace.mkdir(parents=True)
            (workspace / "index.html").write_text("<h1>Demo</h1>", encoding="utf-8")
            (generated / "result.json").write_text(
                json.dumps({"status": "success", "scaffold": "fixture"}),
                encoding="utf-8",
            )

            row = builder.vision_case(
                "frontend/demo",
                generation_root=root / "generation",
                data_root=root / "data",
            )
            serialized = json.dumps(row)
            self.assertEqual(row["task"], "PUBLIC REQUIREMENT")
            self.assertNotIn("PRIVATE WORKFLOW POISON", serialized)
            self.assertNotIn("workflow.json", serialized)
            self.assertEqual(row["web_runtime"]["adapter"], "mechanical_static_server")

    def test_vision_adapter_rejects_non_level_two_or_three(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "Level 2/3"):
                builder.vision_case(
                    "webpage/demo", generation_root=root, data_root=root
                )

    def test_interact_adapter_uses_only_first_user_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "shards" / "shard-00" / "interactweb" / "model"
            log = base / "logs" / "case"
            log.mkdir(parents=True)
            history = log / "interaction_history.json"
            history.write_text(
                json.dumps(
                    {
                        "trajectory": [
                            {"role": "system", "content": "SYSTEM"},
                            {"role": "user", "content": "PUBLIC REQUEST"},
                            {"role": "assistant", "content": "CODE"},
                            {"role": "user", "content": "PRIVATE VISUAL COPILOT POISON"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (log / "pending_evaluation.json").write_text(
                json.dumps({"rubric": "PRIVATE EVALUATOR POISON"}), encoding="utf-8"
            )

            freeze = root / "freeze"
            workspace = freeze / "programs" / "case"
            dependency = root / "dependencies" / "node_modules"
            dependency.mkdir(parents=True)
            workspace.mkdir(parents=True)
            (workspace / "node_modules").symlink_to(
                dependency, target_is_directory=True
            )
            (workspace / "index.html").write_text("<h1>Demo</h1>", encoding="utf-8")
            (workspace / "package.json").write_text(
                json.dumps({"scripts": {"dev": "vite"}}), encoding="utf-8"
            )
            (freeze / "provenance.json").write_text(
                json.dumps(
                    {
                        "schema": "multimodalcode-interact-first-runnable-freeze-1",
                        "external_visual_feedback_consumed": False,
                        "official_evaluator_visible": False,
                        "cases": [
                            {
                                "case_id": "case",
                                "selection_rule": "first_pre_visual_execution_feedback_environment_ready",
                                "external_visual_feedback_consumed": False,
                                "official_evaluator_visible": False,
                                "program_path": str(workspace),
                                "public_task_sha256": hashlib.sha256(
                                    b"PUBLIC REQUEST"
                                ).hexdigest(),
                                "source_trajectory_sha256": builder.sha256(history),
                                "cutoff_assistant_turn": 2,
                                "cutoff_feedback_turn": 3,
                                "snapshot_sha256": "fixture",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            row = builder.interact_case(
                "case", run_root=root, first_runnable_root=freeze
            )
            serialized = json.dumps(row)
            self.assertEqual(row["task"], "PUBLIC REQUEST")
            self.assertEqual(row["metadata"]["excluded_followup_user_events"], 1)
            self.assertEqual(
                row["metadata"]["program_version"],
                "first_pre_visual_clean_runnable",
            )
            self.assertNotIn("PRIVATE VISUAL COPILOT POISON", serialized)
            self.assertNotIn("PRIVATE EVALUATOR POISON", serialized)
            self.assertNotIn("pending_evaluation", serialized)

    def test_interact_reconstruction_stops_at_first_clean_pre_visual_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            history = Path(temporary) / "interaction_history.json"
            history.write_text(
                json.dumps(
                    {
                        "trajectory": [
                            {
                                "role": "system",
                                "content": '<boltArtifact><boltAction type="file" filePath="src/App.jsx">SYSTEM POISON</boltAction></boltArtifact>',
                            },
                            {"role": "user", "content": "PUBLIC REQUEST"},
                            {
                                "role": "assistant",
                                "content": '<boltArtifact><boltAction type="file" filePath="package.json">{"scripts":{"dev":"vite"}}</boltAction><boltAction type="file" filePath="index.html">&lt;div id="root"&gt;&lt;/div&gt;</boltAction><boltAction type="file" filePath="src/App.jsx">VERSION ONE</boltAction></boltArtifact>',
                            },
                            {
                                "role": "user",
                                "content": "Execution Feedback:\nRuntime Error: broken\nEnvironment Ready. Verify UI or Submit.",
                            },
                            {
                                "role": "assistant",
                                "content": '<boltArtifact><boltAction type="file" filePath="src/App.jsx">VERSION TWO</boltAction></boltArtifact>',
                            },
                            {
                                "role": "user",
                                "content": "Execution Feedback:\nEnvironment Ready. Verify UI or Submit.",
                            },
                            {
                                "role": "user",
                                "content": "Visual Process Audit: EXTERNAL VISUAL POISON",
                            },
                            {
                                "role": "assistant",
                                "content": '<boltArtifact><boltAction type="file" filePath="src/App.jsx">VERSION THREE POISON</boltAction></boltArtifact>',
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            files, provenance = reconstructor.reconstruct_history(history)

            self.assertEqual(files["src/App.jsx"], "VERSION TWO")
            self.assertNotIn("SYSTEM POISON", json.dumps(files))
            self.assertNotIn("VERSION THREE POISON", json.dumps(files))
            self.assertEqual(provenance["cutoff_assistant_turn"], 4)
            self.assertEqual(provenance["cutoff_feedback_turn"], 5)
            self.assertEqual(provenance["artifact_turn_count"], 2)
            self.assertFalse(provenance["external_visual_feedback_consumed"])
            self.assertFalse(provenance["official_evaluator_visible"])

    def test_interact_reconstruction_rejects_no_clean_pre_visual_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            history = Path(temporary) / "interaction_history.json"
            history.write_text(
                json.dumps(
                    {
                        "trajectory": [
                            {"role": "user", "content": "PUBLIC REQUEST"},
                            {
                                "role": "assistant",
                                "content": '<boltArtifact><boltAction type="file" filePath="package.json">{}</boltAction><boltAction type="file" filePath="index.html">ok</boltAction></boltArtifact>',
                            },
                            {
                                "role": "user",
                                "content": "Execution Feedback:\nRuntime Error: broken",
                            },
                            {"role": "user", "content": "Visual Process Audit: STOP"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "No clean pre-Visual-Copilot"):
                reconstructor.reconstruct_history(history)

    def test_interact_reconstruction_rejects_unsafe_file_action(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            history = Path(temporary) / "interaction_history.json"
            history.write_text(
                json.dumps(
                    {
                        "trajectory": [
                            {"role": "user", "content": "PUBLIC REQUEST"},
                            {
                                "role": "assistant",
                                "content": '<boltArtifact><boltAction type="file" filePath="../poison">bad</boltAction></boltArtifact>',
                            },
                            {
                                "role": "user",
                                "content": "Execution Feedback:\nEnvironment Ready. Verify UI or Submit.",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Unsafe InteractWeb"):
                reconstructor.reconstruct_history(history)

    def test_interact_snapshot_dependency_rejection_leaves_no_partial_program(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dependency = root / "released" / "node_modules"
            dependency.mkdir(parents=True)
            (dependency.parent / "package.json").write_text(
                '{"name":"different"}', encoding="utf-8"
            )
            (dependency.parent / "package-lock.json").write_text(
                '{"lockfileVersion":3}', encoding="utf-8"
            )
            target = root / "freeze" / "program"

            with self.assertRaisesRegex(ValueError, "package.json differs"):
                reconstructor.write_snapshot(
                    {
                        "package.json": '{"name":"expected"}',
                        "index.html": "ok",
                    },
                    target,
                    dependency_source=dependency,
                )

            self.assertFalse(target.exists())

    def test_materialization_makes_public_source_readable_without_copying_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            dependency = source / "node_modules" / "vite"
            dependency.mkdir(parents=True)
            private_mode_source = source / "prd.md"
            private_mode_source.write_text("PUBLIC PRD", encoding="utf-8")
            private_mode_source.chmod(0o600)
            (dependency / "package.json").write_text("{}", encoding="utf-8")
            target = root / "materialized"

            linked_dependency = materializer.copy_program(source, target)

            self.assertEqual(target.joinpath("prd.md").read_text(), "PUBLIC PRD")
            self.assertEqual(target.joinpath("prd.md").stat().st_mode & 0o777, 0o644)
            self.assertTrue(target.joinpath("node_modules").is_symlink())
            self.assertEqual(
                os.readlink(target / "node_modules"), str((source / "node_modules").resolve())
            )
            self.assertEqual(linked_dependency, (source / "node_modules").resolve())

    def test_materialization_can_copy_hash_identical_dependencies_node_locally(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            dependency = source / "node_modules" / "vite" / "bin"
            dependency.mkdir(parents=True)
            (source / "index.html").write_text("ok", encoding="utf-8")
            (dependency / "vite.js").write_text("console.log('vite')", encoding="utf-8")
            binaries = source / "node_modules" / ".bin"
            binaries.mkdir()
            (binaries / "vite").symlink_to("../vite/bin/vite.js")
            target = root / "materialized"

            source_identity = materializer.dependency_tree_identity(
                source / "node_modules"
            )
            linked_dependency = materializer.copy_program(
                source, target, copy_dependencies=True
            )

            self.assertEqual(linked_dependency, (source / "node_modules").resolve())
            self.assertTrue((target / "node_modules").is_dir())
            self.assertFalse((target / "node_modules").is_symlink())
            self.assertTrue((target / "node_modules" / ".bin" / "vite").is_symlink())
            self.assertEqual(
                materializer.dependency_tree_identity(target / "node_modules"),
                source_identity,
            )

    def test_materialization_provenance_keeps_case_manifest_and_dependency_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            dependency = source / "node_modules" / "vite"
            dependency.mkdir(parents=True)
            (source / "index.html").write_text("ok", encoding="utf-8")
            (dependency / "package.json").write_text("{}", encoding="utf-8")
            case_manifest = root / "cases.jsonl"
            case_manifest.write_text(
                json.dumps(
                    {
                        "case_id": "case",
                        "program_path": str(source),
                        "web_runtime": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output = root / "materialized"

            with mock.patch.object(
                materializer.argparse.ArgumentParser,
                "parse_args",
                return_value=materializer.argparse.Namespace(
                    cases=str(case_manifest), output_dir=str(output)
                ),
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(materializer.main(), 0)

            provenance = json.loads(
                (output / "provenance.json").read_text(encoding="utf-8")
            )
            self.assertEqual(provenance["source_manifest"], str(case_manifest.resolve()))
            self.assertEqual(
                provenance["source_manifest_sha256"], builder.sha256(case_manifest)
            )
            self.assertEqual(
                provenance["cases"][0]["dependency_source"],
                str((source / "node_modules").resolve()),
            )

    def test_case_workspace_keeps_vite_cache_local_and_dependencies_linked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            dependency = source / "node_modules" / "vite"
            old_cache = source / "node_modules" / ".vite"
            dependency.mkdir(parents=True)
            old_cache.mkdir()
            (source / "index.html").write_text("<h1>ok</h1>", encoding="utf-8")
            (dependency / "package.json").write_text("{}", encoding="utf-8")
            (old_cache / "stale").write_text("old", encoding="utf-8")

            tool = SafeFileToolExecutor.prepare(
                source, root / "workspace", root / "checkpoints"
            )

            self.assertTrue((tool.workspace / "node_modules" / "vite").is_symlink())
            self.assertTrue((tool.workspace / "node_modules" / ".vite").is_dir())
            self.assertFalse((tool.workspace / "node_modules" / ".vite").is_symlink())
            self.assertFalse((tool.workspace / "node_modules" / ".vite" / "stale").exists())


if __name__ == "__main__":
    unittest.main()
