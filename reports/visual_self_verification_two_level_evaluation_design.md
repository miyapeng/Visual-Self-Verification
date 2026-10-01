# Visual Self-Verification 两层评测设计

> 状态：工作稿，状态同步于 2026-09-17，目标会议 CVPR 2027。当前方向和下一步以[项目主计划](../visual_self_verification_research_plan.md)为准；本文保留详细协议与历史记录。三个离线阶段已完成单轨迹集成，但 Judge 和泛化有效性尚未校准；所有生成成功数不代表 Vision2Web 官方得分。

**最新进展（覆盖下文旧进度数字，不改写历史）：**

- 09-12 审计 Qwen3.5-9B / Claude Code official 全部 193 任务：192 正常结束、1 failed；1 个浏览自己的应用、0 个实际接收应用截图。见[原始轨迹审计](qwen35_claude_official_self_check_audit_20260912.md)。这不是 OpenHands browser-disabled 那批轨迹。
- 09-12/13 的 Classic Clashes 固定代码排障：工具图像通路通过；baseline 和 guided 均无应用图像，追加正确命令示例后有 4 次实际图像输入及一次路径修改后视觉复查，最终 1200 秒 timeout。见[实验记录](vsv_confounder_probe_20260912.md)。这是新会话代码续写，不是官方从零生成条件，也没有证明视觉独立收益。
- 三阶段评分仍是待人工校准的开发版本；新排障轨迹尚未完成三阶段评分或官方前后质量评测。Qwen3.8 的旧状态仅作带日期记录，本次没有重新统计。
- 当前不新增批量浏览器接口、强制验证 gate 或外部 verifier 决策者；不启动 SFT/RL，不恢复 InteractWeb。

最新审计、漏洞和最小补救见 [VSV 学术审计与集成](vsv_evaluation_academic_audit.md)。统一入口为 `scripts/vision2web/evaluate_trajectory.py`，不改 Agent 的推理流程。

当前文件与产物入口见 [研究目录](README.md)。09-08 新版三阶段补评分已完成：T/C/J/R = **83.33/30.77/81.82/60**，另抽查两条轨迹、复核 11 个判断点；仍有覆盖条目与视觉标签争议，**未完成独立人工校准**。证据及限制见[评分校准记录](vsv_scoring_calibration_20260908.md)。旧协议试评分不能代替新版结果。Checkpoint 已有同一网站的 4 个可启动输入，尚无新模型能力比较。

**本轮机制修订（09-07）：** Test 改为同片段联合判断覆盖；Visual Judgment 可回溯此前片段的图像与工具证据；Safe Repair 改为显式配置“需求—原检查—前后版本—回归项”。不重切片段、不改 Agent、不覆盖旧分数。实现与复现记录见 [协议修订记录](vsv_protocol_v2_implementation.md)。

## 1. 两层评测

1. **Trajectory-level**：从原始 Vision2Web 任务开始，测模型是否主动产生有效的视觉检查和证据驱动修改。
2. **Checkpoint-level**：所有模型从同一个可运行版本开始，单独测检查、判断和修复能力。

未主动检查不等于没有能力；guided prompt 下成功也不等于模型天然具有主动性。

核心问题：

- **RQ1 Initiation**：模型是否主动观察已部署应用？
- **RQ2 Verification**：模型生成的检查是否相关、可执行且判断正确？
- **RQ3 Repair**：视觉/运行证据是否带来有效代码修改？
- **RQ4 Safety**：修复是否经过复查且不破坏已有功能？

工作假设是：模型可能存在“有能力但不主动”的触发差距；任务复杂度和长上下文会削弱检查；视觉修改可能改善目标状态但引入功能回退。Pilot 先比较一个强闭源模型和一个开源模型，模型集合在协议稳定后再冻结。

## 2. 实验条件与当前执行范围

| Scaffold | 条件 | Prompt | 浏览器能力 |
|---|---|---|---|
| OpenHands | `official` | 官方原文 | 官方 CLI；无注册的 BrowserToolSet |
| OpenHands | `browser_enabled` | 与官方逐字节相同 | 完整且未修改的原生 `BrowserToolSet` |
| OpenHands | `guided_vsv` | 官方原文 + 冻结 VSV 指导 | 与 `browser_enabled` 相同 |
| Claude Code | `official` | 官方原文 | 官方镜像内 `playwright-cli` |
| Claude Code | `guided_vsv` | 官方原文 + 冻结 VSV 指导 | 与 `official` 相同 |

因此 OpenHands 分别测工具增益和 prompt 增益；Claude Code 只测 prompt 增益。历史名称仅兼容旧产物：`tools` 对应 `browser_enabled`（Claude Code 中对应 `official`），`self_verify` 对应 `guided_vsv`。

当前为节省时间，**全量基线只运行 Claude Code `official`**：原始 Vision2Web prompt、官方镜像内 Claude Code 与 `playwright-cli`，不附加 VSV 指导。OpenHands `browser_enabled` 用于确认原生浏览器接口和后续 scaffold 对照；`guided_vsv` 只作为 elicitation 诊断，不是当前主要 setting，也不与 spontaneous self-verification 混合汇总。

Guided prompt 冻结以下流程，但不由 harness 强制执行：

- 使用已有 `start.sh` 启动首个可运行版本并观察真实页面；
- 每次只检查一个用户可观察功能；
- 先声明 `objective / expectation / reset / actions`；
- 使用原生浏览器工具逐步交互，根据截图、页面状态和 action error 自行判断；
- 只在证据支持时修改，随后复查受影响功能并保护已有功能；
- 自行决定继续检查或提交。

