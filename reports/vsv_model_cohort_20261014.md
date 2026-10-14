# Model cohort and trajectory availability

Audit date: 2026-10-14. This report proposes a cohort for Vision2Web and
SWE-bench Multimodal. It records data availability, not new agent scores.
No paid model calls, inference jobs, or training jobs were launched.

## Recommendation

Use **10 models for the main Vision2Web study**: seven archived systems and
three Qwen3.5 sizes. Use **eight models for SWE-MM**: five public OpenHands
systems and the same three Qwen sizes. This gives 13 distinct model identities
across the study, with five intended shared models, subject to verifying the
Opus routing alias. A single ten-model intersection is not available from the
inspected sources.

| Vision2Web model | Local source | SWE-MM plan |
| --- | --- | --- |
| Claude Fable 5 | Claude Code; 193 structured conversations | Reuse OpenHands archive; 68 indexed tasks |
| Claude Opus 4.8 | Claude Code; 193 structured conversations; recorded ID `claude-opus-4-8-cc` | Reuse OpenHands `claude-opus-4-8`; 68 indexed tasks; confirm alias before pooling identities |
| GPT-5.6-Sol | Codex; 193 terminal transcripts; recorded ID `gpt-5.6-sol-vibe` | No matching public SWE-MM run found; use GPT-5.5 as a separate SWE-MM baseline |
| Gemini 3.7 Flash | Gemini CLI; 193 structured conversations | No matching public SWE-MM run found; use Gemini 3.5 Flash separately |
| Kimi K3 | Claude Code; 193 structured conversations | No matching public SWE-MM run found; use Kimi K2.6 separately |
| GLM-5.3-Flash | Claude Code; 193 structured conversations | No matching visual SWE-MM run found |
| DeepSeek-V4-Flash-Vision-Exp | Claude Code; 193 structured conversations | No matching visual SWE-MM run found |
| Qwen3.5-4B | Collect with the frozen harness and task cohort | Collect on the same SWE-MM cohort |
| Qwen3.5-9B | Audit the existing 86 local tasks; collect missing or incompatible runs | Existing mini-SWE-agent runs cover 47 tasks; collect an OpenHands cohort for controlled comparison |
| Qwen3.5-27B | Collect with the same settings as 4B and 9B | Collect on the same SWE-MM cohort |

The eight-model SWE-MM panel is Fable 5, Opus 4.8, GPT-5.5, Gemini 3.5 Flash,
Kimi K2.6, and Qwen3.5-{4B,9B,27B}. The three benchmark-specific substitutes
are separate models, not aliases for the Vision2Web entries. Their archives
avoid additional paid agent inference; evaluation and environment restoration
still require compute and possibly judge calls.

If the first study must remain within ten distinct models, start SWE-MM with
the five intended shared models. That smaller panel covers only Claude and
Qwen, so the eight-model SWE-MM panel is preferable for family diversity.
Do not rerun expensive models solely to make a rectangular leaderboard.

Opus 5 and GPT-5.6-Luna/Terra remain available as optional within-family
comparisons. They are not needed in the first panel. MiniMax-M3 and
Qwen3.5-Flash are optional SWE-MM additions, not prerequisites.

## Verified local inventory

Source: `datasets/vision2web-leaderboard/_trajectory_index.csv`, checked against
the files on disk, with record-format inspection. All ten submissions have
193 result JSONs: 100 webpage, 66 frontend, and 27 website tasks.
The downloaded result JSONs total 3,689,421,908 bytes. The local download
manifest pins Hugging Face revision `dd464e60b02f836e768fd9d21baa49c97a53edf6`;
the current remote root exposes the same ten submissions.

| Submission | Structured conversation records | Terminal log records |
| --- | ---: | ---: |
| ClaudeCode+Claude-Fable-5 | 193 | 0 |
| ClaudeCode+Claude-Opus-4.8 | 193 | 0 |
| ClaudeCode+Claude-Opus-5 | 193 | 0 |
| ClaudeCode+DeepSeek-V4-Flash-Vision-Exp | 193 | 0 |
| ClaudeCode+GLM-5.3-Flash | 193 | 0 |
| ClaudeCode+Kimi-K3 | 193 | 0 |
| Codex+GPT-5.6-Luna | 0 | 193 |
| Codex+GPT-5.6-Sol | 0 | 193 |
| Codex+GPT-5.6-Terra | 0 | 193 |
| GeminiCLI+Gemini-3.7-Flash | 193 | 193 |

The Gemini columns describe the same 193 runs, not additional trajectories.
Generation status is `success` for every indexed run except three Luna runs.
These statuses are not official task correctness labels. Failed runs remain
in the inventory.

Codex logs contain assistant text, commands, command returns, and patches;
an empty `conversation` field does not mean there is no trajectory. However,
these terminal transcripts do not supply the same explicit call IDs or image
payloads as native events. Validate parsing, action/return order, truncation,
and image receipts on a few tasks before including GPT in the diagnostic table.
The download manifest explicitly excludes generated website source/assets;
their absence from this directory is not evidence that the upstream submission
lacks them.

