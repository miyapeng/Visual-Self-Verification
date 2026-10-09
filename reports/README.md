# Current research entry points

Current protocol: [Finding-oriented diagnostics](../docs/self_verification_evaluation.md#finding-oriented-breakdowns)
(`requirement-level-20261009`). Requirement-type coverage, visible-defect auditing,
and per-attempt repair/regression breakdowns use the shared evaluator. The code
has automated test coverage; fresh API judgments and complete observation/version
inputs are still required for a new model comparison. Historical pilot scores
below are not results of this revision. Start with the [system guide](../docs/VSV_SYSTEM.md).

Runtime setup and real trajectory probes: [2026-10-08 validation](vsv_runtime_validation_20261008.md).

Repository layout and migration checks: [reorganization report](repository_reorganization_20261008.md).

Latest engineering update: [Automatic repair acceptance integration](vsv_acceptance_integration_20261008.md).

Earlier implementation revision: [Requirement-level protocol audit](vsv_requirement_level_audit_20261006.md).
The results below are archived pilots; the new observation-first and scoped-recheck stages
require fresh model judgments before a new comparison can be reported.

Latest RS/CP update: [Post-repair acceptance and presentation tolerance](vsv_acceptance_tolerance_20261005.md).

Latest BDA correction: [Scoped judgments and source conflicts](vsv_bda_scope_correction_20261005.md).
The current development result has no unknown diagnosis targets and retains the
[balanced formula with a missing-diagnosis penalty](vsv_bda_penalty_restoration_20261005.md).
The expressed-only 100% result and the temporary ordinary-accuracy experiment are superseded.

Updated 2026-10-05: the current six-metric evaluation entry point is
`scripts/vision2web/score_vsv.py --rounds-json --catalogue`.
See [the implementation guide](../docs/self_verification_evaluation.md) for
commands, record contracts, exact version execution and calibration limits.
The [SmartRecruiters implementation and pilot report](vsv_protocol_implementation_20261003.md)
links the executed evidence, six-metric results, unavailable states and label disputes.
It evaluates visual and text checks using VC, CV, BDA, RS, CP and VCS.
The [interaction acceptance pilot](vsv_interaction_acceptance_20261003.md)
adds fixed UI workflows, constrained element grounding and post-interaction
evidence, with actual before/after executions and recorded Gemini responses.
The [independent metric calls report](vsv_independent_metric_calls_20261004.md)
documents separate VC/CV/BDA and RS/CP calls, the real Winston smoke and limits.
The [acceptance plan fix](vsv_acceptance_plan_fix_20261004.md) documents the
GPT-5.4 plan, executed Winston V3/V4 check and validated repair results.
The [input scope and call accounting report](vsv_input_scope_and_calls_20261004.md)
documents reduced judge inputs, saved-result reuse and all seven requests in
the bounded optimization trial.
The [Gemini 3 Flash pilot](vsv_flash_smartrecruiters_20261005.md) records the
affordable default profile, visual/text evaluation, validation failures and costs.
The [check scope repair](vsv_check_scope_fix_20261005.md) separates actual attempts
from coverage goals, preserves shared-plan context and records a four-round regression.
Earlier Test / Judgment / Repair results below retain their historical meaning
and must not be presented as results of the current protocol.

## Historical project notes

更新：2026-09-17。目标 **CVPR 2027**，当前定位为现象分析、能力诊断与轻量改进。先读[项目主计划](../visual_self_verification_research_plan.md)的当前摘要；旧计划存档不自动进入待办。
维护两条主线：**长轨迹中的自验证**与**从真实代码开始的独立验证**，共用 Test / Visual Judgment / Safe Repair。详细协议见 [两层评测设计](visual_self_verification_two_level_evaluation_design.md)；实现修订见 [协议 v2](vsv_protocol_v2_implementation.md)，输入构造见 [checkpoint 记录](checkpoint_verify_construction.md)。跨模型 handoff 仅为补充分析。

## 文件分工

| 要做什么 | 当前入口 | 数据/产物 |
| --- | --- | --- |
| 切长轨迹、检查调用分组 | `src/multimodalcode/vsv_eval/episodes.py`、`checks.py`；`scripts/vision2web/score_vsv.py --extract-only` | `data/vision2web/vsv_eval_fixtures/`；`runs/vsv_eval/` |
| 三阶段统一评分 | `scripts/vision2web/evaluate_trajectory.py`；`configs/vision2web/vsv_smartrecruiters_pipeline_v2.json` | 每次新输出目录中的 `scores.json` 与 HTML；源码在 `src/multimodalcode/vsv_eval/` |
| 构造/验证中间代码题 | `scripts/vision2web/build_checkpoint_tasks.py build/validate`；`checkpoint_tasks.py` | `data/vision2web/checkpoint_verify/`；启动证据在 `runs/vsv_eval/checkpoint-construction-0908/` |
| 模型工具会话与记录 | `src/multimodalcode/agent_harness/`；`prompts/vision2web/checkpoint_verify.txt` | `runs/vision2web_generation/`；Classic Clashes 已有固定代码续写排障，SmartRecruiters 构造集不因此视为已完成比较 |
| 全量行为审计 | `scripts/vision2web/audit_observed_self_checks.py` | [193 任务结果](qwen35_claude_official_self_check_audit_20260912.md) |
| 命令使用排障 | `scripts/vision2web/probe_vsv_confounders.py` | [小实验记录](vsv_confounder_probe_20260912.md)；`runs/vsv_eval/confounder-probe-0912/`、`confounder-probe-0913/` |
| 20 题抽样与实验条件 | `configs/vision2web/visual_self_verification_pilot_20.json` | L1/L2/L3 = 6/8/6，题目及条件未更换 |

所有表中路径相对项目根目录。`evaluate/` 放冻结的官方 evaluator；`vsv_eval/` 是我们自己的行为评测，二者不是同一种分数。

## 哪些结果现在能用

- **09-12 全量行为统计：** Qwen3.5-9B / Claude Code official 去重后 193 任务，192 正常结束、1 failed；1 个浏览应用、0 个接收应用截图。不是官方通过率。
- **09-13 排障对照：** 正确命令示例组成功收到 4 次应用图像，修改主图路径并再次看图，最终 timeout。只支持局部可执行性，不代表已证明质量提升或稳定自检；详见上表小实验记录。

- **09-08 新版试评分已补齐：** [本轮记录](vsv_scoring_calibration_20260908.md)、[统一结果](../runs/vsv_eval/calibration-0908/integrated/scores.json)。T/C/J/R = **83.33/30.77/81.82/60**；另抽查两条轨迹、复核 11 个判断点。C 的部分条目与若干 J 标签仍有争议；AI 源证据复核不是独立人工校准，不是官方成绩。
- **之前的 v2 空值快照：** [`protocol-v2/scores.json`](../runs/vsv_eval/scorecard-0908/protocol-v2/scores.json)。当时只跑通切分和配置式重放，尚无 Judge；保留作机制开发记录，不再作为最新评分入口。Safe Repair 仍需各轨迹的目标/版本配置。
- **旧版试评分：** [`legacy/scores.json`](../runs/vsv_eval/scorecard-0908/legacy/scores.json)。T/C/J/R = 83.33/23.08/72.73/60；只用于旧协议开发诊断，不能合并成新版模型结果，未完成人工校准。
- **当前 checkpoint：** [Opus 的 3 个输入](../data/vision2web/checkpoint_verify/smartrecruiters-opus-0908/manifest.json)与 [Kimi 的 1 个输入](../data/vision2web/checkpoint_verify/smartrecruiters-kimi-0908-clean/manifest.json)。均已有独立启动证据，但全来自 SmartRecruiters 一个网站，尚未测新会话的验证能力。恢复代码不等于恢复历史数据库或浏览器状态。
- **全量生成快照：** 09-08 09:01 UTC，Claude Code official：Qwen3.5-9B 去重后 159/193 正常交付，Qwen3.8-27B 去重后 41/193，73 次尝试均 timeout。只统计结果文件，不代表官方得分或实时排队状态；详见主设计 §3.5。

## 历史内容保留，但不作为新实验入口

- `runs/vsv_eval/` 下旧 `smartrecruiters-*` 评分与原始轨迹保留，不覆盖。
- `data/vision2web/checkpoint_verify/pilot-0908/`、非 `-clean` Kimi 包保留作构造调试记录。
- `build_verification_pilot.py` / `run_verification_capacity_probe.py` 和 `fresh_context_verification_probe.txt`、`same_context_self_check.txt` 为旧探针；旧 builder 依赖已归档配置，不作为当前可复现构造命令。现在使用 `build_checkpoint_tasks.py`。
- 旧 OpenHands 批量浏览器 smoke 不算原生 BrowserToolSet 的验证证据；当前实验不重新引入该接口。
- InteractWeb 继续归档；Vision2Web **全部 L1/L2/L3 保留**。本轮没有加入替代 benchmark。

## 最小检查命令

```bash
cd /data/miyapeng/mmcode/MultimodalCode
conda activate mmcode
python scripts/vision2web/evaluate_trajectory.py --help
python scripts/vision2web/build_checkpoint_tasks.py --help

# 仅汇总已有 v2 产物，不调用模型或重放；输出目录须尚不存在。
python scripts/vision2web/evaluate_trajectory.py \
  --config runs/vsv_eval/scorecard-0908/protocol-v2-inputs.json \
  --output-dir runs/vsv_eval/v2-readonly-review-new
```

重新产生新版 Judge 结果使用 `configs/vision2web/vsv_smartrecruiters_pipeline_v2.json` 与 `--run-missing`，会调用配置中的模型和重放环境；09-08 后续校准已执行，参数与新产物以[校准记录](vsv_scoring_calibration_20260908.md)为准。Checkpoint 构造、容器启动与 prompt 的精确命令见 [构造记录](checkpoint_verify_construction.md)。

## 前一轮目录整理记录

项目根目录不是 Git worktree。此次只整理入口、状态和过时配置标记，不移动目录、不删除文件、不改官方 evaluator、不启动推理。现有绝对路径继续可用。
下一步以项目主计划为准：定位命令示例组后段失败 → 少量跨网站复测 → 同预算视觉/文字证据对照及质量评估，并补人工评分校准。下面测试数量是此前整理时的历史记录，不是本次新执行。

验证：155 项相关测试通过（系统 pytest，禁用无关自动插件）；两个入口在 mmcode 中 `--help` 成功，已有 v2 产物的只读汇总也成功且仍为 partial。修正了一处旧探针测试与现存 prompt 的字面措辞不一致，未修改 prompt 或 Agent 行为。
修改文件：根 `README.md`、本页、两层评测设计、`configs/vision2web/visual_self_verification_pilot_20.json`、`tests/test_vision2web_verification_pilot.py`。

- [Opus leaderboard pilot and current SmartRecruiters scores](vsv_leaderboard_opus48_pilot_20261005.md): 193-case evidence audit, two text-only pilots, extraction failures, and the validated current score join.

- [Matched-model Vision2Web pilot](vsv_matched_models_pilot_20261005.md): two tasks across four coding models, text scores, missing visual evidence, extraction review, and request accounting.

- [Archived-text proxy evaluation pilot](vsv_text_proxy_pilot_20261005.md): opt-in six-metric estimates across four models, evidence provenance, strict-mode isolation, and missing-pixel limitations.

- [Cross-benchmark adaptation and sample audit](vsv_cross_benchmark_adaptation_20261008.md): SWE-MM, 3DCodeBench and GameDevBench input/acceptance adapters; no paid API scoring.

- [In-session Codex scoring pilot](vsv_codex_session_pilot_20261008.md): one fully evaluated 3D trajectory, explicit judge provenance, zero API requests.

- [Cross-benchmark verification diagnosis and method plan](vsv_diagnosis_synthesis_20261008.md): 16 paired task-model traces, evidence-linked findings and no-API mechanism experiments.
