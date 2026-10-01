# Vision2Web official evaluator

`upstream/` is an unmodified snapshot of commit
`577f9397b3db8fc6d828adde254a830caa65d515`. It contains the released
functional tester, visual scorer, prompts, aggregation, dataset manager, and
Docker sandbox. Run `../verify_official_sources.py vision2web` to verify it.

`run_official.py` only prepends the frozen source to `PYTHONPATH` and forwards
arguments to the released `vision2web evaluate` command. It does not replace
the official GUI Agent, VLM judge prompt, workflow ordering, visual scoring,
or aggregation.

The released evaluator requires a Docker daemon. For ClusterX, use
`run_clusterx.py`: the ClusterX job itself runs the pinned official sandbox
image, while `clusterx_transport/docker` maps the evaluator's Docker lifecycle
onto that outer container. Unknown Docker calls fail closed, every mapped call
is audited, environment values are redacted, and `upstream/` remains unchanged.

This is an execution-transport adaptation, not a scoring port. The released
CLI, workflow ordering, functional prompt, Claude Code + Playwright execution,
visual prompt, VLM scorer, and result format are unchanged. Until paired
Docker-vs-ClusterX validation is completed (or the maintainers accept this
transport), report it as **official Vision2Web evaluator with ClusterX
transport**, rather than silently claiming the unqualified leaderboard setup.

See `../../docs/VISION2WEB_CLUSTERX.md` for the ClusterX job configuration,
single-case command, resume behavior, and equivalence checklist.
