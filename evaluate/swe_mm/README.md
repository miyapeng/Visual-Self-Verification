# SWE-MM official evaluator

SWE-MM uses the official SWE-bench harness and per-instance test specification.
`upstream/` is the complete, unmodified SWE-bench repository snapshot at
commit `7e578260da58400f307e435e43d1d2ab29d686f6` (package 5.0.0rc0), including
its MIT license. Run `../verify_official_sources.py swe_mm` to verify it.

`scripts/swe_mm/direct_case_eval.py` now prepends this snapshot to `sys.path`
and refuses import drift. The `swemm` environment supplies dependencies only;
the scorer source no longer comes from the sibling checkout or site-packages.
The task image still supplies the repository, dependencies, and test runtime,
which are intrinsic parts of the official SWE-bench protocol.