Additional project-local Qwen3.5-9B assets were found outside `datasets/`:

- `runs/vision2web_generation/qwen35-9b-claude-code-official-full-0904`: 44 task
  directories with native Claude event files.
- `runs/vision2web_generation/qwen35-9b-claude-code-official-resume-0908`: 43 such
  directories. The union is **86 distinct tasks**: 11 webpage, 64 frontend,
  and 11 website. There is one overlapping task. The older audit reported
  193 tasks; that historical total does not establish current local availability.
- Four `runs/swe_mm_official/qwen35-9b-mini-official-dev*` directories contain
  **50 native mini-SWE-agent records covering 47 tasks**. Recorded model ID:
  `hosted_vllm/Qwen3.5-9B`. Exit statuses are 48 `Submitted` and two
  `BadRequestError`; `Submitted` is not task success. Only 27 of the 47 task IDs
  occur in the current OpenHands curated 68-task list. Retries and the input-fix
  runs must retain their configuration identities.

These assets can support an archive audit. They do not replace new matched
rollouts for a Qwen size comparison if harnesses or observation handling differ.

Sources: [Vision2Web submissions](https://huggingface.co/datasets/zai-org/Vision2Web-Leaderboard/tree/dd464e60b02f836e768fd9d21baa49c97a53edf6),
[older local Qwen audit](qwen35_claude_official_self_check_audit_20260912.md).

## SWE-MM sources and overlap

The OpenHands results tree was pinned at
`3015ac612e7196f428e6e8a3948965d32d9a3331`. It contains 34 main OpenHands model
entries with SWE-MM archive links, plus nine alternative-agent configurations.
Twenty of the 34 main entries declare vision support. This counts entries
and links, not independently validated complete trajectory collections.

| Main OpenHands entry | Indexed task outcomes | Declared vision support | Role |
| --- | ---: | --- | --- |
| Claude Fable 5 | 68 | Yes | Shared model |
| Claude Opus 4.8 | 68 | Yes | Shared model pending alias check |
| GPT-5.5 | 68 | Yes | OpenAI baseline |
| Gemini 3.5 Flash | 67 | Yes | Google baseline |
| Kimi K2.6 | 68 | Yes | Moonshot baseline |
| Qwen3.5-Flash | 68 | Yes | Optional API baseline |
| MiniMax-M3 | 67 | Yes | Optional additional family |
| GLM-5.1 | 68 | No | Optional text-only control |
| DeepSeek-V4-Pro | 68 | No | Optional text-only control |

The five recommended archived SWE-MM systems share **67 indexed task IDs**.
Gemini 3.5 Flash lacks the `Automattic__wp-calypso-35531` sidecar entry found
in the other four. Sidecar absence does not prove that no attempted run exists
in its archive. Validate archive completeness and report that missing task
before freezing a paired cohort. Adding both optional MiniMax-M3 and
Qwen3.5-Flash leaves 66 common indexed tasks.

The Space describes its frontend data as a curated **dev** subset. An inspected
Qwen native row independently records `dataset_split: dev`, a curated instance
file, and `n_critic_runs: 3`. The current benchmark README also contains a
`--split test` command example; do not use that example to relabel archived runs.
Pin each archive's task IDs and metadata. These 68-task results are not full
SWE-MM test-set results or the newer 480-task v2 evaluation.

GLM-5.1 is not GLM-5.3-Flash. DeepSeek-V4-Pro is not
DeepSeek-V4-Flash-Vision-Exp. Their indexed lack of vision support makes them
useful optional text controls, but unsuitable substitutes for multimodal
diagnosis baselines. Likewise, the Qwen3.5-Flash API identifier does not establish
equivalence to a particular released 4B, 9B, 27B, or MoE checkpoint.

Sources: [OpenHands pinned index](https://github.com/OpenHands/openhands-index-results/tree/3015ac612e7196f428e6e8a3948965d32d9a3331/results),
[frontend dataset description](https://huggingface.co/spaces/OpenHands/openhands-index/blob/main/content.py),
[curated task list](https://github.com/OpenHands/benchmarks/blob/main/benchmarks/swebenchmultimodal/resolved_instances.txt),
[GLM metadata](https://github.com/OpenHands/openhands-index-results/blob/3015ac612e7196f428e6e8a3948965d32d9a3331/results/GLM-5.1/metadata.json),
[DeepSeek metadata](https://github.com/OpenHands/openhands-index-results/blob/3015ac612e7196f428e6e8a3948965d32d9a3331/results/DeepSeek-V4-Pro/metadata.json),
[official SWE-MM versions](https://www.swebench.com/multimodal.html).

### Archive probes

Bounded requests confirmed HTTP 206 archive access for the nine entries in the
table. Small prefixes expose native `conversations/*.tar.gz` or `output*.jsonl`
members. Complete nested conversation files were read for Fable 5, Opus 4.8,
and Kimi K2.6; a complete history row was read for Qwen3.5-Flash. The latter is
explicitly **critic attempt 3**, not a first attempt. The Kimi sample is a short
error conversation, not a successful complete coding run.

The three nested conversation samples each contain one user image and no
image content block in an observation event. These user images are task inputs,
not evidence of visual self-verification. An earlier Opus 4.8 p5.js sample has
artifact image receipts, as recorded in the existing replay audit. Availability
of a multimodal model or a task image does not guarantee visual checking in
each task.

These probes establish accessible native data, not full-corpus completeness.
Some bounded transfers reached their time limit after yielding complete small
members. Truncated archive prefixes were not counted as complete archives.
The audit artifacts occupy about 21 MiB; no full archive or model weights were
downloaded.

### Other public sources

- `SWE-bench/experiments` has 22 inspected Multimodal entries, including
  SWE-agent M, OpenHands-Versa, and GUIRepair. Eighteen metadata files have
  null log/trajectory assets and four have no assets block. The inspected
  entries therefore do not establish another ready-to-download corpus for
  the modern models above. This is not a claim that their authors have no logs.
- `OpenHands/openhands-index` on Hugging Face mirrors leaderboard and
  per-instance outcomes. It is not an additional collection of full histories.
- Other search results were often SWE-bench Verified, Multilingual, or
  training traces. They were not treated as SWE-MM trajectories.

Sources: [official submission repository](https://github.com/SWE-bench/experiments/tree/40f164d5b8f1d249bf95a6df8b74b577fd8e519d/evaluation/multimodal),
[example missing trajectory assets](https://github.com/SWE-bench/experiments/blob/40f164d5b8f1d249bf95a6df8b74b577fd8e519d/evaluation/multimodal/20241006_SWE-agent_M_claude3.5/metadata.yaml),
[OpenHands outcome mirror](https://huggingface.co/datasets/OpenHands/openhands-index).

## Qwen selection

Use the released post-trained **Qwen3.5-4B, Qwen3.5-9B, and Qwen3.5-27B**
checkpoints. They provide a smaller, target-size, and larger dense model with
vision support. The 9B checkpoint is the training baseline; `-Base` checkpoints
would answer a different question. This is a within-family size comparison,
not an isolated causal effect of parameter count.

Qwen3.5-35B-A3B is the first optional extension: a 35B-total, 3B-active MoE
model. It tests a different compute/architecture tradeoff, not a fourth point
on a dense-model scaling curve. Small active parameter count does not mean
only 3B weights need storage. The 122B-A10B and 397B-A17B variants need not
block the first study. No GPU feasibility or throughput has been measured here.

The official ms-swift Qwen3.5 guide provides dense and MoE training recipes.
Framework support alone does not validate our long, image-bearing agent
training workload. Keep the exact checkpoint revision, processor/template,
thinking mode, image limits, harness version, and generation budget in the
run manifest. Evaluate the original 9B and trained 9B under the same settings.

Sources: [4B](https://huggingface.co/Qwen/Qwen3.5-4B),
[9B](https://huggingface.co/Qwen/Qwen3.5-9B),
[27B](https://huggingface.co/Qwen/Qwen3.5-27B),
[35B-A3B](https://huggingface.co/Qwen/Qwen3.5-35B-A3B),
[ms-swift guide](https://swift.readthedocs.io/en/v4.0/BestPractices/Qwen3_5-Best-Practice.html).

## Conditions for useful findings

1. Freeze task selection before inspecting diagnostic scores. Use the same
   tasks within each benchmark, retain failures and no-check runs, and stratify
   Vision2Web by L1/L2/L3. A proposed first batch is 40 tasks per benchmark;
   broader findings should use the full feasible paired cohort, not just the
   easiest archive members to retrieve.
2. Record the attempt policy. Do not compare an unassisted first attempt with
   a critic-selected third attempt as if they used the same budget. Archive
   member order is not a sampling rule. Recover original attempts where possible.
3. Report archived **model + harness + configuration** performance. Vision2Web
   mixes Claude Code, Codex, and Gemini CLI; OpenHands versions also vary.
   Use one frozen harness per benchmark for new Qwen runs and method comparisons.
4. Establish actual image receipts and restoration readiness before expensive
   judging. Original and reconstructed observations remain separate. Restoring
   a screenshot cannot establish that the agent originally saw it.
5. Report task counts and diagnostic opportunities alongside VTR, VC, EDR,
   CAR, RSR, and CPR. Use task-level uncertainty estimates. More models alone
   cannot compensate for few observable defects or repair attempts. Reserve
   evaluation tasks from teacher collection, SFT, RL, and reward tuning.

The immediate work is a small readiness pass on each selected system, followed
by the frozen cohort evaluation. This report does not mark all selected
trajectories ready for all six metrics.

## Local audit artifacts

`runs/vsv_eval/model_cohort_20261014/` contains the local inventory, pinned
remote metadata, source URLs, per-instance sidecars, Qwen file inventory,
bounded archive probes, and native sample records. These raw artifacts are
ignored by Git. Only this report and its index entry are intended for sync.
