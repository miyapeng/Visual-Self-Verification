from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from multimodalcode.backends import ModelBackend
from multimodalcode.research.agent import (
    AgentCase,
    AgentLoop,
    _stable_evidence_text,
    _stable_url,
    _local_console_errors,
)
from multimodalcode.research.context import (
    ConservedFrontierPolicy,
    ContextBudget,
    ContextRecord,
    FrontierPolicy,
    FullHistoryPolicy,
    GuardedFrontierPolicy,
    GuardedFrontierTextOnlyPolicy,
    LLMSummaryPolicy,
    OracleEvidencePolicy,
    RecentPolicy,
    ResidualPolicy,
    SummaryPolicy,
    UnboundFrontierPolicy,
)
from multimodalcode.research.events import EventLog, EvidenceStore
from multimodalcode.research.interactive import (
    InteractiveJudge,
    hash_program,
    runtime_source_context,
)
from multimodalcode.research.planner import OneShotActionPlanner
from multimodalcode.research.schema import ActionPlan
from multimodalcode.research.tools import SafeFileToolExecutor, parse_revision
from multimodalcode.schema import GenerationRequest
from multimodalcode.research.cli import _load_cases


def _fixture_plan() -> ActionPlan:
    return ActionPlan.from_dict(
        {
            "planner": "unit-test-fixed-plan",
            "checklist": [
                {
                    "id": "interaction",
                    "description": "Form and button interactions work",
                    "expected": "Click and keyboard actions update visible text",
                },
                {
                    "id": "navigation",
                    "description": "The second page is reachable",
                    "expected": "URL and heading identify page two",
                },
            ],
            "actions": [
                {"type": "screenshot", "checklist_id": "interaction"},
                {
                    "type": "fill",
                    "selector": "#name",
                    "value": "Ada",
                    "checklist_id": "interaction",
                },
                {"type": "hover", "selector": "#go", "checklist_id": "interaction"},
                {"type": "click", "selector": "#go", "checklist_id": "interaction"},
                {
                    "type": "assert_text",
                    "selector": "#result",
                    "expected": "Hello Ada",
                    "checklist_id": "interaction",
                },
                {
                    "type": "press",
                    "selector": "#name",
                    "key": "Enter",
                    "checklist_id": "interaction",
                },
                {
                    "type": "assert_text",
                    "selector": "#key-result",
                    "expected": "Enter received",
                    "checklist_id": "interaction",
                },
                {"type": "scroll", "x": 0, "y": 500, "checklist_id": "interaction"},
                {"type": "wait", "milliseconds": 5, "checklist_id": "interaction"},
                {"type": "navigate", "url": "page2.html", "checklist_id": "navigation"},
                {
                    "type": "assert_url",
                    "expected": "page2.html",
                    "checklist_id": "navigation",
                },
                {
                    "type": "assert_visible",
                    "selector": "h1",
                    "expected": "true",
                    "checklist_id": "navigation",
                },
                {
                    "type": "assert_text",
                    "selector": "h1",
                    "expected": "Page Two",
                    "checklist_id": "navigation",
                },
            ],
        }
    )


def _write_site(root: Path) -> Path:
    site = root / "site"
    site.mkdir()
    (site / "index.html").write_text(
        """<!doctype html><html><body>
        <input id="name" onkeydown="if(event.key==='Enter') document.querySelector('#key-result').textContent='Enter received'">
        <button id="go" type="button" onmouseover="document.body.dataset.hovered='yes'"
          onclick="document.querySelector('#result').textContent='Hello '+document.querySelector('#name').value; console.log('clicked')">Go</button>
        <p id="result"></p><p id="key-result"></p><div style="height:1200px"></div>
        <script>window.addEventListener('keydown', () => {});</script>
        </body></html>""",
        encoding="utf-8",
    )
    (site / "page2.html").write_text(
        "<!doctype html><html><body><h1>Page Two</h1></body></html>",
        encoding="utf-8",
    )
    return site


class _RecordingBackend(ModelBackend):
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.requests: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> str:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("Unexpected extra generator call")
        return self.responses.pop(0)


