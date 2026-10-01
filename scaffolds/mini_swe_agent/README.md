# mini-swe-agent source snapshot

This directory contains the Python runtime used by `agent_run.py` for SWE-MM,
Design2Code, and ChartMimic. It is a source snapshot, not a Git submodule and
not a pointer to `/data/miyapeng/mmcode/mini-swe-agent`.

The exact upstream commit, copied scope, and deterministic tree hash are in
`UPSTREAM.json`. `LICENSE.md` is the unmodified upstream MIT license. Runtime
dependencies are intentionally kept in the existing `mmcode` environment and
are pinned by `requirements-agents.txt` at the project root.

Keeping the loop in-tree makes prompt, action, context, and trajectory changes
reviewable in the same research commit as the experiment that uses them.
