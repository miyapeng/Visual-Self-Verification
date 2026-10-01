"""Thin coding-agent adapters used by the benchmark runner.

The upstream harnesses remain external, pinned dependencies.  This package owns
only benchmark adaptation, launch configuration, and trajectory normalization.
"""

from .cases import AgentCase, list_case_ids, load_case, prepare_workspace
from .registry import SCAFFOLDS, ScaffoldSpec

__all__ = [
    "AgentCase",
    "SCAFFOLDS",
    "ScaffoldSpec",
    "list_case_ids",
    "load_case",
    "prepare_workspace",
]