完整文本见 [`prompts/vision2web/guided_vsv.txt`](../prompts/vision2web/guided_vsv.txt)。`workflow.json` 和 evaluator 始终只在轨迹结束后使用。

## 3. 已有工作与实现状态

### 3.1 已完成的现象审计

对历史 Qwen3.5-9B official Vision2Web 运行的保守审计已经完成：193 个任务中有 189 条轨迹；121 个任务执行过 882 次终端检查，但**真实生成页面的浏览器/截图检查为 0**。72 个任务没有主动检查，36 个只在最终版本检查，22 个检查后修改但未复查。139/189 条轨迹发生过 OpenHands condensation，但没有硬 context-length error。

该批 OpenHands official 没有启用原生 BrowserToolSet，因此“0 次视觉检查”首先是该配置下的观察，不能单独证明模型在工具可用时不主动检查。机器结果位于：

- `runs/research/active_visual_verification/observational-audit-010/`
- `runs/research/active_visual_verification/implementation-rhythm-audit-002/`
- `runs/research/active_visual_verification/context-window-audit-001/`

单例 `frontend/smartrecruiters` 的 scaffold × model 探索也已完成：六条 guided 轨迹中，只有 Claude Opus 4.8 + Claude Code 自主形成部署、视觉观察、交互、证据后修改和复查；Qwen3.8-27B 主要因过度原型分析而在实现前后 timeout。该结果仅用于理解失败机制，不能代替多任务对照。报告见 [`vision2web_scaffold_model_comparison/REPORT.md`](vision2web_scaffold_model_comparison/REPORT.md)。

### 3.2 已完成的代码与配置

| 模块 | 状态 | 主要位置 |
|---|---|---|
| 数据与 Pilot | 已冻结 Vision2Web revision、全部 193 任务及 20-case 6/8/6 子集 | `data/vision2web/manifest.json`；`configs/vision2web/visual_self_verification_pilot_20.json` |
| 条件与 Prompt | 已实现 scaffold-specific 条件、旧名称兼容和冻结 guided prompt | `src/multimodalcode/agent_harness/vision2web_experiment.py`；`prompts/vision2web/`；`configs/vision2web/self_verify_*.json` |
| OpenHands | `official` 保持官方 CLI（browser disabled）；另两组仅追加完整、未改 schema 的原生 `BrowserToolSet` | `src/multimodalcode/agent_harness/vision2web_openhands_browser_entrypoint.py` |
| Claude Code | 已实现 ClusterX direct-container 官方命令适配，保留官方 `playwright-cli` 和 stream-json | `src/multimodalcode/agent_harness/claude_code_runner.py` |
| 被动轨迹 | 已合并 model/tool、terminal/edit/deploy、browser evidence、workspace change 和结束事件 | `src/multimodalcode/agent_harness/vision2web_trace.py`；`scripts/vision2web/rebuild_development_timelines.py` |
| 版本 | 已保存可重建的 `P_first/P_final`；应用观察绑定代码 hash、manifest 和服务身份；当前代码已修复 Claude 首次 snapshot 晚存问题 | `src/multimodalcode/agent_harness/vision2web_trace.py`；`src/multimodalcode/agent_harness/claude_code_runner.py` |
| 信息边界 | workflow/evaluator 不进入模型；官方 evaluator 未修改，只允许事后评分 | `scripts/vision2web/run_generation_case.sh`；实验 configs |
| 运行与展示 | 已有 ClusterX 提交、监控、轨迹标准化与 HTML 展示脚本 | `scripts/vision2web/`；`reports/vision2web_trajectory_explorer/` |
| 回归测试 | 覆盖 prompt hash、入口隔离、两种 browser context、版本 checkpoint 和 observation binding | `tests/test_vision2web_self_verify_openhands.py`；`tests/test_vision2web_claude_code.py` |

当前公共 benchmark 配置冻结为：数据 revision `8f03299d...`、代码 commit `577f939...`、镜像 `vision2web:official-577f939` 和每个 case 7200 秒 wall time。2026-09-04 全量生成使用 262,144 服务上下文、229,376 最大模型输入、32,768 最大单次输出、3 个 CPU case worker 共享一个 TP=2 vLLM 服务，并关闭 prefix caching。Qwen3.5-9B 使用 `reasoning_effort=high`，Qwen3.8-27B 使用其支持的默认级别 `xhigh`；本轮每个 case 不做 Agent retry。