class SchemaTests(unittest.TestCase):
    def test_plan_rejects_unknown_checklist(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown checklist"):
            ActionPlan.from_dict(
                {
                    "checklist": [{"id": "known", "expected": "x"}],
                    "actions": [{"type": "wait", "checklist_id": "missing"}],
                }
            )

    def test_case_loader_freezes_reference_image_outside_mutable_workspace(self) -> None:
        manifest = Path(__file__).resolve().parents[1] / "configs" / "research" / "webcompass_local_cases.json"
        case = _load_cases(
            manifest,
            case_ids=["webcompass-video-83-diamond-wave"],
        )[0]
        reference = next(
            action.reference_image
            for action in case.plan.actions
            if action.type == "assert_visual_signature"
        )
        self.assertIsNotNone(reference)
        self.assertTrue(Path(str(reference)).is_absolute())
        self.assertTrue(Path(str(reference)).is_file())
        self.assertNotIn("/workspace/", str(reference))


class EvidenceTests(unittest.TestCase):
    def test_event_resume_and_content_addressed_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = EventLog(root / "events.jsonl")
            first = log.append("one", {"x": 1}, case_id="case", code_version="v1")
            second_log = EventLog(root / "events.jsonl")
            second = second_log.append("two", {"x": 2}, case_id="case", code_version="v1")
            self.assertEqual((first["sequence"], second["sequence"]), (0, 1))
            store = EvidenceStore(root / "evidence")
            a = store.add_text(
                "same", kind="dom", case_id="case", code_version="v1"
            )
            b = store.add_text(
                "same", kind="dom", case_id="case", code_version="v2"
            )
            self.assertEqual(a["evidence_id"], b["evidence_id"])
            self.assertEqual(len(list((root / "evidence" / "objects").iterdir())), 1)
            self.assertEqual(len(store.records()), 2)


class ToolExecutorTests(unittest.TestCase):
    def test_runtime_dependencies_are_linked_but_not_hashed_or_rendered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "index.html").write_text("<h1>public source</h1>", encoding="utf-8")
            dependencies = source / "node_modules" / "demo"
            dependencies.mkdir(parents=True)
            dependency = dependencies / "index.js"
            dependency.write_text("large runtime dependency", encoding="utf-8")
            generated = source / "dist"
            generated.mkdir()
            bundle = generated / "bundle.js"
            bundle.write_text("generated-v1", encoding="utf-8")
            before = hash_program(source)
            dependency.write_text("changed dependency", encoding="utf-8")
            bundle.write_text("generated-v2", encoding="utf-8")
            self.assertEqual(before, hash_program(source))

            tool = SafeFileToolExecutor.prepare(
                source, root / "workspace", root / "checkpoints"
            )
            self.assertTrue((tool.workspace / "node_modules").is_dir())
            self.assertFalse((tool.workspace / "node_modules").is_symlink())
            self.assertTrue((tool.workspace / "node_modules" / "demo").is_symlink())
            self.assertTrue((tool.workspace / "node_modules" / ".vite").is_dir())
            self.assertFalse((tool.workspace / "dist").exists())
            self.assertNotIn("runtime dependency", tool.render_program())
            version = hash_program(tool.workspace)
            tool.checkpoint(version)
            (tool.workspace / "index.html").write_text("changed", encoding="utf-8")
            tool.restore(version)
            self.assertTrue((tool.workspace / "node_modules" / "demo").is_symlink())
            self.assertTrue((tool.workspace / "node_modules" / ".vite").is_dir())
            self.assertIn("public source", (tool.workspace / "index.html").read_text())

    def test_program_rendering_restricts_to_complete_explicit_source_units(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "src").mkdir(parents=True)
            (source / "index.html").write_text("<h1>runtime entry</h1>", encoding="utf-8")
            (source / "src" / "main.js").write_text("window.active = true;", encoding="utf-8")
            (source / "src" / "dead.js").write_text("window.dead = true;", encoding="utf-8")
            tool = SafeFileToolExecutor.prepare(
                source, root / "workspace", root / "checkpoints"
            )
            rendered, manifest = tool.render_program_context(
                relative_paths=["index.html", "src/main.js"]
            )
            self.assertIn("runtime entry", rendered)
            self.assertIn("window.active", rendered)
            self.assertNotIn("window.dead", rendered)
            self.assertEqual(
                [row["path"] for row in manifest["selected_files"]],
                ["index.html", "src/main.js"],
            )
            self.assertTrue(all(not row["truncated"] for row in manifest["selected_files"]))
            self.assertEqual(manifest["rendered_bytes"], len(rendered.encode("utf-8")))

    def test_runtime_resource_graph_maps_only_same_origin_workspace_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "site"
            (root / "src").mkdir(parents=True)
            (root / "index.html").write_text("<script src='/src/main.js'></script>")
            (root / "src" / "main.js").write_text("window.ok = true;")
            (root / "src" / "dead.js").write_text("window.dead = true;")
            context = runtime_source_context(
                root,
                [
                    {
                        "sequence": 0,
                        "url": "http://127.0.0.1:43123/",
                        "resource_type": "document",
                    },
                    {
                        "sequence": 1,
                        "url": "http://127.0.0.1:43123/@vite/client",
                        "resource_type": "script",
                    },
                    {
                        "sequence": 2,
                        "url": "http://127.0.0.1:43123/src/main.js?t=1",
                        "resource_type": "script",
                    },
                    {
                        "sequence": 3,
                        "url": "https://example.invalid/external.js",
                        "resource_type": "script",
                    },
                ],
                entry_url="http://127.0.0.1:43123/",
            )
            self.assertEqual(
                [row["path"] for row in context["files"]],
                ["index.html", "src/main.js"],
            )
            self.assertNotIn("src/dead.js", {row["path"] for row in context["files"]})
            self.assertEqual(context["fallback"], None)

    def test_patch_is_confined_to_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = _write_site(root)
            tool = SafeFileToolExecutor.prepare(site, root / "workspace", root / "checkpoints")
            tool.apply_files({"index.html": "new"})
            self.assertEqual((root / "workspace" / "index.html").read_text(), "new")
            with self.assertRaisesRegex(ValueError, "escapes workspace"):
                tool.apply_files({"../escape.txt": "bad"})

    def test_exact_revision_edits_existing_and_creates_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = _write_site(root)
            tool = SafeFileToolExecutor.prepare(
                site, root / "workspace", root / "checkpoints"
            )
            written = tool.apply_revision(
                edits=[
                    {
                        "path": "index.html",
                        "old": "'Hello '+",
                        "new": "'Hi '+",
                        "replace_all": False,
                    }
                ],
                files={"script.js": "console.log('new');"},
            )
            self.assertEqual(written, ["index.html", "script.js"])
            self.assertIn(
                "'Hi '+", (root / "workspace" / "index.html").read_text()
            )
            with self.assertRaisesRegex(ValueError, "only create a new file"):
                tool.apply_revision(edits=[], files={"index.html": "overwrite"})

    def test_revision_validation_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = _write_site(root)
            tool = SafeFileToolExecutor.prepare(
                site, root / "workspace", root / "checkpoints"
            )
            before = (root / "workspace" / "index.html").read_text(encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "only create a new file"):
                tool.apply_revision(
                    edits=[
                        {
                            "path": "index.html",
                            "old": "'Hello '+",
                            "new": "'Hi '+",
                        }
                    ],
                    files={"index.html": "unsafe overwrite"},
                )
            self.assertEqual(
                (root / "workspace" / "index.html").read_text(encoding="utf-8"),
                before,
            )

    def test_admission_keeps_exact_edit_and_rejects_redundant_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = _write_site(root)
            tool = SafeFileToolExecutor.prepare(
                site, root / "workspace", root / "checkpoints"
            )
            written, rejected = tool.apply_revision_with_admission(
                edits=[
                    {
                        "path": "index.html",
                        "old": "'Hello '+",
                        "new": "'Hi '+",
                    }
                ],
                files={"index.html": "unsafe overwrite"},
            )
            self.assertEqual(written, ["index.html"])
            self.assertEqual(rejected, ["index.html"])
            result = (root / "workspace" / "index.html").read_text(encoding="utf-8")
            self.assertIn("'Hi '+", result)
            self.assertNotEqual(result, "unsafe overwrite")

    def test_revision_admission_validation_does_not_mutate_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = _write_site(root)
            tool = SafeFileToolExecutor.prepare(
                site, root / "workspace", root / "checkpoints"
            )
            before = (tool.workspace / "index.html").read_text(encoding="utf-8")
            written, rejected = tool.validate_revision_with_admission(
                edits=[
                    {
                        "path": "index.html",
                        "old": "'Hello '+",
                        "new": "'Hi '+",
                    }
                ],
                files={"index.html": "unsafe overwrite"},
            )
            self.assertEqual(written, ["index.html"])
            self.assertEqual(rejected, ["index.html"])
            self.assertEqual(
                (tool.workspace / "index.html").read_text(encoding="utf-8"),
                before,
            )
            with self.assertRaisesRegex(ValueError, "not found"):
                tool.validate_revision_with_admission(
                    edits=[
                        {
                            "path": "index.html",
                            "old": "missing exact text",
                            "new": "replacement",
                        }
                    ],
                    files={},
                )
            self.assertEqual(
                (tool.workspace / "index.html").read_text(encoding="utf-8"),
                before,
            )

    def test_exact_revision_rejects_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = _write_site(root)
            tool = SafeFileToolExecutor.prepare(
                site, root / "workspace", root / "checkpoints"
            )
            with self.assertRaisesRegex(ValueError, "no-op"):
                tool.apply_revision(
                    edits=[
                        {
                            "path": "index.html",
                            "old": "'Hello '+",
                            "new": "'Hello '+",
                        }
                    ],
                    files={},
                )

    def test_revision_contract_parses_exact_edits(self) -> None:
        parsed = parse_revision(
            json.dumps(
                {
                    "decision": "patch",
                    "edits": [
                        {"path": "a.js", "old": "const x = 1;", "new": "const x = 2;"}
                    ],
                }
            )
        )
        self.assertEqual(parsed["edits"][0]["path"], "a.js")
        self.assertEqual(parsed["files"], {})

    def test_revision_contract_rejects_misnested_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            parse_revision(
                json.dumps(
                    {
                        "decision": "patch",
                        "edits": [
                            {
                                "path": "a.js",
                                "old": "const x = 1;",
                                "new": "const x = 2;",
                                "files": {"new.js": "content"},
                            }
                        ],
                    }
                )
            )

    def test_revision_contract_hoists_harmless_nested_reason(self) -> None:
        parsed = parse_revision(
            json.dumps(
                {
                    "decision": "patch",
                    "edits": [
                        {
                            "path": "a.js",
                            "old": "const x = 1;",
                            "new": "const x = 2;",
                            "reason": "Fix x.",
                        }
                    ],
                }
            )
        )
        self.assertEqual(parsed["reason"], "Fix x.")
        self.assertEqual(parsed["normalizations"], ["edit[0].reason->reason"])

    def test_revision_contract_parser(self) -> None:
        parsed = parse_revision(
            '```json\n{"decision":"patch","files":{"index.html":"ok"}}\n```'
        )
        self.assertEqual(parsed["decision"], "patch")
        self.assertEqual(parsed["files"], {"index.html": "ok"})

    def test_revision_contract_recovers_an_identical_duplicate(self) -> None:
        response = '{"decision":"patch","files":{"index.html":"ok"}}'
        parsed = parse_revision(response + response)
        self.assertEqual(parsed["decision"], "patch")
        self.assertEqual(parsed["files"], {"index.html": "ok"})

    def test_revision_contract_recovers_an_extra_closing_brace(self) -> None:
        parsed = parse_revision(
            '{"decision":"patch","files":{"index.html":"ok"}}}'
        )
        self.assertEqual(parsed["decision"], "patch")
        self.assertEqual(parsed["files"], {"index.html": "ok"})

    def test_revision_contract_closes_one_missing_outer_brace(self) -> None:
        parsed = parse_revision(
            '{"decision":"patch","files":{"index.html":"ok"}'
        )
        self.assertEqual(parsed["files"], {"index.html": "ok"})
        self.assertEqual(
            parsed["normalizations"],
            ["append_missing_json_delimiters:}"],
        )

    def test_revision_contract_closes_missing_array_and_object(self) -> None:
        parsed = parse_revision(
            '{"decision":"patch","edits":['
            '{"path":"a.js","old":"x","new":"y"}'
        )
        self.assertEqual(parsed["edits"][0]["path"], "a.js")
        self.assertEqual(
            parsed["normalizations"],
            ["append_missing_json_delimiters:]}"],
        )

    def test_revision_contract_does_not_repair_semantic_truncation(self) -> None:
        invalid = (
            '{"decision":"patch","files":{"index.html":"unterminated',
            '{"decision":"patch","files":{"index.html":"ok"},',
            '{"decision":"patch","files":',
            '{"decision":"patch","files":{"x":{"y":{"z":"ok"}',
        )
        for response in invalid:
            with self.subTest(response=response):
                with self.assertRaisesRegex(ValueError, "valid JSON"):
                    parse_revision(response)

    def test_revision_contract_rejects_different_multiple_objects(self) -> None:
        with self.assertRaisesRegex(ValueError, "multiple different JSON objects"):
            parse_revision(
                '{"decision":"keep"}'
                '{"decision":"patch","files":{"index.html":"changed"}}'
            )


