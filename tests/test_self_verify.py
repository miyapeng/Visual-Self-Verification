from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from multimodalcode.backends import ModelBackend
from multimodalcode.research.events import EvidenceStore
from multimodalcode.research.interactive import InteractiveJudge, hash_program
from multimodalcode.research.schema import ActionPlan
from multimodalcode.research.self_verify import (
    InteractionPlan,
    ObligationSet,
    PublicSelfVerifyCase,
    SamePolicySelfVerifyLoop,
    SelfVerifyBudget,
    _separately_bounded_images,
)
from multimodalcode.research.tools import parse_revision
from multimodalcode.schema import GenerationRequest


class RecordingBackend(ModelBackend):
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.requests: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> str:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("Unexpected model call")
        return self.responses.pop(0)


class EvidencePortabilityTests(unittest.TestCase):
    def test_content_addressed_evidence_has_a_portable_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            browser_root = Path(temporary) / "browser"
            store = EvidenceStore(browser_root / "evidence")
            row = store.add_bytes(
                b"portable",
                kind="screenshot",
                case_id="case",
                code_version="version",
                media_type="image/png",
            )
            self.assertEqual(
                row["relative_path"],
                f"evidence/objects/{row['evidence_id']}.png",
            )
            self.assertEqual(
                (browser_root / row["relative_path"]).read_bytes(), b"portable"
            )


def write_broken_site(root: Path) -> Path:
    site = root / "site"
    site.mkdir()
    (site / "index.html").write_text(
        """<!doctype html><html><body>
        <button id="go" onclick="document.querySelector('#result').textContent='Wrong'">Go</button>
        <p id="result">Waiting</p>
        </body></html>""",
        encoding="utf-8",
    )
    return site


class FakeMechanicalJudge:
    """Deterministic unit-test executor; real Playwright tests are opt-in below."""

    def __init__(self, root: Path):
        self.root = root
        self.calls = 0

    def execute(self, *, case_id, program_path, plan, purpose, **_kwargs):
        self.calls += 1
        evidence_root = self.root / f"execution-{self.calls}"
        evidence_root.mkdir(parents=True)
        initial_screenshot = evidence_root / "initial.png"
        initial_dom = evidence_root / "initial.html"
        initial_aria = evidence_root / "initial.aria.txt"
        initial_console = evidence_root / "initial.console.json"
        initial_screenshot.write_bytes(b"unit-test-png")
        initial_dom.write_text("<button id='go'>Go</button><p id='result'>Waiting</p>")
        initial_aria.write_text("button Go\nparagraph Waiting")
        initial_console.write_text("[]")
        action_rows = []
        for index, action in enumerate(plan.actions):
            screenshot = evidence_root / f"{index}.png"
            dom = evidence_root / f"{index}.html"
            aria = evidence_root / f"{index}.aria.txt"
            console = evidence_root / f"{index}.console.json"
            screenshot.write_bytes(b"unit-test-png-" + str(index).encode())
            program_text = "".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in Path(program_path).rglob("*.html")
            )
            observed = "Fixed" if "textContent='Fixed'" in program_text else "Wrong"
            dom.write_text(f"<p id='result'>{observed}</p>")
            aria.write_text(f"paragraph {observed}")
            console.write_text("[]")
            action_rows.append(
                {
                    "index": index,
                    "type": action.type,
                    "checklist_id": action.checklist_id,
                    "status": "pass",
                    "observed": observed,
                    "url": "file:///workspace/index.html",
                    "title": "fixture",
                    "changed": index > 0,
                    "evidence": {
                        "screenshot": str(screenshot),
                        "dom": str(dom),
                        "accessibility": str(aria),
                        "console_delta": str(console),
                    },
                }
            )
        result_path = evidence_root / "result.json"
        result = {
            "status": "ok",
            "execution_id": f"unit-{self.calls}",
            "code_version": hash_program(program_path),
            "purpose": purpose,
            "result_path": str(result_path),
            "initial_state": {
                "url": "file:///workspace/index.html",
                "title": "fixture",
                "fingerprint": "unit",
                "browser_state": {},
                "evidence": {
                    "screenshot": str(initial_screenshot),
                    "dom": str(initial_dom),
                    "accessibility": str(initial_aria),
                    "console_delta": str(initial_console),
                },
            },
            "actions": action_rows,
            "runtime_source_context": {
                "files": [{"path": "index.html"}],
                "fallback": None,
            },
            "score": 0,
        }
        result_path.write_text(json.dumps(result), encoding="utf-8")
        return result


