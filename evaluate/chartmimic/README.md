# ChartMimic official evaluator

`upstream/` is an exact `git archive` snapshot of commit
`92ba5b97b908c607f630c3ffbb5043de9de62b76`. The 18 compatibility edits in
the sibling `Benchmarks/ChartMimic` working tree were deliberately excluded.
Run `../verify_official_sources.py chartmimic` to verify every file.

The official evaluator expects the old dataset names `ori_500` and
`customized_500`, even though the same commit documents the new 600-case
release. `scripts/chartmimic/prepare_official_evaluator.py` creates path aliases
outside `upstream/`; it does not edit evaluator code. The official environment
pins Python 3.9 and Matplotlib 3.8.4. These versions matter because the color
instrumentation calls a private Matplotlib method, so evaluating with the
`mmcode` environment's Matplotlib 3.10 would not be the released protocol.

No scoring formula, instrumentation prefix, threshold, or aggregation rule is
changed. `run_official_case.py` is an I/O adapter around the four released
low-level evaluator classes: text, chart type, layout, and color.