class ContextPolicyTests(unittest.TestCase):
    def test_policies_share_budget_but_select_different_evidence(self) -> None:
        records = [
            ContextRecord(
                "old", "verified_obligation", "old passing state", "v1", 0,
                checklist_id="old", status="pass"
            ),
            ContextRecord(
                "stale", "verification_residual", "old failure", "v1", 1,
                checklist_id="stale", status="fail"
            ),
            ContextRecord(
                "current-pass", "verified_obligation", "now correct", "v2", 2,
                checklist_id="pass", status="pass"
            ),
            ContextRecord(
                "current-fail",
                "verification_residual",
                "button does not update",
                "v2",
                3,
                checklist_id="fail",
                status="fail",
                is_first_failure=True,
            ),
            ContextRecord(
                "regression",
                "verification_residual",
                "navigation regressed",
                "v2",
                4,
                checklist_id="regression",
                status="fail",
                is_regression=True,
            ),
            ContextRecord(
                "trace-fail",
                "interactive_trace",
                "a later failed action trace",
                "v2",
                5,
                checklist_id="fail",
                status="fail",
            ),
        ]
        budget = ContextBudget(max_text_tokens=1000, max_images=0, max_image_pixels=0)
        full = FullHistoryPolicy().select(records, current_version="v2", budget=budget)
        recent = RecentPolicy(k=2).select(records, current_version="v2", budget=budget)
        summary = SummaryPolicy().select(records, current_version="v2", budget=budget)
        residual = ResidualPolicy().select(records, current_version="v2", budget=budget)
        self.assertEqual(len(full.records), 6)
        self.assertEqual(
            {record.record_id for record in recent.records}, {"regression", "trace-fail"}
        )
        self.assertEqual(len(summary.records), 1)
        self.assertIn("Latest checklist states", summary.records[0].text)
        self.assertEqual(
            {record.record_id for record in residual.records},
            {"current-fail", "regression"},
        )
        self.assertNotIn("stale", {record.record_id for record in residual.records})
        self.assertNotIn("trace-fail", {record.record_id for record in residual.records})

    def test_frontier_selects_earliest_open_residual(self) -> None:
        records = [
            ContextRecord(
                "later",
                "verification_residual",
                "downstream failure",
                "v2",
                8,
                checklist_id="reset",
                status="fail",
                metadata={"first_failing_action": 10},
            ),
            ContextRecord(
                "earlier",
                "verification_residual",
                "causal boundary",
                "v2",
                7,
                checklist_id="reveal",
                status="fail",
                metadata={"first_failing_action": 6},
            ),
            ContextRecord(
                "stale",
                "verification_residual",
                "old failure",
                "v1",
                2,
                checklist_id="old",
                status="fail",
                metadata={"first_failing_action": 1},
            ),
        ]
        selected = FrontierPolicy().select(
            records,
            current_version="v2",
            budget=ContextBudget(max_text_tokens=100, max_images=0),
        )
        self.assertEqual([record.record_id for record in selected.records], ["earlier"])

    def test_guarded_frontier_adds_only_newly_certified_boundary(self) -> None:
        records = [
            ContextRecord(
                "old-pass",
                "verified_obligation",
                "old pass",
                "v2",
                1,
                checklist_id="old",
                status="pass",
                metadata={"is_newly_passed": False},
            ),
            ContextRecord(
                "guard",
                "verified_obligation",
                "newly certified guard",
                "v2",
                2,
                checklist_id="guard",
                status="pass",
                metadata={"is_newly_passed": True},
            ),
            ContextRecord(
                "frontier",
                "verification_residual",
                "current failure",
                "v2",
                3,
                checklist_id="open",
                status="fail",
                metadata={"first_failing_action": 8},
            ),
        ]
        selected = GuardedFrontierPolicy().select(
            records,
            current_version="v2",
            budget=ContextBudget(max_text_tokens=100, max_images=0),
        )
        self.assertEqual(
            [record.record_id for record in selected.records],
            ["guard", "frontier"],
        )

    def test_text_only_ablation_preserves_certificate_text(self) -> None:
        record = ContextRecord(
            "frontier",
            "verification_residual",
            "expected button enabled; observed disabled",
            "v2",
            3,
            checklist_id="open",
            status="fail",
            image_path="/not/read/by-selection.png",
            image_pixels=640 * 480,
            metadata={"first_failing_action": 8},
        )
        selected = GuardedFrontierTextOnlyPolicy().select(
            [record],
            current_version="v2",
            budget=ContextBudget(max_text_tokens=100, max_images=0),
        )
        self.assertEqual(selected.render_text(), record.text)
        self.assertEqual(selected.image_paths, [])
        self.assertEqual(selected.image_count, 0)
        self.assertEqual(selected.metadata["stripped_image_records"], 1)
        self.assertEqual(selected.metadata["selection_code_version"], "v2")

    def test_unbound_frontier_selects_stale_counterexample(self) -> None:
        records = [
            ContextRecord(
                "stale",
                "verification_residual",
                "old failure",
                "v1",
                1,
                checklist_id="old",
                status="fail",
                metadata={"first_failing_action": 2},
            ),
            ContextRecord(
                "current",
                "verification_residual",
                "current failure",
                "v2",
                2,
                checklist_id="current",
                status="fail",
                metadata={"first_failing_action": 5},
            ),
        ]
        budget = ContextBudget(max_text_tokens=100, max_images=0)
        guarded = GuardedFrontierPolicy().select(
            records, current_version="v2", budget=budget
        )
        unbound = UnboundFrontierPolicy().select(
            records, current_version="v2", budget=budget
        )
        self.assertEqual([row.record_id for row in guarded.records], ["current"])
        self.assertEqual([row.record_id for row in unbound.records], ["stale"])

    def test_conserved_frontier_keeps_every_obligation_and_withholds_stale_payload(self) -> None:
        records = [
            ContextRecord(
                "stale-pass", "verified_obligation", "old success details", "v1", 1,
                checklist_id="search", status="pass",
            ),
            ContextRecord(
                "current-pass", "verified_obligation", "navigation works", "v2", 2,
                checklist_id="navigation", status="pass",
            ),
            ContextRecord(
                "current-fail", "verification_residual", "button remains disabled", "v2", 3,
                checklist_id="button", status="fail", is_first_failure=True,
                metadata={"first_failing_action": 4},
            ),
            ContextRecord(
                "current-blocked", "verification_residual", "browser could not start", "v2", 4,
                checklist_id="checkout", status="blocked",
                metadata={"first_failing_action": None},
            ),
        ]
        selected = ConservedFrontierPolicy().select(
            records,
            current_version="v2",
            budget=ContextBudget(max_text_tokens=1000, max_images=0),
        )
        self.assertEqual(selected.records[0].kind, "requirement_ledger")
        self.assertEqual(
            selected.metadata["obligation_states"],
            {
                "button": "REFUTED",
                "checkout": "BLOCKED",
                "navigation": "SUPPORTED",
                "search": "RECHECK",
            },
        )
        self.assertIn("search | RECHECK | stale-payload-withheld", selected.render_text())
        self.assertNotIn("old success details", selected.render_text())
        self.assertEqual(selected.metadata["detailed_frontier_id"], "current-fail")
        self.assertEqual(
            [record.record_id for record in selected.records],
            [selected.records[0].record_id, "current-fail"],
        )

    def test_conserved_frontier_reopens_support_after_a_state_change(self) -> None:
        record = ContextRecord(
            "pass-v2", "verified_obligation", "requirement passed", "v2", 5,
            checklist_id="save", status="pass",
        )
        policy = ConservedFrontierPolicy()
        current = policy.select(
            [record], current_version="v2",
            budget=ContextBudget(max_text_tokens=300, max_images=0),
        )
        edited = policy.select(
            [record], current_version="v3",
            budget=ContextBudget(max_text_tokens=300, max_images=0),
        )
        self.assertEqual(current.metadata["obligation_states"]["save"], "SUPPORTED")
        self.assertEqual(edited.metadata["obligation_states"]["save"], "RECHECK")

    def test_conserved_frontier_fails_closed_when_ledger_exceeds_budget(self) -> None:
        records = [
            ContextRecord(
                f"failure-{index}", "verification_residual", "x" * 300, "v1", index,
                checklist_id=f"requirement-{index}", status="fail",
            )
            for index in range(4)
        ]
        with self.assertRaisesRegex(ValueError, "cannot encode the conserved"):
            ConservedFrontierPolicy().select(
                records,
                current_version="v1",
                budget=ContextBudget(max_text_tokens=8, max_images=0),
            )

    def test_oracle_selects_only_open_nodes_with_passing_predecessors(self) -> None:
        records = [
            ContextRecord(
                "root-pass", "verified_obligation", "root", "v2", 1,
                checklist_id="root", status="pass",
                metadata={"oracle_predecessors": []},
            ),
            ContextRecord(
                "branch-fail", "verification_residual", "branch", "v2", 2,
                checklist_id="branch", status="fail",
                metadata={"oracle_predecessors": ["root"], "first_failing_action": 3},
            ),
            ContextRecord(
                "downstream-fail", "verification_residual", "downstream", "v2", 3,
                checklist_id="downstream", status="fail",
                metadata={"oracle_predecessors": ["branch"], "first_failing_action": 7},
            ),
        ]
        selected = OracleEvidencePolicy().select(
            records,
            current_version="v2",
            budget=ContextBudget(max_text_tokens=100, max_images=0),
        )
        self.assertEqual([record.record_id for record in selected.records], ["branch-fail"])

    def test_llm_summary_is_cached_and_reports_separate_cost(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            backend = _RecordingBackend(["Current failure: button remains disabled."])
            policy = LLMSummaryPolicy(
                backend=backend,
                artifact_dir=Path(temporary) / "summaries",
                model_name="mock",
                max_tokens=64,
            )
            records = [
                ContextRecord(
                    "failure", "verification_residual", "button is disabled", "v1", 1,
                    checklist_id="button", status="fail",
                    metadata={"execution_id": "execution-1"},
                )
            ]
            budget = ContextBudget(max_text_tokens=100, max_images=0)
            first = policy.select(records, current_version="v1", budget=budget)
            second = policy.select(records, current_version="v1", budget=budget)
            self.assertEqual(len(backend.requests), 1)
            self.assertIn("button remains disabled", first.render_text())
            self.assertEqual(first.render_text(), second.render_text())
            self.assertEqual(first.metadata["context_policy_calls"], 1)
            self.assertGreater(first.metadata["context_policy_input_tokens"], 0)

    def test_context_removes_run_identity_and_prompt_hides_policy_name(self) -> None:
        first = (
            "Failed to load file:///tmp/run/full/cases/c/workspace/script.js "
            "from file:///tmp/run/full/cases/c/workspace/index.html"
        )
        second = first.replace("/tmp/run/full", "/tmp/another/recent")
        self.assertEqual(_stable_evidence_text(first), _stable_evidence_text(second))
        self.assertEqual(
            _stable_url("file:///tmp/run/full/cases/c/workspace/index.html"),
            "workspace/index.html",
        )
        self.assertEqual(
            _stable_url("http://127.0.0.1:43123/src/main.js"),
            "http://127.0.0.1:<port>/src/main.js",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = _write_site(root)
            tool = SafeFileToolExecutor.prepare(
                site, root / "workspace", root / "checkpoints"
            )
            case = AgentCase(
                case_id="same-input",
                task="Repair the page.",
                program_path=str(site),
                plan=_fixture_plan(),
            )
            full = AgentLoop(
                backend=_RecordingBackend([]),
                model_name="mock",
                run_dir=root / "full",
                policy=FullHistoryPolicy(),
                budget=ContextBudget(),
            )
            recent = AgentLoop(
                backend=_RecordingBackend([]),
                model_name="mock",
                run_dir=root / "recent",
                policy=RecentPolicy(k=8),
                budget=ContextBudget(),
            )
            self.assertEqual(
                full._revision_prompt(case, tool, "identical evidence"),
                recent._revision_prompt(case, tool, "identical evidence"),
            )

    def test_execution_rooted_agent_prompt_excludes_unloaded_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = root / "site"
            (site / "src").mkdir(parents=True)
            (site / "index.html").write_text("<h1>active entry</h1>", encoding="utf-8")
            (site / "src" / "dead.js").write_text(
                "window.deadImplementation = true;", encoding="utf-8"
            )
            tool = SafeFileToolExecutor.prepare(
                site, root / "workspace", root / "checkpoints"
            )
            loop = AgentLoop(
                backend=_RecordingBackend([]),
                model_name="mock",
                run_dir=root / "run",
                policy=FullHistoryPolicy(),
                budget=ContextBudget(),
                source_context_mode="execution_rooted",
            )
            rendered, manifest = loop._program_source_context(
                tool,
                {
                    "code_version": hash_program(tool.workspace),
                    "execution_id": "execution",
                    "runtime_source_context": {
                        "files": [{"path": "index.html"}],
                        "fallback": None,
                    },
                },
            )
            prompt = loop._revision_prompt(
                AgentCase(
                    case_id="rooted",
                    task="Repair the page.",
                    program_path=str(site),
                    plan=_fixture_plan(),
                ),
                tool,
                "failure evidence",
                program_context=rendered,
            )
            self.assertIn("active entry", prompt)
            self.assertNotIn("deadImplementation", prompt)
            self.assertEqual(
                [row["path"] for row in manifest["render"]["selected_files"]],
                ["index.html"],
            )

    def test_local_console_certificate_deduplicates_missing_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "console.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "type": "requestfailed",
                            "text": "GET file:///tmp/a/workspace/js/app.js net::ERR_FILE_NOT_FOUND",
                            "location": {},
                        },
                        {
                            "type": "error",
                            "text": "Failed to load resource: net::ERR_FILE_NOT_FOUND",
                            "location": {
                                "url": "file:///tmp/a/workspace/js/app.js"
                            },
                        },
                        {
                            "type": "requestfailed",
                            "text": "GET https://example.invalid/font.woff net::ERR_FAILED",
                            "location": {},
                        },
                        {
                            "type": "pageerror",
                            "text": "ReferenceError: init is not defined",
                            "stack": (
                                "ReferenceError: init is not defined\n"
                                "    at http://127.0.0.1:43123/src/main.js:9:2"
                            ),
                            "location": {},
                        },
                    ]
                ),
                encoding="utf-8",
            )
            certificate = json.loads(_local_console_errors(str(path), 2400))
            self.assertEqual(
                certificate["missing_local_resources"], ["workspace/js/app.js"]
            )
            self.assertEqual(len(certificate["page_errors"]), 1)
            self.assertIn("ReferenceError: init is not defined", certificate["page_errors"][0])
            self.assertIn(
                "http://127.0.0.1:<port>/src/main.js:9:2",
                certificate["page_errors"][0],
            )