class CandidateReplayFailureJudge(FakeMechanicalJudge):
    def execute(self, **kwargs):
        if self.calls == 2:
            self.calls += 1
            raise RuntimeError("candidate replay failed")
        return super().execute(**kwargs)


class SelfVerifySchemaTests(unittest.TestCase):
    def test_runtime_evidence_and_public_reference_images_have_separate_caps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = [root / name for name in ("e1", "e2", "e3", "r1", "r2")]
            for path in paths:
                path.write_bytes(path.name.encode())
            e1, e2, e3, r1, r2 = map(str, paths)
            self.assertEqual(
                _separately_bounded_images(
                    [e1, e2, e3],
                    [e2, r1, r2],
                    2,
                ),
                [e1, e2, r1, r2],
            )

    def test_revision_contract_enforces_local_patch_budgets(self) -> None:
        too_many = json.dumps(
            {
                "decision": "patch",
                "edits": [
                    {"path": "index.html", "old": str(index), "new": f"x{index}"}
                    for index in range(3)
                ],
                "files": {},
            }
        )
        with self.assertRaisesRegex(ValueError, "edit budget 2"):
            parse_revision(too_many, max_edits=2, max_patch_chars=100)

        too_large = json.dumps(
            {
                "decision": "patch",
                "edits": [
                    {"path": "index.html", "old": "a" * 6, "new": "b" * 6}
                ],
                "files": {},
            }
        )
        with self.assertRaisesRegex(ValueError, "character budget 10"):
            parse_revision(too_large, max_edits=2, max_patch_chars=10)

    def test_plan_allows_only_mechanical_actions_and_requires_reset_boundaries(self) -> None:
        obligations = ObligationSet.from_dict(
            {
                "obligations": [
                    {"obligation_id": "O1", "obligation": "First behavior works."},
                    {"obligation_id": "O2", "obligation": "Second behavior works."},
                ]
            },
            max_checks=4,
        )
        budget = SelfVerifyBudget(max_checks=4)
        plan = InteractionPlan.from_dict(
            {
                "checks": [
                    {
                        "check_id": "C1",
                        "obligation": "First behavior works.",
                        "actions": [{"type": "click", "selector": "#one"}],
                        "expected_observation": "One appears.",
                    },
                    {
                        "check_id": "C2",
                        "obligation": "Second behavior works.",
                        "actions": [
                            {"type": "reset"},
                            {"type": "click", "selector": "#two"},
                        ],
                        "expected_observation": "Two appears.",
                    },
                ]
            },
            obligations=obligations,
            budget=budget,
        )
        self.assertEqual(plan.action_count, 3)
        missing_obligation = plan.to_dict()
        missing_obligation["checks"] = missing_obligation["checks"][:1]
        with self.assertRaisesRegex(ValueError, "cover every frozen obligation"):
            InteractionPlan.from_dict(
                missing_obligation,
                obligations=obligations,
                budget=budget,
            )
        missing_reset = plan.to_dict()
        missing_reset["checks"][1]["actions"] = [
            {"type": "click", "selector": "#two"}
        ]
        normalized = InteractionPlan.from_dict(
            missing_reset, obligations=obligations, budget=budget
        )
        self.assertEqual(
            [action.type for action in normalized.checks[1].actions],
            ["reset", "click"],
        )
        with self.assertRaisesRegex(ValueError, "exceeds its action budget"):
            InteractionPlan.from_dict(
                missing_reset,
                obligations=obligations,
                budget=SelfVerifyBudget(
                    max_checks=4,
                    max_actions_per_check=1,
                ),
            )
        unknown_action_fields = plan.to_dict()
        unknown_action_fields["checks"][0]["actions"] = [
            {"type": "scroll", "direction": "down", "amount": 500}
        ]
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            InteractionPlan.from_dict(
                unknown_action_fields,
                obligations=obligations,
                budget=budget,
            )
        excessive_timeout = plan.to_dict()
        excessive_timeout["checks"][0]["actions"] = [
            {"type": "click", "selector": "#one", "timeout_ms": 30_001}
        ]
        with self.assertRaisesRegex(ValueError, "timeout_ms"):
            InteractionPlan.from_dict(
                excessive_timeout,
                obligations=obligations,
                budget=budget,
            )
        invalid_assertion = plan.to_dict()
        invalid_assertion["checks"][0]["actions"] = [
            {"type": "assert_text", "selector": "body", "expected": "one"}
        ]
        with self.assertRaisesRegex(ValueError, "mechanical actions"):
            InteractionPlan.from_dict(
                invalid_assertion, obligations=obligations, budget=budget
            )
        missing_fill_locator = plan.to_dict()
        missing_fill_locator["checks"][0]["actions"] = [
            {"type": "fill", "value": "AAPL"}
        ]
        with self.assertRaisesRegex(ValueError, "requires exactly one locator"):
            InteractionPlan.from_dict(
                missing_fill_locator, obligations=obligations, budget=budget
            )
        ambiguous_click = plan.to_dict()
        ambiguous_click["checks"][0]["actions"] = [
            {"type": "click", "selector": "#one", "text": "One"}
        ]
        with self.assertRaisesRegex(ValueError, "requires exactly one locator"):
            InteractionPlan.from_dict(
                ambiguous_click, obligations=obligations, budget=budget
            )
        role_only_click = plan.to_dict()
        role_only_click["checks"][0]["actions"] = [
            {"type": "click", "role": "checkbox"}
        ]
        role_only = InteractionPlan.from_dict(
            role_only_click,
            obligations=obligations,
            budget=budget,
        )
        self.assertEqual(role_only.checks[0].actions[0].role, "checkbox")
        name_without_role = plan.to_dict()
        name_without_role["checks"][0]["actions"] = [
            {"type": "click", "name": "Submit"}
        ]
        with self.assertRaisesRegex(ValueError, "name requires a role"):
            InteractionPlan.from_dict(
                name_without_role,
                obligations=obligations,
                budget=budget,
            )
        with self.assertRaisesRegex(ValueError, "total action budget"):
            InteractionPlan.from_dict(
                plan.to_dict(),
                obligations=obligations,
                budget=SelfVerifyBudget(
                    max_browser_actions=5,
                    max_revisions=1,
                    max_checks=4,
                    max_actions_per_check=3,
                ),
            )
        with self.assertRaisesRegex(ValueError, "every allowed candidate replay"):
            InteractionPlan.from_dict(
                plan.to_dict(),
                obligations=obligations,
                budget=SelfVerifyBudget(
                    max_browser_actions=9,
                    max_revisions=2,
                    max_checks=4,
                    max_actions_per_check=3,
                ),
            )

    def test_public_case_rejects_evaluator_and_hidden_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = write_broken_site(root)
            for forbidden in ("official_evaluator", "hidden_tests", "rubric"):
                with self.subTest(forbidden=forbidden):
                    with self.assertRaisesRegex(ValueError, "forbidden"):
                        PublicSelfVerifyCase.from_dict(
                            {
                                "case_id": "x",
                                "benchmark": "fixture",
                                "task": "Public task",
                                "program_path": str(site),
                                forbidden: {"private": True},
                            },
                            base_dir=root,
                        )
            with self.assertRaisesRegex(ValueError, "forbidden"):
                PublicSelfVerifyCase.from_dict(
                    {
                        "case_id": "nested",
                        "benchmark": "fixture",
                        "task": "Public task",
                        "program_path": str(site),
                        "metadata": [{"rubric": "private"}],
                    },
                    base_dir=root,
                )

    def test_obligation_texts_must_be_distinct(self) -> None:
        with self.assertRaisesRegex(ValueError, "texts must be unique"):
            ObligationSet.from_dict(
                {
                    "obligations": [
                        {"obligation_id": "O1", "obligation": "Same behavior."},
                        {"obligation_id": "O2", "obligation": "Same behavior."},
                    ]
                },
                max_checks=2,
            )

    def test_plan_supports_mechanical_select(self) -> None:
        obligations = ObligationSet.from_dict(
            {
                "obligations": [
                    {"obligation_id": "O1", "obligation": "A format can be selected."}
                ]
            },
            max_checks=1,
        )
        plan = InteractionPlan.from_dict(
            {
                "checks": [
                    {
                        "check_id": "C1",
                        "obligation": "A format can be selected.",
                        "actions": [
                            {"type": "select", "selector": "#format", "value": "pdf"},
                            {"type": "screenshot"},
                        ],
                        "expected_observation": "PDF is selected.",
                    }
                ]
            },
            obligations=obligations,
            budget=SelfVerifyBudget(max_checks=1),
        )
        self.assertEqual(plan.checks[0].actions[0].type, "select")


