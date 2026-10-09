# Repository reorganization — 2026-10-08

The reorganization separates project implementation, commands, upstream sources
and local artifacts. See [the layout guide](../docs/REPOSITORY_LAYOUT.md) for the
current structure and path mapping.

## Changes

- Moved 23 root or script entries without deleting files.
- Moved full benchmark checkouts under `evaluate/benchmarks/`, retaining each
  checkout's Git history. Their source inventory is tracked separately from the
  ignored full clones. Pinned evaluator snapshots remain separate.
- Moved research CLI implementations into `src/multimodalcode/research/`; thin
  launchers live under `scripts/research/`. Updated dependent launchers, frozen
  runtime builders and prompt references.
- Grouped local environments, runtime caches and empty application stubs under
  `.local/`. Updated active Conda editable imports and external environment guides.
- Preserved the previous README as `docs/LEGACY_WORKFLOWS.md`; added a concise
  root guide and directory indexes. Moved historical protocol proposals to
  `docs/protocols/`.
- Kept all datasets, runs, generated report sites and historical evidence at their
  existing locations. No scoring definitions or experiment records were changed.

Pre-edit copies include existing uncommitted work. They and the move manifest
are stored locally at `.local/maintenance/reorganization-20261008/`. No commit,
push, model call or new benchmark experiment was made during this cleanup.

## Validation

The current VSV, research-scope and agent-harness tests passed: **330 passed,
8 skipped**. Additional self-verification and prompt/recording tests produced
**34 passed, 4 skipped, 3 subtests passed**, with three previously identified
failures deselected on that follow-up run.

The initial expanded run reported 349 passed, 8 skipped and these three failures:

1. `test_observation_binding_is_content_addressed_and_changes_with_code`:
   a rapid file replacement retained the cached digest. Reproduced using the
   pre-migration source and test backups.
2. `test_first_version_requires_a_model_facing_deployed_app_observation`:
   the expected workspace-change event was absent. Also reproduced using the
   pre-migration backups.
3. `test_capacity_manifest_contains_only_public_probe_inputs`:
   the unchanged pilot manifest points to missing files under the old
   `/data/miyapeng/...` location. Neither the manifest nor task data was moved.

These remain open issues outside the layout change. Passing entry-point checks
does not establish that historical cluster workflows can run on this machine.
Copied local environments already contain stale interpreter links and need their
own rebuild before use.

Seven CLI help checks passed, including the relocated launchers and common VSV
commands. Changed shell scripts passed `bash -n`. Vision2Web and GameDevBench
import successfully from their relocated checkouts using the existing Conda
environments. New guide links and `git diff --check` passed.

## Deletion review

No deletion has been performed. These small targets are ready for explicit
confirmation:

| Target | Contents | Impact of deletion |
| --- | --- | --- |
| `.local/legacy-root/backend/` | One empty npm lockfile, 86 bytes | Removes an unused application stub |
| `.local/legacy-root/frontend/` | One empty npm lockfile, 87 bytes | Removes an unused application stub |
| `.local/cache/root_pycache/` | Two compiled Python cache files | Python can regenerate bytecode |
| `.local/cache/pytest/` | Pytest node IDs and last-run cache | Clears cached test metadata; pytest recreates it |

Together these contained about 216 KiB of file content after verification.
They contain no application implementation or experiment result.

The main storage consumers are `runs/` (about 21 GiB), `data/` (12 GiB), full
benchmark checkouts (12 GiB) and `reports/` (1.8 GiB). These are inventory sizes,
not a deletion recommendation. Old runs can still supply screenshots, version
evidence or Judge responses cited by reports. This change does not classify those
results as unused or remove them.

## Approved follow-up cleanup

The user subsequently approved deletion of the four targets listed above.
Both empty npm stubs and both cache directories have now been removed.
Experiment data, results and migration backups remain intact. Pytest may recreate
its cache during a future test run.
