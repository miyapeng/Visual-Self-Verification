from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from multimodalcode.research.context import ContextBudget
from multimodalcode.research.decision_probes import (
    ProbeCase,
    build_context_records,
    events_before_terminal_submit,
    extract_public_input,
    parse_decision_response,
    render_decision_prompt,
)


def _event(
    sequence: int,
    *,
    source: Path,
    raw_index: int,
    program: str,
    verification: str = "t0",
    status: str | None = None,
    categories: tuple[str, ...] = (),
) -> dict:
    return {
        "benchmark": "interactweb",
        "task": "case",
        "sequence": sequence,
        "source_ref": f"{source}#trajectory/{raw_index}",
        "program_version": {"id": program},
        "verification_version": {"id": verification},
        "observation": (
            {
                "status": status,
                "failure_signal": status == "concrete_failure",
                "content": {"snippet": status or ""},
            }
            if status
            else None
        ),
        "edit": None,
        "executed_action": (
            [{"tool": "test", "categories": list(categories)}] if categories else None
        ),
    }


class DecisionProbeTests(unittest.TestCase):
    def test_submit_event_is_not_visible_to_probe(self) -> None:
        source = Path("/tmp/not-read.json")
        events = [
            _event(0, source=source, raw_index=0, program="v0"),
            _event(1, source=source, raw_index=1, program="v0", categories=("submit",)),
            _event(2, source=source, raw_index=2, program="v0"),
        ]
        self.assertEqual([row["sequence"] for row in events_before_terminal_submit(events)], [0])

    def test_state_edit_reopens_previous_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "history.json"
            source.write_text(
                json.dumps(
                    {
                        "trajectory": [
                            {"role": "assistant", "content": "verify"},
                            {"role": "user", "content": "Status: passed"},
                            {"role": "assistant", "content": "edit current code"},
                            {"role": "user", "content": "Environment Ready"},
                            {"role": "assistant", "content": "finish"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            events = [
                _event(
                    0,
                    source=source,
                    raw_index=0,
                    program="v0",
                    categories=("executable_check",),
                ),
                _event(
                    1,
                    source=source,
                    raw_index=1,
                    program="v0",
                    status="check_pass",
                ),
                _event(2, source=source, raw_index=2, program="v1", categories=("edit",)),
                _event(3, source=source, raw_index=3, program="v1", status="neutral"),
                _event(4, source=source, raw_index=4, program="v1", categories=("submit",)),
            ]
            records = build_context_records(events)
            case = ProbeCase(
                benchmark="interactweb",
                task="case",
                stratum="private-stratum",
                source_trajectory=str(source),
                public_request="Build the public page.",
                public_image_paths=[],
                current_state="v1@t0",
                records=records,
                manual_label={"secret_manual_marker": True},
                detector_label="secret_detector_marker",
                external_outcome="secret_outcome_marker",
            )
            prompt, _, metadata = render_decision_prompt(
                case,
                policy_name="conserved_frontier",
                budget=ContextBudget(max_text_tokens=1024, max_images=1),
            )
            self.assertIn("RECHECK", prompt)
            self.assertEqual(
                metadata["selection"]["metadata"]["obligation_states"],
                {"task-operational": "RECHECK"},
            )
            self.assertNotIn("secret_manual_marker", prompt)
            self.assertNotIn("secret_detector_marker", prompt)
            self.assertNotIn("secret_outcome_marker", prompt)

            rule_prompt, _, rule_metadata = render_decision_prompt(
                case,
                policy_name="summary_with_completion_rule",
                budget=ContextBudget(max_text_tokens=1024, max_images=1),
            )
            self.assertIn("[Completion rule]", rule_prompt)
            self.assertIn("Deterministic trajectory summary", rule_prompt)
            self.assertNotIn("Conserved requirement ledger", rule_prompt)
            self.assertEqual(rule_metadata["policy"], "summary_with_completion_rule")

    def test_only_last_reused_screenshot_is_attached(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "visual_step_0.png"
            # Minimal PNG signature + IHDR dimensions; pixel contents are not read.
            image.write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00\x00\x00\x02\x00\x00\x00\x03"
            )
            source = root / "history.json"
            source.write_text(
                json.dumps(
                    {
                        "trajectory": [
                            {
                                "role": "user",
                                "content": "old failure",
                                "debug_info": {
                                    "internal_test_trace": [{"screenshot_path": str(image)}]
                                },
                            },
                            {"role": "assistant", "content": "edit"},
                            {
                                "role": "user",
                                "content": "new failure",
                                "debug_info": {
                                    "internal_test_trace": [{"screenshot_path": str(image)}]
                                },
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            events = [
                _event(0, source=source, raw_index=0, program="v0", status="concrete_failure"),
                _event(1, source=source, raw_index=1, program="v1", categories=("edit",)),
                _event(2, source=source, raw_index=2, program="v1", status="concrete_failure"),
            ]
            records = build_context_records(events)
            image_records = [record for record in records if record.image_path]
            self.assertEqual([record.record_id for record in image_records], ["event:2"])
            self.assertEqual(image_records[0].image_pixels, 6)

    def test_swe_public_input_materializes_only_public_embedded_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "raw.json"
            normalized = root / "trajectory.json"
            payload = b"public-image-bytes"
            raw.write_text(
                json.dumps(
                    {
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "<pr_description>Consider the following PR description:\nFix the chart.</pr_description>",
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": "data:image/png;base64,"
                                            + base64.b64encode(payload).decode()
                                        },
                                    },
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            normalized.write_text(
                json.dumps({"raw_trajectory": str(raw), "events": []}), encoding="utf-8"
            )
            text, images = extract_public_input(
                normalized, benchmark="swe_mm", image_dir=root / "images"
            )
            self.assertEqual(text.splitlines()[0], "Fix the chart.")
            self.assertIn("[public task image", text)
            self.assertEqual(len(images), 1)
            self.assertEqual(Path(images[0]).read_bytes(), payload)

    def test_parse_decision_response(self) -> None:
        parsed = parse_decision_response(
            '```json\n{"decision":"retry_verification","evidence_ids":["event:2"],"reason":"stale"}\n```'
        )
        self.assertEqual(parsed["decision"], "retry_verification")
        with self.assertRaisesRegex(ValueError, "Invalid decision"):
            parse_decision_response('{"decision":"guess"}')

        recovered = parse_decision_response(
            '{"decision":"submit","evidence_ids":[],"reason":"long explanation'
        )
        self.assertEqual(recovered["decision"], "submit")
        self.assertEqual(recovered["evidence_ids"], [])
        self.assertEqual(
            recovered["parse_recovery"],
            "truncated_after_complete_decision_and_evidence_ids",
        )
        with self.assertRaises(json.JSONDecodeError):
            parse_decision_response('{"decision":"sub')


if __name__ == "__main__":
    unittest.main()