7200 秒来自当前 Vision2Web 公共仓库默认值，但 [Qwen3.8-27B 模型卡](https://huggingface.co/Qwen/Qwen3.8-27B)只公开其 Vision2Web 结果使用 Claude Code harness 和 `gpt-5.4-2026-03-05` Judge，没有公开硬件、并发、Claude Code 版本、单 case wall time 和完整请求参数。因此当前运行是公共 benchmark setting 下的本地实验，**不是 Qwen 报告分数的严格复现**。最终跨模型比较需要在 pilot 后统一冻结吞吐、并发和预算。

### 3.3 已完成的工具链证据

- OpenHands 旧批量接口的确定性 browser smoke（仅历史证据）：`runs/vision2web_self_verify/probes/browser_interface_smoke_v5_viewport_r1.json`；
- Claude PNG 确实进入模型上下文：`runs/vision2web_self_verify/end_to_end/tool_checks_v4/claude-png-context/result.json`；
- 人工控制的重复检查：[`vision2web_repeated_function_check.md`](vision2web_repeated_function_check.md)；
- 真实 Level-2 `academy_govloop` 容量验证：[`vision2web_real_case_capacity_probe.md`](vision2web_real_case_capacity_probe.md)；
- 历史 OpenHands/Claude Code 长轨迹浏览：[`vision2web_trajectory_explorer/index.html`](vision2web_trajectory_explorer/index.html)；
- scaffold/model 单例对比：[`vision2web_scaffold_model_comparison/index.html`](vision2web_scaffold_model_comparison/index.html)。

这些旧证据证明过浏览器和记录链路可用，不等价于当前原生
`BrowserToolSet` 的容器运行证据，也不证明模型会主动使用。当前协议不再
提供 `browser_execute_plan`；模型通过原生逐动作工具自行决定检查粒度。

### 3.4 2026-09-04 Claude Code official 全量基线

运行对象为 Vision2Web 全部 193 个 case，顺序覆盖 `webpage`、`frontend` 和 `website`。下表冻结于 2026-09-05 07:30 UTC；当前尚未执行官方 evaluator。

| 模型 | 当前进度 | 已保存结果 | 生成状态 | 直接问题 |
|---|---:|---:|---|---|
| Qwen3.5-9B | 平台已结束约 146/193 | 140 | 134 success，6 failed | 控制器因磁盘配额耗尽退出；6 个已结束 job 缺少 `result.json`，47 个尚未提交 |
| Qwen3.8-27B | 29 completed，3 running，161 new | 29 | 29 timeout | 所有已结束 case 均达到官方 7200 秒上限，尚无正常结束样本 |

Qwen3.5 的 `success` 只表示 Claude Code 正常退出且生成了 `start.sh`，不能当作功能或视觉通过。其控制器日志和产物位于：

- `runs/vision2web_generation/qwen35-9b-claude-code-official-full-0904/`

Qwen3.8 的已完成轨迹显示系统性停止失败，而不是 API 或 context-length 错误：

- 29/29 均运行约 7200 秒后由 runner 终止；
- 共记录 1,101 次 Bash、896 次 Read、43 次 Write 和 26 次 Edit，平均约 74 次工具调用/case；
- 17/29 在超时点仍没有非输入程序文件，12/29 保存了部分实现但未正常提交；
- 最大观测输入约 188K token，低于 229,376 输入上限，服务日志没有 context-length error；
- 模型主要反复裁剪、读取和测量原型图，部分 case 写入代码后仍继续细化，没有形成停止决策。

这批结果首先说明当前 `TP=2 + 3 case 并发 + prefix cache 关闭 + xhigh` 的 wall-clock 条件不足以支持 Qwen3.8-27B 的长 Claude Code 轨迹；不能据此断言模型本身无法完成 Vision2Web，也不能与模型卡的 62.9 直接比较。产物位于：

- `runs/vision2web_generation/qwen38-27b-claude-code-official-full-0904-v2/`

### 3.5 尚未完成或仅部分完成

**09-08 09:01 UTC 产物更新：** 按原任务去重、取最新 `result.json`，Qwen3.5-9B 保存 159/193 个任务的 success（全在 L1/L2）；Qwen3.8-27B 保存 41/193 个不同任务，73 次尝试均 timeout（全在 L1）。来源为 `runs/vision2web_generation/` 下两模型的 `claude-code-official-full-0904*` 和 `claude-code-official-resume-0908/`。这是文件快照，不是实时调度状态；success 仍只表示 runner 正常交付，不是部署或官方评分通过。前节 09-05 数字仅作历史。

- 新轨迹已增加 content-addressed program blobs；旧轨迹仍只有 hash/manifest 或 `P_first/P_final`，无法保证任意中间版本可重建。
- episode parser、workflow 候选对齐、开源/闭源 Judge 客户端、机械重放器和 JSON/HTML 汇总已经实现；真实轨迹人工标注、Judge 校准及批量 replay 尚未完成。
- Claude Code 的任意 `run-code` 脚本不能无条件转换成语义 action，无法安全恢复时必须人工规范化，不能改写后冒充原 action replay。
- 20-case × 新冻结条件尚未形成完整结果；旧 `tools/self_verify`、timeout 和协议失败轨迹不得直接聚合。
- Qwen3.5 的恢复批次已有新结果；下一次续跑须重新核对最新产物，不按旧的“6 个缺失、47 个未提交”名单重复提交。
- Qwen3.8 仍未正常交付；需区分推理吞吐、预算和模型行为，不能将当前 L1 timeout 归因为 L3 全栈难度。
- 两个全量运行均未评分，当前不存在可报告的 Vision2Web 功能分或视觉分。

## 4. 从长轨迹中切出检查块

规则切分器只保留两种部署后检查，不做正确性判断：

- `visual`：当前应用截图真实进入 coding policy 上下文；
- `functional`：浏览器执行 click、fill、hover 等功能交互并返回状态。

相邻同类检查属于同一连续验证阶段时合并。每段保留动作、观察、policy
消息、截图、代码版本和 workspace 变化；编辑与复查只记为事实字段，不再
强制输出 `inspect_only/check_then_edit/...` 标签。原型图、纯 console、单纯
HTTP 200、依赖探测、部署前调用，以及只生成但未送入模型的截图均不切出。

切分结果以 `events` 为可审计的原始时序，同时保留后续评分直接需要的规范化
视图：`action_sequence`、`observations`、`policy_messages`、`edit_actions` 和
已解析的 `images`。代码版本由 `program_before/program_after` 与
`workspace_changes` 关联。任务、模型、框架和模式只保存在文件顶层，不在每个
片段重复；`visual_evidence_consumed` 与 `verification_kind=visual` 等价，已移除。
紧凑 summary 只用于人工快速审计，不是第二份权威数据。

## 5. 单个检查块的评测

### 5.1 Test

Test 判断两点。

**第一，原轨迹执行情况（规则）。** 按工具调用 ID 配对动作和反馈，一次调用的多 scenario
保留为一组；截图 Read 关联回生产调用。输出 `pass/partial/fail/not_evaluable`、反馈和
错误的 event ordinal。检查工具异常与应用功能失败分开处理；不因 `is_error=false` 就判成功。
Test 不要求重放，中间代码快照缺失不阻止该项评估。

**第二，检查合理性（模型）。** 每个片段一次输入完整原始任务、该片段各组动作及配对反馈、
已有 policy 文本、编辑边界及完整 workflow。Judge 按每组对检查过程的贡献输出
`reasonable/partial/unreasonable/not_evaluable`，
引用需求原文及动作/反馈编号。判断提出该检查是否服务于原始需求、所选方法能否检验其目标；
工具失败不自动使检查意图不合理，实际覆盖只看本次获得的证据，不借用后续重试。
workflow 不是唯一白名单，合理的视觉检查可以没有 workflow 匹配。Test 只判断检查方法，
不调用图像 Judge，不判断页面是否正确；真实视觉内容的判断留给 Visual Judgment。
合理性引用连续的需求原文和本组 action；覆盖引用有序的原始 action/observation 链。
结构或引用错误最多纠正一次，原始回答与纠正回答按片段各保存一份，仍不合法则标明待评。

执行情况和合理性分别汇总，不合并为一个布尔分数。合理率以已评估组为分母，同时报告
已评估数量。

**Test 整体覆盖率（规则汇总模型匹配）。** 完整覆盖率 = 已完整检查的 workflow ID 数 / 该任务
全部 workflow ID 数。跨调用、跨片段按 ID 去重，保留官方条目，不按调用数计分。
相关的 click、fill、get_state 可共同证明一次完整检查；不能因为它们分成多个工具调用而低估覆盖。
证据链不能跨代码修改、部署、显式 reset 或失败重试；分支检查可分别引用完整的独立链。
另列“仅部分覆盖”的条目数与明细，不折算半分，也不把不确定状态下的零散步骤拼成完整覆盖。
点击后观察到应用错误仍可算检查覆盖；直接打开目标 URL 或只看见按钮，不覆盖其交互流程。
逐条列出 full / partial / uncovered；尚有缺失或不合法判断时，未证实条目标为 unknown，
覆盖率仅为已确认下界；全部未评时为 null。此指标不是功能通过率，也不代表 workflow 外的合理检查无效。

以下为旧逐调用协议的历史试评，新协议不会自动继承这些分数。

2026-09-07 已实现 `checks.py`、逐组 Judge 输入与校验、`--test-only` 入口和 HTML 展示。
Opus/Claude Code pilot 共 12 组：执行 10 pass、1 fail（247）、1 partial（272）。
该目录保存离线执行判断，12 组合理性均未评分。
产物：`runs/vsv_eval/test-v2-smartrecruiters-0907/`，命令与限制见同目录 `README.md`。

2026-09-07 已完成 232–298 六组的 Qwen3.8-27B 真实 Judge 试评，复用已有服务；
temperature=0、8192 输出上限、low thinking，必要时同一模型接收原型和实际截图。
输入 SHA256 核实与原始指令一致；命令、响应与结果保存在
`runs/vsv_eval/test-qwen38-smartrecruiters-0232-0907/`。该试评只校验 Test，不能当作
Visual Judgment/修复得分或 Judge 准确率；模型优劣仍需人工标注对照。
实际 6 次文本、4 次附图请求：5 组合理，272 因需求引用含省略号拼接而未通过校验，
记为待评而非不合理。Judge 存在检查合理性与页面正确性混淆、workflow 引用缺失 action
编号的问题；原始响应均保留，下一步先收紧判断边界与引用格式，再做小规模人工校准。

上述问题已在第二版收紧：Test 关闭附图调用、显式引用校验与单次格式纠正，HTML 增加完整
workflow 覆盖明细。对经典轨迹全部 3 个片段、12 组的复测产物独立保存在
`runs/vsv_eval/test-qwen38-workflow-v2-0907/`，不覆盖第一轮结果。
复测完成：12 组均通过引用校验并被 Qwen 判合理；13 次文本请求（含 232 的一次范围纠正）、
0 次图像请求。自动覆盖 3/13（23.1%），另 3 条 partial；此为试评分。抽查发现 Judge 将
hover 展开视为 click 展开，并对 Load More 后 URL 状态作了推断，这两处须人工裁定；不能
把结构校验完成当作语义准确。具体证据与命令见同目录 README。

### 5.2 Visual Judgment

Visual Judgment 只评“原模型如何理解反馈”，不重评 Test，也不判断修复成功。

2026-09-07 已实现 `src/multimodalcode/vsv_eval/visual_judgment.py`，入口为
`scripts/vision2web/score_vsv.py --visual-only`，独立运行时不读取 workflow。

1. 规则在原轨迹中确认此前收到过应用图片，再提取片段中的真实模型文字；不因本片段没有新图而排除候选。定位模型只返回含判断/假设的原事件编号，不评分。
2. 每个判断位置单独构造历史前缀：完整任务、按页面匹配的原型、此前收到的截图及原始 action/observation/模型文字。允许跨片段引用；保留 `inspect` 等反馈。截图标注事件时的 workspace hash，不能据旧图证明后续版本。判断之后的动作与修复结果不进入评分。
3. 同一个 Qwen3.8-27B 在独立请求中评分：正确 / 部分正确 / 错误 / 证据不足，附原话、真实事件编号和中文理由。区分观察与因果解释、假设与断言；截图与解释相容不等于证实原因。
4. 原 episode 不变，只增加内部判断记录与类别计数。没有判断或 API/格式失败单列；不报告伪造的准确率，不推断未表达的思考，也不计漏检率。

输入来自已记录的判断前历史，不声称所有旧图都在模型压缩后的上下文里。图片优先保留显式提及的图及各页面最新图，
省略图片和未覆盖页面均显式记录；证据不足时不猜测。原型匹配依赖已记录 URL/截图文件名，匹配依据保留。旧版全段混合截图、
workflow 选原型、丢失 inspect 反馈的 Visual Judgment 路径已替换。

首轮试评与有限修正版分别保存在 `runs/vsv_eval/visual-qwen38-pilot-0907/` 和
`runs/vsv_eval/visual-qwen38-pilot-v2-0907/`。固定检查 237、254、259，但必须先由定位器找到，
不会手动构造判断。结果与限制见各目录 README。
真实测试还发现原型/观察混淆，已补逐图编号与图片角色提示。当前同协议三样本结果在
`runs/vsv_eval/visual-qwen38-image-label-check-0907/`。254 的因果解释仍有过度认可，需要人工复核；
实现已跑通不等于 Judge 已校准，不能把这些自动标签当成真值。

### 5.3 Safe Repair

Safe Repair 只在检查后发生真实代码修改时评估，包含两点。

**第一，目标错误是否被修复。**

- 分别部署 `P_before` 和 `P_after`；
- 在两个版本上执行该检查对应的动作或功能流程；
- 将前后运行结果交给外部模型/官方 Judge，判断目标错误是否真正得到修复。

**第二，patch 是否影响其他功能。**

- 读取 patch，分析它可能影响哪些其他功能；
- 对相关功能执行回归检查；
- 根据实际运行结果判断是否出现新的功能或视觉错误。

最终输出目标是否修复、是否产生其他影响及原因。代码分析用于寻找可能受影响的范围，不能代替实际运行验证。

当前使用显式 `repair_records` 连接：需求来源、目标期望、原动作/反馈编号、前后代码哈希及相关回归检查。
启动命令、代码目录、reset route 和结果断言放入任务配置，公共执行器不再按五个功能名称写死判断。
配置使用已核验版本 manifest，或明确审计过的 Edit-only 重建；没有对应代码时不能用最终版本替代。
同代码版本不计 repair attempt；先前没有执行通过的功能不能作为“无回归”基线。

2026-09-07 已在经典 SmartRecruiters / Claude Opus 4.8 / Claude Code 轨迹上完成一次三阶段 pilot：
`runs/vsv_eval/smartrecruiters-full-0907/`（入口 `index.html`、结果 `scores.json`）。

- 保留原来三个检查片段；Safe Repair 内部按连续修改与后续检查，重建 V0–V3 三段修复，不重切原 episode。8 次原始 Edit 都唯一匹配，6 个改动文件最终与保存快照一致。
- 原记录的 61 个 workspace 哈希包含浏览器产物，不等于 61 个代码版本。新版本清单仅标识应用文件，并恢复原任务素材；不声称任意历史节点都可恢复。
- 4 个版本各打开 6 页，并执行原事件 299/302/305/310/313 的 JS 检查。增加独立 route reset、逐步截图和本地端口映射，因此是规范化重放，不是逐字节复现历史 CLI 时序；每步证据与版本分别保存。
- 对五个修复目标，Qwen3.8-27B 看同一要求、原型、实际 before/after 截图。只有 before 不满足且 after 满足才算修好；两边本来都满足不算修复增益。未使用代码 Judge 决定成败。
- 自动结果：3/5 修好（空白区域、第二次 Gartner 修改、Pricing 素材），2/5 未修好（第一次 Gartner 修改、删除评价区白块但未恢复所需品牌标志）。五组交互检查在四版本都通过，三段未发现该范围内的功能回归；不代表全站安全。
- Test 复用同一轨迹已完成的 12 组调用评测：10 成功、1 失败、1 部分执行；12 组方法合理；workflow 完整覆盖 3/13，另 3 项部分覆盖。旧 Visual Judgment 找到 11 处：自动 8 正确、3 部分正确；254、346 的理由仍有人工复核项。旧版漏掉了无新截图片段里的 307，新版已允许回溯，尚未产生其新 Judge 标签。

实现：`repair_pilot.py` 重建与规则，`score_repair_pilot.py` 调度，`replay_recorded_browser.cjs` 原动作执行，`report_vsv_pilot.py` 汇总。
本轮目标/断言是明确记录的人工审核 pilot 配置，不是自动通用定位器；复现命令及限制见结果目录 README。无官方分数、无加权总分，Judge 尚未校准。

### 5.4 单个检查块总结

学术审计补充（2026-09-07）：11 处是旧定位结果。307 跨片段回溯已通过输入构造测试，但并非新评分完成。另对 V0 做了不改代码、仅滚动的对照，5 处隐藏内容均恢复显示。原 `reveal` 分数仍按固定的 full-page capture 标准保留，但只能解释为该观察条件下的改进，不能直接宣称正常浏览故障修复。证据：`runs/vsv_eval/audit-controls-0907/`。

Test、Visual Judgment 和 Safe Repair 由规则连接已有证据索引并分别汇总，不再默认调用第四个模型决定整体正确性。三个阶段的单位和适用条件不同，不相加或投票；可选文字说明只能解释已有结果。

统一入口仍是 `scripts/vision2web/evaluate_trajectory.py`。旧 `vsv_smartrecruiters_pipeline.json` 只用于复用历史结果；新评分配置为 `vsv_smartrecruiters_pipeline_v2.json`，加 `--run-missing` 才执行缺失阶段。生成独立的 `scores.json`、`manifest.json` 和 `index.html`；三阶段共用原 episode ID。Safe Repair 已去掉 case 名称限制，但仍需任务自己的可执行检查、运行配置和真实版本，目前实测样本仍只有 SmartRecruiters。

### 5.5 具体模型与执行器

当前 Test 与 Visual Judgment 的真实 pilot 使用 **Qwen3.8-27B**，被测轨迹来自 Claude Opus 4.8。
Visual Judgment 默认 profile 为 `qwen38_27b_visual`（JSON 模式、low thinking、temperature=0、8192 输出上限）；
Test 使用独立的原 profile，不随视觉 JSON 配置变更。旧 Qwen3.5/Qwen3-VL 配置保留用于后续比较，不能视为已校准的最终 Judge。
后续评 Qwen3.8 自身轨迹时须引入不同模型核验；Safe Repair pilot 也已使用 `qwen38_27b_visual`，但三阶段 Judge 均未完成独立人工校准。

| 环节 | 固定实现 | 模型实际输入 | 模型输出的作用 |
|---|---|---|---|
| 检查块切分 | 规则 parser + 人工抽查 | 工具顺序、时间戳、URL、workspace hash | 不使用模型 |
| Test：执行情况 | 原轨迹规则解析 | 工具动作、实际反馈、图片消费事件 | 成功/部分完成/失败/证据不足，不重放 |
| Test：检查合理性 | `Qwen3.8-27B`；单片段联合输入 | 完整任务、workflow、各调用组及反馈、修改边界，仅文本 | 逐组合理性；按连续执行链判断完整/部分覆盖 |
| Visual Judgment | `Qwen3.8-27B` 定位 + 独立上下文评分 | 任务、相关原型、判断之前的真实截图/工具反馈/模型文字 | 判断位置、正确/部分正确/错误/证据不足、依据与理由 |
| Safe Repair：目标修复 | `P_before/P_after` 实际重放 + `Qwen3.8-27B` pilot | 同一标准下的原型、修复前/后截图和运行错误 | 分别判断 before/after 满足情况，由规则得到是否修好；不是官方分数 |
| Safe Repair：副作用 | 配置指定原动作及结果断言；示例仍为五组 JS 检查 | before 实际通过项与 after 结果；patch 供人工核查 | 只表示已测范围内有无回归；代码 Judge 不能决定成功 |
| 检查块/轨迹汇总 | 确定性规则 | 前述结构化结果及证据索引 | 不新增 Judge，不形成加权总分 |

各阶段固定模型 revision、prompt、`temperature=0` 和 JSON schema。`workflow.json` 与官方评分信息只在轨迹结束后进入离线评测，绝不提供给 coding policy。

**校准而非投票（尚未完成）。** 先由人工盲审建立参考标签，再检查主 Judge 的错误类型；不同模型复核是辅助，不能代替人类校准，也不平均模型分数。历史配置中保留 Opus/GPT-4o 复核选项，并不表示已经完成复核。被测模型与 Judge 相同的实验尤其需要独立核验；固定 temperature 也不保证确定性。

### 5.6 Claude Opus 4.8 轨迹中的首批 pilot

现有 `frontend/smartrecruiters` Claude Code 轨迹切出三段：

1. `visual 232--298`：7 张部署页截图进入模型上下文；
2. `functional 299--315`：5 组筛选、导航、FAQ、轮播和移动菜单交互；
3. `visual 341--346`：最终截图复查。

`209--230` 的 console 错误检查和纯 HTTP 验证不进入当前视觉/功能切分。
切分器也不把片段内编辑自动解释为由检查触发的 repair。

轨迹证据位于：

- 标准化时间线：[`vision2web_scaffold_model_comparison/data/runs/claude-opus-4-8-claude_code.json`](vision2web_scaffold_model_comparison/data/runs/claude-opus-4-8-claude_code.json)；
- 原始 Claude Code 流：[`vision2web_scaffold_model_comparison/raw/claude-opus-4-8-claude_code/claude.events.attempt-1.jsonl`](vision2web_scaffold_model_comparison/raw/claude-opus-4-8-claude_code/claude.events.attempt-1.jsonl)；
- workspace/browser 统一顺序：[`vision2web_scaffold_model_comparison/raw/claude-opus-4-8-claude_code/development_timeline.jsonl`](vision2web_scaffold_model_comparison/raw/claude-opus-4-8-claude_code/development_timeline.jsonl)。

## 6. 整条轨迹的汇总

一条轨迹可能包含多次检查。汇总时需要：

- 保留每个检查块的 Test、Visual Judgment 和 Safe Repair 结果；
- 根据 workflow 条目统计模型实际覆盖了哪些功能；
- 同一 workflow 条目的重复检查不能重复计算覆盖率，但应保留多次尝试及其结果；
- 分别汇总动作有效性、workflow 覆盖、视觉判断正确性和安全修复情况；
- 通过原事件和版本索引生成确定性轨迹汇总，不再调用额外总结 Judge。

不形成单一总分。合理性按已评调用组、视觉判断按可判定的已表达判断、修复按已复现失败的目标尝试分别计分，并报告缺失/不可判数。无检查轨迹的已观察 workflow 覆盖为 0；未表达判断或未尝试修改不虚造条件正确率。正式模型比较按任务聚合，不把同一任务内的调用、截图或多次修复当独立样本。当前四档视觉标签含“现象与原因解释”的混合，修订及人工校准前只作为开发诊断。

### 6.1 百分制分数记录（2026-09-08 已实现）

仅在统一入口的 `scores.json` 新增 `scorecard`，HTML 同步展示。不改变原标签、切分、Judge、重放或 Agent。

| 字段 | 分数 = 100 × 分子 / 分母 |
| --- | --- |
| T | `execution=pass` 且 `reasonableness=reasonable` 的组数 / 两项均可评的组数 |
| C | 完整覆盖的去重 workflow 条目数 / 固定全部条目数 |
| J | 正确判断数 / 正确、部分正确、错误的判断总数 |
| R | 修好且相关回归通过的目标尝试数 / 原错误已复现且修复与回归均可评的目标尝试数 |
| regression（辅助，越低越好） | 出现回归的版本转换数 / 相关回归检查可评的版本转换数 |

T 是逐组取交集，不是执行率与合理率相乘；`partial` 不折算半分，应用错误被成功观察到不等于工具执行失败。J 沿用原四档标签。
每项保存 `score/numerator/denominator/stage_status`。T/J/R 另存 `excluded_count`：包含已知证据不足或不适用项，原因见原阶段计数；它不估计定位器漏掉的判断。
C 的 unknown 条目仍在固定分母中，未完整评估时标下界；全未评或分母为零时分数为 null。无检查轨迹的 C 为 0，T/J/R 不因此填 0。
辅助回归记录不局限于真正修复错误的尝试，另保留 `target_regression_count`，避免隐藏“原本正确的目标被改坏”。安全范围仍仅限实际重放项。
不合成总分；当前记录单位是一条任务轨迹，跨任务比较按任务等权，不按调用数池化。旧标签换算只用于旧协议，不冒充新版评分。

首次只复用已有产物，不发起模型请求：`runs/vsv_eval/scorecard-0908/`。其中 `legacy/` 展示旧试评换算，`protocol-v2/` 展示新版尚未评分的空值；均未覆盖历史文件。

## 7. Checkpoint-level 诊断

当前只构造 **`checkpoint_verify`：已有可运行 workspace + 原始任务输入 + 检查指令，新会话检查并按需修复**。
不提供标准修复答案、旧模型诊断或旧动作计划；不要求一定修改，不由 harness 强制检查—修改循环。
旧 `verify_only` 和同上下文 resume 脚本保留为历史诊断，本轮不使用。

**准入条件：** 检查前的代码可按 manifest/blob、快照或已审核的应用代码重建恢复；
使用原有 `start.sh`，或轨迹中已成功执行的启动命令，在独立容器启动后能访问实际页面。
不再按 `start.sh` 文件是否存在筛选。缺可核验代码或启动未确认的不能进入正式测试；
不从最终版本补脚本，也不因已知有错误或后来修好才选择样本。可保留本来已正确的实现。

**构造步骤（09-08 已实现）：**

1. 复用现有 episode 和调用分组，定位检查动作前的 workspace hash；使用完整 workspace 事件，不忽略 README 等文件变化。
2. 恢复并核验文件，保留原始 PRD/需求/原型/resources；去除已记录的浏览器截图和会话日志，同一输入代码去重。旧轨迹的重建版本须显式绑定检查事件、审核中间调用并固定来源哈希，标注 `audited_application`，不冒充完整历史 workspace。原轨迹不修改。
3. 原始 inference prompt 后追加 [`checkpoint_verify.txt`](../prompts/vision2web/checkpoint_verify.txt)，不叠加旧 guided prompt。只给新会话 prompt 和 workspace，不传历史轨迹、workflow、探测截图或评分。
4. 在独立 CPU 容器执行原启动命令和工作目录，并保存启动截图，确认可启动后才准入；随后另开新容器/会话推理，不能从启动探测的修改后目录开始。此处是数据筛选，不是 Agent 行为。
5. 新轨迹继续使用相同的 Test → Visual Judgment → Safe Repair 和 T/C/J/R；不复用旧轨迹的判断标签或 repair 事件编号。与完整开发轨迹分开统计。

实现入口：`scripts/vision2web/build_checkpoint_tasks.py build/validate`；主逻辑
`src/multimodalcode/vsv_eval/checkpoint_tasks.py`。`prepare_agent_case()` 核验启动结果后准备新会话输入，
可交给现有 Claude Code/OpenHands runner；正式批量推理尚未接上或启动。
样本、命令与真实启动检查结果见 [Checkpoint 构造记录](checkpoint_verify_construction.md)。
09-08 已新增经典 Opus/Claude Code 的 V1、V2、V3 三个输入（检查动作 249、272、310 前），
均无需 `start.sh`，通过原 `node server.js` 启动；3/3 CPU 启动与截图检查通过。
加上已构造的 Kimi 版本，共 4 个可启动 checkpoint，但都属于同一 SmartRecruiters 任务，
只能作流程诊断，不能作为 4 个独立 benchmark case；后续划分按原任务分组。尚未做新模型推理。

### 7.1 可选分析：整段验证接手

`verification_handoff` 是补充分析，**不作为主实验或优先扩展方向**。
固定 A 的 checkpoint，让另一会话中的 A/B 完整检查、判断并按需局部修复；不在运行时拆成三个角色。
需求和原型作为判断依据，另给“仅进行本轮检查、返回结果而非完成整题”的指令。
两组使用相同输入、工具、提示和预算；Test / Visual Judgment / Safe Repair 仍是事后评分维度。

09-08 已添加独立 prompt、`build_checkpoint_tasks.py handoff` 入口，复用现有 Claude Code / 原生浏览器 OpenHands runner 和记录器。
旧 checkpoint 不改写，不传旧思考/诊断；仅该协议允许无 `start.sh` 正常结束，原 benchmark 交付检查不变。
当前只运行 B 的独立会话，输出完整轨迹及代码版本；**未实现 A 原会话的自动暂停/续接，也未运行真实 A/B 模型比较**。
本地测试覆盖参数传递、独立副本、正常结束与原模式回归；执行命令见 [构造记录](checkpoint_verify_construction.md#可选验证接手入口)。

## 8. 下一步最小工作

1. **评分补齐与 AI 抽查已完成；独立人工校准待完成。** 经典轨迹新评分、另外两条轨迹及 11 个判断点复核见[本轮记录](vsv_scoring_calibration_20260908.md)。修正截图来源、原型配对、Judge 输入过长、Test 覆盖边界及纯文本判断误入 J；保留自动分数与复核分歧。下一步先裁定 C 的 `1.0/4.1/5.0` 与宽泛视觉判断口径，不能报告人工一致率。Safe Repair 仍须显式绑定版本、目标和相关回归，不能宣称任意轨迹已能自动评分。
2. **再扩大输入而非模块。** 建议从已冻结 20 题中先选 6 个不同网站（L1/L2/L3 = 2/3/1），按预先约定的代码可恢复、真实启动成功条件构造 checkpoint；保留已有正确实现，不按已知缺陷或后来是否修好挑样本。记录未准入原因。L3 还需冻结数据库/初始化状态，只有源码可恢复不够。
3. **做最小配对实验。** 一个可稳定运行的开源模型与一个强模型，使用相同 checkpoint、工具和预算；同一会话自己检查、判断、按需修改，复用三阶段评分。轨迹级测自然发生的检查，checkpoint 级测明确要求后的能力；按原网站分组统计，不把同站多个版本当独立任务。扩大到 20 题前先确认分数可解释。

第 1 项已完成补评与 AI 抽查，人工校准仍待完成；第 2–3 项仍待执行。本轮仅复用现有服务调用 Judge，不新增 Agent 生成、GPU 部署或训练任务。全量生成与其官方评分另行记录，不作为评分机制校准的前置依赖。Guided prompt 和跨模型接手保留为补充分析，暂不扩展训练或多角色系统。

若要声称**图像本身**带来增益，还需同 checkpoint 的“截图 + 文本状态 / 仅文本状态”配对对照；仅凭看图后编辑不能推导视觉因果贡献。否则论文先限定为对多模态工具条件下验证行为的分析。

## 9. Benchmark 选择（09-08 调研与建议）

目前不换 benchmark，也不删 Vision2Web 的任何 Level。先用 checkpoint 去掉从零开发负担，再判断验证能力是否仍触底；该测试是自建的 checkpoint 诊断，不冒充官方原始任务成绩。

| 已发表/公开研究的任务设置 | 对本研究的用途与限制 |
| --- | --- |
| [Vision2Web](https://arxiv.org/html/2603.26648v1)：L1 静态页面、L2 交互前端、L3 全栈 | 论文统计 L2/L3 平均有 7.5/28.2 个测试用例，L3 确实更复杂。建议 L2 优先分析机制，L1 作为视觉检查对照，L3 保留作复杂任务验证。L1 无官方功能 workflow，功能覆盖/回归不适用时记 N/A，不填零。 |
| [FronTalk](https://github.com/shirley-wu/frontalk)：100 段对话、每段 10 轮 | 适合检验需求演化中的功能保持；增加多轮与用户模拟因素，不保证比 Vision2Web 简单。当前本地仍为官方逐轮代码生成，不能直接当作现成 Claude Code 工具会话。 |
| [Design2Code](https://github.com/NoviScl/Design2Code)：484 个截图到网页任务，含 self-revision 基线 | 适合静态视觉修改诊断，但不能替代交互 Test 和功能回归评测；仅作候选，不加入当前范围。 |
| [WebGen-Agent / WebGen-Bench](https://arxiv.org/html/2509.22644v1)：101 个网站生成任务、647 个功能用例 | 相近的执行反馈研究使用该设置；从文字需求生成可交互网页。它的方法使用独立视觉/GUI 反馈角色，不能照搬为单 policy 自验证；是否更容易须实际对照，不预设。 |

结论：上述相近工作并未采用同一个“自验证标准 benchmark”；静态重建、交互开发和多轮编辑各测不同能力。优先沿用现有 Vision2Web 分层及 FronTalk 补充范围。只有多网站 checkpoint pilot 仍无法产生可评行为、且原因确属任务复杂度时，再讨论新增更简单任务；不因当前 timeout 直接换数据集。