class OneShotPlannerTests(unittest.TestCase):
    def test_planner_inspects_once_freezes_plan_and_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = _write_site(root)
            backend = _RecordingBackend(
                [
                    json.dumps(
                        {
                            "checklist": [],
                            "actions": [
                                {
                                    "type": "assert_visible",
                                    "selector": "#go",
                                    "expected": "true",
                                    "checklist_id": "button",
                                }
                            ],
                        }
                    )
                ]
            )
            planner = OneShotActionPlanner(
                backend=backend,
                model_name="mock-planner",
                run_dir=root / "planner",
            )
            checklist = [
                {
                    "id": "button",
                    "description": "The primary action is available.",
                    "expected": "The Go button is visible.",
                    "source": "benchmark",
                }
            ]
            first = planner.plan(
                case_id="planner-case",
                task="Test the primary action.",
                program_path=site,
                benchmark_checklist=checklist,
            )
            second = planner.plan(
                case_id="planner-case",
                task="Test the primary action.",
                program_path=site,
                benchmark_checklist=checklist,
            )
            self.assertEqual(first.to_dict(), second.to_dict())
            self.assertEqual(len(backend.requests), 1)
            self.assertEqual(first.planner, "llm-one-shot-frozen")
            self.assertEqual(first.checklist[0].source, "benchmark")
            self.assertEqual(first.actions[0].checklist_id, "button")
            self.assertEqual(len(backend.requests[0].image_paths), 1)
            self.assertIn("Initial browser state", backend.requests[0].prompt)


