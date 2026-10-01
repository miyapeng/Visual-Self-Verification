from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "research_goal"
    / "audit_context_window_pressure.py"
)
SPEC = importlib.util.spec_from_file_location("audit_context_window_pressure", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ContextWindowAuditTests(unittest.TestCase):
    def test_last_assistant_prefix_excludes_terminal_feedback(self) -> None:
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "terminal execution feedback"},
        ]
        self.assertEqual(
            MODULE._last_assistant_request_prefix(messages),
            messages[:2],
        )

    def test_hard_context_patterns_are_narrow(self) -> None:
        self.assertIsNotNone(
            MODULE.HARD_CONTEXT_RE.search(
                "Input length (262552) exceeds model's maximum context length (262144)"
            )
        )
        self.assertIsNone(
            MODULE.HARD_CONTEXT_RE.search("the agent discusses context management")
        )


if __name__ == "__main__":
    unittest.main()