@unittest.skipUnless(
    os.environ.get("MMCODE_RUN_BROWSER_TESTS") == "1",
    "set MMCODE_RUN_BROWSER_TESTS=1 in a Playwright-capable container",
)
class MechanicalBrowserTests(unittest.TestCase):
    def test_navigation_action_persists_post_navigation_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = root / "site"
            site.mkdir()
            (site / "index.html").write_text(
                "<a id='next' href='next.html'>Next</a>", encoding="utf-8"
            )
            (site / "next.html").write_text(
                "<h1>Arrived</h1>", encoding="utf-8"
            )
            plan = ActionPlan.from_dict(
                {
                    "checklist": [{"id": "navigation", "expected": "Arrived"}],
                    "actions": [
                        {
                            "type": "click",
                            "selector": "#next",
                            "checklist_id": "navigation",
                        },
                        {"type": "screenshot", "checklist_id": "navigation"},
                    ],
                }
            )

            result = InteractiveJudge(root / "run").execute(
                case_id="navigation",
                program_path=site,
                plan=plan,
                record_video=False,
            )

            self.assertEqual(result["status"], "ok")
            self.assertTrue(result["actions"][0]["url"].endswith("next.html"))
            self.assertIn(
                "Arrived",
                Path(result["actions"][0]["evidence"]["dom"]).read_text(),
            )

    def test_external_resources_are_blocked_and_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = root / "site"
            site.mkdir()
            (site / "index.html").write_text(
                """<!doctype html><html><body><h1>Local</h1>
                <img src='https://example.invalid/never.png'>
                </body></html>""",
                encoding="utf-8",
            )
            plan = ActionPlan.from_dict(
                {
                    "checklist": [{"id": "state", "expected": "local page captured"}],
                    "actions": [{"type": "screenshot", "checklist_id": "state"}],
                }
            )
            result = InteractiveJudge(root / "run").execute(
                case_id="offline",
                program_path=site,
                plan=plan,
                timeout_ms=5_000,
                record_video=False,
            )
            console = json.loads(
                Path(result["actions"][0]["evidence"]["console_delta"]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(result["status"], "ok")
            self.assertTrue((root / "run" / result["result_relative_path"]).is_file())
            self.assertTrue(result["evidence_manifest"])
            self.assertTrue(
                all(
                    (root / "run" / row["relative_path"]).is_file()
                    for row in result["evidence_manifest"]
                )
            )
            self.assertTrue(
                any(event.get("type") == "requestfailed" for event in console),
                console,
            )

    def test_reset_clears_storage_between_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = root / "site"
            site.mkdir()
            (site / "index.html").write_text(
                """<!doctype html><html><body>
                <button id='set' onclick="localStorage.setItem('saved','yes');document.querySelector('#state').textContent='yes'">Set</button>
                <p id='state'></p>
                <script>document.querySelector('#state').textContent=localStorage.getItem('saved')||'empty'</script>
                </body></html>""",
                encoding="utf-8",
            )
            plan = ActionPlan.from_dict(
                {
                    "checklist": [
                        {"id": "first", "expected": "state is set"},
                        {"id": "isolated", "expected": "state is empty after reset"},
                    ],
                    "actions": [
                        {"type": "click", "selector": "#set", "checklist_id": "first"},
                        {
                            "type": "assert_text",
                            "selector": "#state",
                            "expected": "yes",
                            "match": "equals",
                            "checklist_id": "first",
                        },
                        {"type": "reset", "checklist_id": "isolated"},
                        {
                            "type": "assert_text",
                            "selector": "#state",
                            "expected": "empty",
                            "match": "equals",
                            "checklist_id": "isolated",
                        },
                    ],
                }
            )
            result = InteractiveJudge(root / "run").execute(
                case_id="reset", program_path=site, plan=plan, record_video=False
            )
            self.assertEqual(result["score"], 1.0)
            self.assertEqual(result["actions"][2]["type"], "reset")
            self.assertIn("state cleared", result["actions"][2]["observed"])

    def test_browser_action_failure_is_recorded_and_execution_continues(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = write_broken_site(root)
            plan = ActionPlan.from_dict(
                {
                    "checklist": [{"id": "broken", "expected": "evidence exists"}],
                    "actions": [
                        {
                            "type": "click",
                            "selector": "#does-not-exist",
                            "timeout_ms": 100,
                            "checklist_id": "broken",
                        },
                        {"type": "screenshot", "checklist_id": "broken"},
                    ],
                }
            )
            result = InteractiveJudge(root / "run").execute(
                case_id="failure", program_path=site, plan=plan, record_video=False
            )
            self.assertEqual(result["actions"][0]["status"], "fail")
            self.assertIn("TimeoutError", result["actions"][0]["error"])
            self.assertEqual(result["actions"][1]["status"], "pass")


class SamePolicyLoopTests(unittest.TestCase):
    def test_duplicate_invalid_action_plan_stops_after_second_response(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = write_broken_site(root)
            invalid_plan = (
                '{"checks":[{"check_id":"C1","obligation":"Click Go.",'
                '"actions":[{"type":"semantic_assertion"}],'
                '"expected_observation":"Fixed appears."}]}'
            )
            backend = RecordingBackend(
                [
                    '{"obligations":[{"obligation_id":"O1","obligation":"Click Go."}]}',
                    invalid_plan,
                    invalid_plan,
                ]
            )
            loop = SamePolicySelfVerifyLoop(
                backend=backend,
                model_name="same-policy-fixture",
                run_dir=root / "run",
                budget=SelfVerifyBudget(
                    max_model_calls=3,
                    max_browser_actions=5,
                    max_revisions=1,
                    max_schema_retries=1,
                ),
                record_video=False,
                interactive_judge=FakeMechanicalJudge(root / "mechanical"),
            )

            with self.assertRaisesRegex(ValueError, "repeated the same invalid response"):
                loop.run(
                    PublicSelfVerifyCase(
                        case_id="duplicate-plan",
                        benchmark="fixture",
                        task="Click Go.",
                        program_path=str(site),
                    )
                )

            result = json.loads(
                (root / "run" / "cases" / "duplicate-plan" / "result.json").read_text()
            )
            self.assertEqual(result["status"], "error")
            self.assertIn("repeated the same invalid response", result["termination_reason"])
            self.assertEqual(result["cost"]["model_calls"], 3)

    def test_duplicate_primary_repair_response_stops_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = write_broken_site(root)
            repeated_patch = (
                '{"decision":"patch","edits":[{"path":"index.html",'
                '"old":"textContent=\'Wrong\'","new":"textContent=\'StillWrong\'"}],'
                '"files":{},"reason":"First attempt."}'
            )
            backend = RecordingBackend(
                [
                    '{"obligations":[{"obligation_id":"O1","obligation":"Clicking Go must display Fixed."}]}',
                    '{"checks":[{"check_id":"C1","obligation":"Clicking Go must display Fixed.","actions":[{"type":"click","selector":"#go"},{"type":"screenshot"}],"expected_observation":"The result displays Fixed."}]}',
                    '{"checks":[{"check_id":"C1","status":"fail","first_failure_step":1,"evidence_refs":["C1:step:1:screenshot"]}]}',
                    repeated_patch,
                    '{"checks":[{"check_id":"C1","status":"fail","first_failure_step":0,"evidence_refs":["C1:step:0:screenshot"]}]}',
                    repeated_patch,
                ]
            )
            initial_version = hash_program(site)
            loop = SamePolicySelfVerifyLoop(
                backend=backend,
                model_name="same-policy-fixture",
                run_dir=root / "run",
                budget=SelfVerifyBudget(
                    max_model_calls=7,
                    max_browser_actions=7,
                    max_revisions=2,
                    max_schema_retries=1,
                    max_checks=2,
                    max_actions_per_check=3,
                ),
                record_video=False,
                interactive_judge=FakeMechanicalJudge(root / "mechanical"),
            )

            result = loop.run(
                PublicSelfVerifyCase(
                    case_id="duplicate-repair-response",
                    benchmark="fixture",
                    task="Clicking Go must display Fixed.",
                    program_path=str(site),
                )
            )

            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["termination_reason"], "duplicate_repair_response")
            self.assertEqual(result["final_program_version"], initial_version)
            self.assertEqual(len(result["patches"]), 2)
            self.assertEqual(
                result["patches"][1]["rejection"],
                "duplicate_repair_response_twice_consecutively",
            )
            self.assertEqual(result["cost"]["model_calls"], 6)

    def test_candidate_replay_error_restores_last_accepted_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = write_broken_site(root)
            initial_version = hash_program(site)
            backend = RecordingBackend(
                [
                    '{"obligations":[{"obligation_id":"O1","obligation":"Clicking Go must display Fixed."}]}',
                    '{"checks":[{"check_id":"C1","obligation":"Clicking Go must display Fixed.","actions":[{"type":"click","selector":"#go"},{"type":"screenshot"}],"expected_observation":"The result displays Fixed."}]}',
                    '{"checks":[{"check_id":"C1","status":"fail","first_failure_step":1,"evidence_refs":["C1:step:1:screenshot"]}]}',
                    '{"decision":"patch","edits":[{"path":"index.html","old":"textContent=\'Wrong\'","new":"textContent=\'Fixed\'"}],"files":{},"reason":"Fix result."}',
                ]
            )
            loop = SamePolicySelfVerifyLoop(
                backend=backend,
                model_name="same-policy-fixture",
                run_dir=root / "run",
                budget=SelfVerifyBudget(
                    max_model_calls=4,
                    max_browser_actions=5,
                    max_revisions=1,
                    max_schema_retries=0,
                    max_checks=2,
                    max_actions_per_check=3,
                ),
                record_video=False,
                interactive_judge=CandidateReplayFailureJudge(root / "mechanical"),
            )
            with self.assertRaisesRegex(RuntimeError, "candidate replay failed"):
                loop.run(
                    PublicSelfVerifyCase(
                        case_id="replay-error",
                        benchmark="fixture",
                        task="Clicking Go must display Fixed.",
                        program_path=str(site),
                    )
                )
            result = json.loads(
                (root / "run" / "cases" / "replay-error" / "result.json").read_text()
            )
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["accepted_version"], initial_version)
            self.assertEqual(result["final_program_version"], initial_version)

    def test_failure_repair_replay_uses_one_frozen_model_and_accepts_improvement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = write_broken_site(root)
            initial_version = hash_program(site)
            backend = RecordingBackend(
                [
                    json.dumps(
                        {
                            "obligations": [
                                {
                                    "obligation_id": "O1",
                                    "obligation": "Clicking Go must display Fixed.",
                                }
                            ]
                        }
                    ),
                    json.dumps(
                        {
                            "checks": [
                                {
                                    "check_id": "C1",
                                    "obligation": "Clicking Go must display Fixed.",
                                    "actions": [
                                        {"type": "click", "selector": "#go"},
                                        {"type": "screenshot"},
                                    ],
                                    "expected_observation": "The result displays Fixed.",
                                }
                            ]
                        }
                    ),
                    json.dumps(
                        {
                            "checks": [
                                {
                                    "check_id": "C1",
                                    "status": "fail",
                                    "first_failure_step": 1,
                                    "evidence_refs": ["C1:step:1:screenshot"],
                                }
                            ]
                        }
                    ),
                    json.dumps(
                        {
                            "decision": "patch",
                            "edits": [
                                {
                                    "path": "index.html",
                                    "old": "textContent='Wrong'",
                                    "new": "textContent='Fixed'",
                                }
                            ],
                            "files": {},
                            "reason": "Use the required result text.",
                        }
                    ),
                    json.dumps(
                        {
                            "checks": [
                                {
                                    "check_id": "C1",
                                    "status": "pass",
                                    "first_failure_step": None,
                                    "evidence_refs": ["C1:step:1:screenshot"],
                                }
                            ]
                        }
                    ),
                ]
            )
            budget = SelfVerifyBudget(
                max_model_calls=5,
                max_browser_actions=5,
                max_revisions=1,
                max_schema_retries=0,
                max_checks=2,
                max_actions_per_check=3,
                max_evidence_images=3,
            )
            loop = SamePolicySelfVerifyLoop(
                backend=backend,
                model_name="same-policy-fixture",
                run_dir=root / "run",
                budget=budget,
                max_tokens=1024,
                record_video=False,
                interactive_judge=FakeMechanicalJudge(root / "mechanical"),
            )
            result = loop.run(
                PublicSelfVerifyCase(
                    case_id="repair",
                    benchmark="fixture",
                    task="Clicking Go must display Fixed.",
                    program_path=str(site),
                )
            )
            self.assertEqual(result["termination_reason"], "all_checks_passed_after_repair")
            self.assertNotEqual(result["accepted_version"], initial_version)
            self.assertEqual(result["accepted_version"], result["final_program_version"])
            self.assertTrue(result["patches"][0]["acceptance"]["accepted"])
            self.assertTrue(result["replays"][0]["executor_semantic_score_ignored"])
            self.assertFalse(result["official_evaluator_visible"])
            self.assertEqual(len(backend.requests), 5)
            config = json.loads(
                (root / "run" / "cases" / "repair" / "config.json").read_text()
            )
            self.assertEqual(config["same_policy"]["model"], "same-policy-fixture")
            model_rows = [
                json.loads(line)
                for line in (root / "run" / "model_calls.jsonl").read_text().splitlines()
            ]
            self.assertEqual({row["model"] for row in model_rows}, {"same-policy-fixture"})
            self.assertEqual(
                [row["stage"] for row in model_rows],
                ["obligations", "action-plan", "evidence-judgment", "repair", "evidence-judgment"],
            )
            self.assertIn("at most 2 checks", backend.requests[1].prompt)
            self.assertIn("at most 3 actions", backend.requests[1].prompt)
            self.assertIn("at most 2 total planned actions", backend.requests[1].prompt)
            self.assertIn("at most 4 edits", backend.requests[3].prompt)
            self.assertIn("at most 24000 total characters", backend.requests[3].prompt)

    def test_no_op_repair_uses_bounded_contract_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = write_broken_site(root)
            backend = RecordingBackend(
                [
                    '{"obligations":[{"obligation_id":"O1","obligation":"Clicking Go must display Fixed."}]}',
                    '{"checks":[{"check_id":"C1","obligation":"Clicking Go must display Fixed.","actions":[{"type":"click","selector":"#go"},{"type":"screenshot"}],"expected_observation":"The result displays Fixed."}]}',
                    '{"checks":[{"check_id":"C1","status":"fail","first_failure_step":1,"evidence_refs":["C1:step:1:screenshot"]}]}',
                    '{"decision":"patch","edits":[{"path":"index.html","old":"textContent=\'Wrong\'","new":"textContent=\'Wrong\'"}],"files":{},"reason":"No effective change."}',
                    '{"decision":"patch","edits":[{"path":"index.html","old":"textContent=\'Wrong\'","new":"textContent=\'Fixed\'"}],"files":{},"reason":"Use the required result text."}',
                    '{"checks":[{"check_id":"C1","status":"pass","first_failure_step":null,"evidence_refs":["C1:step:1:screenshot"]}]}',
                ]
            )
            loop = SamePolicySelfVerifyLoop(
                backend=backend,
                model_name="same-policy-fixture",
                run_dir=root / "run",
                budget=SelfVerifyBudget(
                    max_model_calls=6,
                    max_browser_actions=5,
                    max_revisions=1,
                    max_schema_retries=1,
                    max_checks=2,
                    max_actions_per_check=3,
                    max_evidence_images=3,
                ),
                max_tokens=1024,
                record_video=False,
                interactive_judge=FakeMechanicalJudge(root / "mechanical"),
            )
            result = loop.run(
                PublicSelfVerifyCase(
                    case_id="no-op-retry",
                    benchmark="fixture",
                    task="Clicking Go must display Fixed.",
                    program_path=str(site),
                )
            )
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["termination_reason"], "all_checks_passed_after_repair")
            model_rows = [
                json.loads(line)
                for line in (root / "run" / "model_calls.jsonl").read_text().splitlines()
            ]
            self.assertEqual(
                [row["stage"] for row in model_rows],
                [
                    "obligations",
                    "action-plan",
                    "evidence-judgment",
                    "repair",
                    "repair-contract-retry-1",
                    "evidence-judgment",
                ],
            )
            self.assertEqual(
                {request.system_prompt for request in backend.requests[3:5]},
                {
                    "You are the same multimodal coding policy. Produce the smallest typed code repair grounded in cited browser evidence. Return JSON only."
                },
            )

    def test_inapplicable_exact_edit_uses_bounded_contract_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = write_broken_site(root)
            backend = RecordingBackend(
                [
                    '{"obligations":[{"obligation_id":"O1","obligation":"Clicking Go must display Fixed."}]}',
                    '{"checks":[{"check_id":"C1","obligation":"Clicking Go must display Fixed.","actions":[{"type":"click","selector":"#go"},{"type":"screenshot"}],"expected_observation":"The result displays Fixed."}]}',
                    '{"checks":[{"check_id":"C1","status":"fail","first_failure_step":1,"evidence_refs":["C1:step:1:screenshot"]}]}',
                    '{"decision":"patch","edits":[{"path":"index.html","old":"textContent=\'Missing\'","new":"textContent=\'Fixed\'"}],"files":{},"reason":"First localization was wrong."}',
                    '{"decision":"patch","edits":[{"path":"index.html","old":"textContent=\'Wrong\'","new":"textContent=\'Fixed\'"}],"files":{},"reason":"Use the exact current source."}',
                    '{"checks":[{"check_id":"C1","status":"pass","first_failure_step":null,"evidence_refs":["C1:step:1:screenshot"]}]}',
                ]
            )
            loop = SamePolicySelfVerifyLoop(
                backend=backend,
                model_name="same-policy-fixture",
                run_dir=root / "run",
                budget=SelfVerifyBudget(
                    max_model_calls=6,
                    max_browser_actions=5,
                    max_revisions=1,
                    max_schema_retries=1,
                    max_checks=2,
                    max_actions_per_check=3,
                    max_evidence_images=3,
                ),
                max_tokens=1024,
                record_video=False,
                interactive_judge=FakeMechanicalJudge(root / "mechanical"),
            )
            result = loop.run(
                PublicSelfVerifyCase(
                    case_id="inapplicable-edit-retry",
                    benchmark="fixture",
                    task="Clicking Go must display Fixed.",
                    program_path=str(site),
                )
            )
            self.assertEqual(result["status"], "complete")
            self.assertEqual(
                result["termination_reason"], "all_checks_passed_after_repair"
            )
            model_rows = [
                json.loads(line)
                for line in (root / "run" / "model_calls.jsonl").read_text().splitlines()
            ]
            self.assertEqual(
                [row["stage"] for row in model_rows],
                [
                    "obligations",
                    "action-plan",
                    "evidence-judgment",
                    "repair",
                    "repair-contract-retry-1",
                    "evidence-judgment",
                ],
            )
            retry_request = backend.requests[4]
            self.assertIn("Exact edit text was not found", retry_request.prompt)

    def test_model_call_budget_terminates_without_unbounded_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = write_broken_site(root)
            backend = RecordingBackend(
                [
                    '{"obligations":[{"obligation_id":"O1","obligation":"Click Go."}]}',
                    '{"checks":[{"check_id":"C1","obligation":"Click Go.","actions":[{"type":"click","selector":"#go"}],"expected_observation":"Fixed appears."}]}',
                    '{"checks":[{"check_id":"C1","status":"fail","first_failure_step":0,"evidence_refs":["C1:step:0:screenshot"]}]}',
                ]
            )
            loop = SamePolicySelfVerifyLoop(
                backend=backend,
                model_name="budgeted",
                run_dir=root / "run",
                budget=SelfVerifyBudget(
                    max_model_calls=3,
                    max_browser_actions=4,
                    max_revisions=2,
                    max_schema_retries=0,
                ),
                record_video=False,
                interactive_judge=FakeMechanicalJudge(root / "mechanical"),
            )
            result = loop.run(
                PublicSelfVerifyCase(
                    case_id="budget",
                    benchmark="fixture",
                    task="Click Go.",
                    program_path=str(site),
                )
            )
            self.assertEqual(result["termination_reason"], "model_call_budget_exhausted")
            self.assertEqual(result["cost"]["model_calls"], 3)
            self.assertEqual(len(backend.requests), 3)

    def test_runner_is_disabled_by_default_and_does_not_create_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = write_broken_site(root)
            manifest = root / "cases.json"
            manifest.write_text(
                json.dumps(
                    [
                        {
                            "case_id": "disabled",
                            "benchmark": "fixture",
                            "task": "Do nothing.",
                            "program_path": str(site),
                        }
                    ]
                ),
                encoding="utf-8",
            )
            output = root / "must-not-exist"
            project = Path(__file__).resolve().parents[1]
            process = subprocess.run(
                [
                    sys.executable,
                    str(project / "self_verify_run.py"),
                    "--cases",
                    str(manifest),
                    "--output-root",
                    str(output),
                    "--backend",
                    "mock",
                    "--model",
                    "mock",
                ],
                cwd=project,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertIn('"status": "disabled"', process.stdout)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