class InteractiveJudgeTests(unittest.TestCase):
    def test_executor_structured_failure_is_preserved(self) -> None:
        class StructuredFailureJudge(InteractiveJudge):
            def _node_command(self, _spec_path: Path, result_path: Path):
                payload = json.dumps(
                    {"status": "error", "error": "TimeoutError: viewport capture failed"}
                )
                source = (
                    "import pathlib,sys; "
                    f"pathlib.Path(sys.argv[1]).write_text({payload!r}); "
                    "raise SystemExit(2)"
                )
                return [sys.executable, "-c", source, str(result_path)]

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = root / "site"
            site.mkdir()
            (site / "index.html").write_text("<h1>fixture</h1>", encoding="utf-8")
            plan = ActionPlan.from_dict(
                {
                    "checklist": [{"id": "state", "expected": "state captured"}],
                    "actions": [{"type": "screenshot", "checklist_id": "state"}],
                }
            )
            judge = StructuredFailureJudge(root / "run")
            with self.assertRaisesRegex(RuntimeError, "viewport capture failed"):
                judge.execute(
                    case_id="failure",
                    program_path=site,
                    plan=plan,
                    record_video=False,
                )
            result_path = next((root / "run" / "executions").rglob("result.json"))
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(
                result["executor_result"]["error"],
                "TimeoutError: viewport capture failed",
            )

    def test_public_http_runtime_style_and_console_assertions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = root / "served-site"
            site.mkdir()
            (site / "index.html").write_text(
                "<!doctype html><style>#card{opacity:0.25}</style><div id='card'>ok</div>",
                encoding="utf-8",
            )
            plan = ActionPlan.from_dict(
                {
                    "metadata": {
                        "web_runtime": {
                            "command": [
                                sys.executable,
                                "-m",
                                "http.server",
                                "{port}",
                                "--bind",
                                "127.0.0.1",
                            ],
                            "entry_path": "/",
                            "startup_timeout": 10,
                        }
                    },
                    "checklist": [
                        {"id": "runtime", "expected": "HTTP page is error free"},
                        {"id": "style", "expected": "Card opacity is 0.25"},
                    ],
                    "actions": [
                        {
                            "type": "assert_no_console_errors",
                            "checklist_id": "runtime",
                        },
                        {
                            "type": "assert_style",
                            "selector": "#card",
                            "value": "opacity",
                            "expected": "0.25",
                            "match": "equals",
                            "checklist_id": "style",
                        },
                    ],
                }
            )
            result = InteractiveJudge(root / "run").execute(
                case_id="http-runtime",
                program_path=site,
                plan=plan,
                record_video=False,
            )
            self.assertEqual(result["score"], 1.0)
            self.assertTrue(str(result["initial_state"]["url"]).startswith("http://127.0.0.1:"))
            self.assertTrue(Path(result["service_log"]).is_file())
            self.assertGreaterEqual(len(result["loaded_resources"]), 1)
            self.assertEqual(
                [row["path"] for row in result["runtime_source_context"]["files"]],
                ["index.html"],
            )
            self.assertEqual(
                [row["type"] for row in result["actions"]],
                ["assert_no_console_errors", "assert_style"],
            )

    def test_all_actions_and_deterministic_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = _write_site(root)
            plan = _fixture_plan()
            judge = InteractiveJudge(root / "run")
            first = judge.execute(
                case_id="fixture",
                program_path=site,
                plan=plan,
                record_video=False,
            )
            self.assertEqual(first["initial_state"]["browser_state"]["viewport"]["width"], 1280)
            self.assertIn(
                {"type": "keydown", "target": "window"},
                first["initial_state"]["browser_state"]["keyboard_listeners"],
            )
            second = judge.execute(
                case_id="fixture",
                program_path=site,
                plan=plan,
                record_video=False,
            )
            self.assertEqual(first["status"], "ok")
            self.assertEqual(first["score"], 1.0)
            self.assertEqual(
                {action["type"] for action in first["actions"]},
                {
                    "navigate",
                    "click",
                    "fill",
                    "hover",
                    "press",
                    "scroll",
                    "wait",
                    "screenshot",
                    "assert_text",
                    "assert_visible",
                    "assert_url",
                },
            )
            self.assertEqual(
                [item["status"] for item in first["checklist"]], ["pass", "pass"]
            )
            self.assertEqual(
                [action["status"] for action in first["actions"]],
                [action["status"] for action in second["actions"]],
            )
            self.assertEqual(
                first["actions"][-1]["fingerprint"],
                second["actions"][-1]["fingerprint"],
            )
            self.assertEqual(first["code_version"], hash_program(site))
            self.assertTrue(first["evidence_manifest"])

    def test_fresh_certification_is_version_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = _write_site(root)
            result = InteractiveJudge(root / "run").certify(
                case_id="fixture",
                program_path=site,
                plan=_fixture_plan(),
                record_video=False,
            )
            self.assertTrue(result["fresh_certification"])
            self.assertEqual(result["purpose"], "certification")
            self.assertEqual(result["code_version"], hash_program(site))

    def test_load_time_console_errors_reach_the_first_action(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = _write_site(root)
            index = site / "index.html"
            index.write_text(
                index.read_text(encoding="utf-8").replace(
                    "</body>", "<script>const broken = ;</script></body>"
                ),
                encoding="utf-8",
            )
            plan = ActionPlan.from_dict(
                {
                    "checklist": [{"id": "load", "expected": "page state captured"}],
                    "actions": [
                        {"type": "screenshot", "checklist_id": "load"},
                        {
                            "type": "assert_visible",
                            "selector": "body",
                            "expected": "true",
                            "checklist_id": "load",
                        },
                    ],
                }
            )
            result = InteractiveJudge(root / "run").execute(
                case_id="broken-script",
                program_path=site,
                plan=plan,
                record_video=False,
            )
            console_path = Path(result["actions"][0]["evidence"]["console_delta"])
            console_events = json.loads(console_path.read_text(encoding="utf-8"))
            page_errors = [
                event for event in console_events if event.get("type") == "pageerror"
            ]
            self.assertTrue(page_errors, console_events)
            self.assertIn("stack", page_errors[0])

    def test_canvas_pixels_and_temporal_change_are_executable_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = root / "animated"
            site.mkdir()
            (site / "index.html").write_text(
                """<!doctype html><canvas id='static' width='64' height='64'></canvas>
                <canvas id='c' width='64' height='64'></canvas>
                <script>
                const s=document.querySelector('#static'),sx=s.getContext('2d');sx.fillStyle='rgb(255,0,128)';sx.fillRect(0,0,64,64);
                const c=document.querySelector('#c'),x=c.getContext('2d'); let n=0;
                function draw(){x.fillStyle=`rgb(${n%255},80,180)`;x.fillRect(0,0,64,64);n+=40;requestAnimationFrame(draw)}draw();
                </script>""",
                encoding="utf-8",
            )
            Image.new("RGB", (64, 64), (255, 0, 128)).save(site / "reference.png")
            plan = ActionPlan.from_dict(
                {
                    "checklist": [
                        {"id": "pixels", "expected": "Canvas is drawn"},
                        {"id": "motion", "expected": "Canvas changes over time"},
                        {"id": "signature", "expected": "Canvas matches reference structure"},
                    ],
                    "actions": [
                        {
                            "type": "assert_canvas_drawn",
                            "selector": "#c",
                            "minimum_ratio": 0.9,
                            "checklist_id": "pixels",
                        },
                        {
                            "type": "assert_visual_change",
                            "selector": "#c",
                            "milliseconds": 120,
                            "minimum_ratio": 0.9,
                            "checklist_id": "motion",
                        },
                        {
                            "type": "assert_visual_signature",
                            "selector": "#static",
                            "reference_image": "reference.png",
                            "minimum_ratio": 0.99,
                            "checklist_id": "signature",
                        },
                    ],
                }
            )
            result = InteractiveJudge(root / "run").execute(
                case_id="animated", program_path=site, plan=plan, record_video=False
            )
            self.assertEqual(result["score"], 1)
            self.assertTrue(
                Path(result["actions"][0]["evidence"]["canvas_buffer"]).is_file()
            )
            self.assertTrue(
                Path(result["actions"][1]["evidence"]["visual_after"]).is_file()
            )
            self.assertTrue(
                Path(result["actions"][2]["evidence"]["visual_reference"]).is_file()
            )


class AgentLoopTests(unittest.TestCase):
    def test_historical_certification_ablation_reuses_version_bound_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = _write_site(root)
            broken = (site / "index.html").read_text(encoding="utf-8").replace(
                "'Hello '+", "'Wrong '+"
            )
            (site / "index.html").write_text(broken, encoding="utf-8")
            response = json.dumps(
                {
                    "decision": "patch",
                    "edits": [
                        {
                            "path": "index.html",
                            "old": "'Wrong '+",
                            "new": "'Hello '+",
                        }
                    ],
                }
            )
            loop = AgentLoop(
                backend=_RecordingBackend([response]),
                model_name="mock-reviser",
                run_dir=root / "run",
                policy=ResidualPolicy(),
                budget=ContextBudget(max_text_tokens=1000, max_images=2),
                max_revisions=1,
                record_video=False,
                fresh_certification=False,
            )
            result = loop.run(
                AgentCase(
                    case_id="historical-fixture",
                    task="Repair the greeting.",
                    program_path=str(site),
                    plan=_fixture_plan(),
                )
            )
            self.assertEqual(result["final_score"], 1.0)
            self.assertFalse(result["fresh_certification"])
            self.assertTrue(result["historical_result_reused"])
            self.assertEqual(result["verifier_calls"], 2)

    def test_contract_rejection_retries_without_changing_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = _write_site(root)
            broken = (site / "index.html").read_text(encoding="utf-8").replace(
                "'Hello '+", "'Wrong '+"
            )
            (site / "index.html").write_text(broken, encoding="utf-8")
            rejected = json.dumps(
                {
                    "decision": "patch",
                    "edits": [
                        {
                            "path": "index.html",
                            "old": "'Wrong '+",
                            "new": "'Hello '+",
                            "files": {"accidental.js": "bad"},
                        }
                    ],
                }
            )
            admitted = json.dumps(
                {
                    "decision": "patch",
                    "edits": [
                        {
                            "path": "index.html",
                            "old": "'Wrong '+",
                            "new": "'Hello '+",
                        }
                    ],
                    "files": {},
                    "reason": "Corrected the typed operation after admission feedback.",
                }
            )
            backend = _RecordingBackend([rejected, admitted])
            loop = AgentLoop(
                backend=backend,
                model_name="mock-reviser",
                run_dir=root / "run",
                policy=ResidualPolicy(),
                budget=ContextBudget(max_text_tokens=1000, max_images=2),
                max_revisions=1,
                max_contract_retries=1,
                record_video=False,
            )
            result = loop.run(
                AgentCase(
                    case_id="retry-fixture",
                    task="Repair the greeting.",
                    program_path=str(site),
                    plan=_fixture_plan(),
                )
            )
            self.assertEqual(result["final_score"], 1.0)
            self.assertEqual(result["generator_calls"], 2)
            self.assertEqual(result["contract_rejections"], 1)
            self.assertEqual(len(backend.requests), 2)
            self.assertIn("No code was changed", backend.requests[1].prompt)
            rejection = json.loads(
                next(
                    (root / "run" / "cases" / "retry-fixture" / "rejections").glob(
                        "*.json"
                    )
                ).read_text(encoding="utf-8")
            )
            self.assertTrue(rejection["workspace_unchanged"])

    def test_mock_revision_loop_fixes_residual_and_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = _write_site(root)
            broken = (site / "index.html").read_text(encoding="utf-8").replace(
                "'Hello '+", "'Wrong '+"
            )
            (site / "index.html").write_text(broken, encoding="utf-8")
            fixed = broken.replace("'Wrong '+", "'Hello '+")
            response = json.dumps(
                {
                    "decision": "patch",
                    "edits": [
                        {
                            "path": "index.html",
                            "old": "'Wrong '+",
                            "new": "'Hello '+",
                        }
                    ],
                    "reason": "The first interaction residual reports the wrong greeting.",
                }
            )
            backend = _RecordingBackend([response])
            loop = AgentLoop(
                backend=backend,
                model_name="mock-reviser",
                run_dir=root / "run",
                policy=ResidualPolicy(),
                budget=ContextBudget(
                    max_text_tokens=1000,
                    max_images=2,
                    max_image_pixels=4_000_000,
                ),
                max_revisions=1,
                record_video=False,
            )
            case = AgentCase(
                case_id="broken-fixture",
                task="Make all interactions and navigation satisfy the checklist.",
                program_path=str(site),
                plan=_fixture_plan(),
            )
            result = loop.run(case)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["final_score"], 1.0)
            self.assertTrue(result["fresh_certification"])
            self.assertEqual(result["generator_calls"], 1)
            self.assertEqual(len(backend.requests), 1)
            self.assertEqual(backend.requests[0].seed, 0)
            self.assertIn("Verification residual", backend.requests[0].prompt)
            config = json.loads(
                (root / "run" / "cases" / "broken-fixture" / "config.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(config["seed"], 0)
            request_manifest = json.loads(
                (
                    root
                    / "run"
                    / "cases"
                    / "broken-fixture"
                    / "requests"
                    / "iteration-00.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(request_manifest["seed"], 0)
            self.assertEqual(len(request_manifest["request_fingerprint"]), 64)
            state_path = root / "run" / "cases" / "broken-fixture" / "state.json"
            stale_state = json.loads(state_path.read_text(encoding="utf-8"))
            stale_state["generator_calls"] = 0
            state_path.write_text(json.dumps(stale_state), encoding="utf-8")
            resumed = loop.run(case, resume=True)
            self.assertEqual(resumed["final_version"], result["final_version"])
            self.assertEqual(resumed["generator_calls"], 1)
            self.assertEqual(len(backend.requests), 1)


if __name__ == "__main__":
    unittest.main()
