# OpenHands runtime source snapshot

This is the OpenHands Python runtime that the frozen Vision2Web image actually
executes: CLI 1.16.0 plus its pinned SDK, tools, workspace, and agent-server
namespaces. Package metadata is retained under `metadata/` and exact versions,
image digest, and source hash are recorded in `UPSTREAM.json`.

It intentionally does **not** copy `/data/miyapeng/mmcode/OpenHands`. That
checkout is the Node/React Agent Canvas control plane at the time of this
snapshot; it does not provide Vision2Web's `openhands --headless` Python loop.

`agent_run.py` launches Python 3.12 with this directory's `src/` prepended to
`PYTHONPATH`. Thus edits to the agent loop or its context condenser are owned
by MultimodalCode and take effect in experiments. The pinned ClusterX image
still supplies Python 3.12, Chrome, OS services, and transitive Python wheels;
those binary/system dependencies should not be duplicated in the repository.

The context policies shipped by this version are under
`src/openhands/sdk/context/condenser/`, which is the appropriate in-tree seam
for controlled context-management experiments.
