# Frozen InteractWeb repair-interface diagnostic

Frozen before any model inference on 2026-08-21 UTC.

## Question

The execution-rooted one-shot exact-edit policy and the stronger 35B capacity
probe both repaired 0/2 public InteractWeb failures. This diagnostic asks
whether the restrictive one-shot patch contract, rather than source selection,
is the immediate repair bottleneck.

## Fixed inputs and permissions

- Cases: `interactweb-000045-navigation-repair` and
  `interactweb-000062-question-bank-repair`.
- Generator: `/data/miyapeng/model/Qwen3.5-9B`, temperature 0, native
  mini-swe-agent v2 Bash tool calls, at most 8 calls, 4096 output tokens/call,
  900 seconds/case.
- Input: the same public task, single guarded-frontier failure certificate,
  single failure screenshot, and frozen public interaction plan used by the
  execution-rooted condition. No official/hidden evaluator output is exposed.
- Agent actions: ordinary repository inspect/search/edit commands and replay of
  the deterministic public check. The task source and dependencies are copied
  to node-local `/tmp`; the agent process runs as uid 65534 and cannot modify
  `/data` benchmark or harness files. Network proxies point to a closed local
  port. Checker/source hashes are recorded before and after.
- Final outcome: a fresh replay of the same public plan after the agent exits,
  regardless of its self-reported completion. This is not an official
  InteractWeb-Bench score.

## Single changed variable

Relative to the Qwen3.5-9B execution-rooted one-shot condition, replace the
exact JSON edit contract with an eight-step native Bash inspect/edit/recheck
loop. This grants more actions and potentially more tokens, so it is an
extended-budget repair-interface diagnostic, not a fair method win.

## Decision rule

- If at least one of two cases reaches fresh public-check score 1.0 with an
  unchanged checker, the one-shot contract is supported as a competing repair
  bottleneck. Do not attribute the gain to execution-rooted context.
- If neither case improves, ordinary tool access within this budget is not a
  sufficient explanation; move to the next failure-driven branch without
  adding another context module.
- Checker mutation, missing fresh certification, or an infrastructure failure
  invalidates that case. No outcome-triggered retries are allowed.

## Dry-run validation

On `000062`, an isolated no-model run reproduced 0.0 before and after, preserved
the same code version, produced no changed files, and reported identical checker
snapshots. The Vite dependency cache was copied to `/tmp`; the original case and
project remained read-only.
