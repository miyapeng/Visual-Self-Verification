# MultimodalCode：Visual Self-Verification 项目计划

更新：2026-09-17。目标：**CVPR 2027**。本节是当前研究方向与进度的统一入口；后面的早期方案仅留作历史，不是待自动执行的任务。

## 当前核心问题

**Coding agent 能否通过观察自己运行出来的页面，发现问题、修改代码，并确认修改有效？**

当前定位为“现象分析 + 能力诊断 + 轻量改进”，不把大规模新 benchmark、SFT 或 RL 作为论文必须完成的前提。上下文管理已不是核心方向。浏览器只执行与记录，模型自行决定何时观察、修改、复查或结束，不引入强制循环、独立 planner/judge/repair agent。离线评分 Judge 不参与被测 Agent 的决策。

研究需要分开回答三件事：平时会不会检查；提供正确工具使用说明后能不能检查；视觉反馈究竟能否带来更好的结果。不能把“看图后编辑”直接写成“视觉反馈导致改进”。

## 研究范围与实验组织

- **Vision2Web 全部 L1/L2/L3 保留**，是当前主要实验来源；已冻结 20 题子集，L1/L2/L3 = 6/8/6。
- FronTalk 保留为后续多轮需求场景；SWE-bench Multimodal 保留为次要迁移候选，当前不扩展、不据它宣称主动视觉发现能力。是否纳入论文由后续证据决定。
- InteractWeb-Bench 已退出当前研究，不自动恢复；其他已下载 benchmark 不自动成为论文实验。
- **轨迹级**：复用完整开发轨迹，统计浏览、图片实际输入、后续编辑及复查。
- **检查点级**：从同一可运行代码版本开启新会话，保留原需求与原型，只改变检查指导；不提供错误答案或历史修复。

主基线为 Claude Code `official`，另保留 OpenHands `browser_enabled` 作框架对照。OpenHands 只用完整原生 BrowserToolSet，不恢复自定义批量接口。`guided_vsv` 是能力引导诊断，不与原始条件混合统计；官方 prompt 自身已有测试要求，不称为“完全没有验证提示”。

## 已完成什么

| 工作 | 当前状态与证据 |
|---|---|
| 推理与记录 | 已接入 Claude Code/OpenHands，记录原始工具事件、截图、程序版本和 HTML 展示；旧轨迹并非任意节点都可恢复 |
| 片段切分 | 已能提取视觉/交互功能检查片段；批量动作不强拆为多个 episode；仍需抽样标注检验漏检与误检 |
| 三阶段评分 | Test / Visual Judgment / Safe Repair 已有统一入口与单轨迹集成；尚未完成独立人工校准 |
| 固定代码题 | SmartRecruiters 已构造并启动验证 4 个输入（Opus 3、Kimi 1）；不是 4 个独立网站 |
| 全量行为审计 | Qwen3.5-9B × Claude Code official：193 任务，192 正常结束、1 failed；这是生成状态，不是官方通过率 |
| 排障对照 | Classic Clashes 已跑工具通路与 3 个续写条件；命令示例组出现局部自检闭环，但最终 timeout |

经典 SmartRecruiters 轨迹已有 T/C/J/R = 83.33/30.77/81.82/60 的**开发试评分**，不是官方分、人工金标或模型总体能力。T/C 同属 Test，不是四个独立评测阶段。具体定义与分母见[评分校准记录](reports/vsv_scoring_calibration_20260908.md)。

## 目前有哪些真实发现

**1. 原始条件下很少使用浏览器。** Qwen3.5-9B 的 193 个任务中，仅 1 个打开自己的应用，0 个实际读取应用截图；唯一浏览器案例基于 console/页面文字修改并复查。142 个任务读取过原型或素材，不能算视觉自检。135 个任务有运行期 HTTP 200，63 个出现过依赖/网络错误；环境因素仍需分层处理。[逐任务审计](reports/qwen35_claude_official_self_check_audit_20260912.md)

**2. 工具可用，不代表模型会正确调用。** 同一已有代码、Qwen3.5-9B、Claude Code，三个续写条件如下；这不是重新跑三次 official 全程生成。

| 续写条件 | 应用图片实际输入 | 后续行为 | 结束 |
|---|---:|---|---|
| 原任务 + 已有实现说明 | 0 | 看原型、改代码、curl 检查，没有浏览应用 | 正常，约 9 分钟 |
| 再加详细自检指导 | 0 | 尝试错误 Playwright 命令及安装，未获得应用图像 | 正常，约 20 分钟 |
| 再加正确命令示例 | 4 次 | 截图/Read → 结合请求及文件检查定位图片路径问题 → 编辑 → 截图/Read 复查 | 1200 秒 timeout |

独立工具检查已证实原生浏览器能截图，Claude Read 会返回 image，Qwen 有后续图像描述。命令示例组后来仍出现工具错误和反复尝试，后半程浏览器失败的根因未完整审计。两次临时单卡实验结束时均已确认服务释放。[实验记录](reports/vsv_confounder_probe_20260912.md)

**允许的结论**：这个样例支持“正确工具说明可以引出局部自检行为”，工具使用是可见障碍之一。
**不允许的结论**：所有模型都不会自检、prompt 已解决问题、视觉反馈一定提升质量。当前主图问题也可用文件/网络文字证据发现，尚未证明视觉的独立收益；单样例单次对照不构成稳定因果结论。

## 评测保留三个环节

1. **Test**：规则检查动作是否实际执行；Judge 对照原需求判断检查是否合理，离线匹配 workflow 并去重计算覆盖。workflow 不是合理检查的唯一白名单。
2. **Visual Judgment**：VL Judge 只依据判断发生前的证据，检查模型可见的判断是否有依据；没有表达或证据不足单列，不臆测隐藏思考。
3. **Safe Repair**：仅评真实修复尝试，在前后版本执行目标检查，并在新版本检查相关已通过功能。代码审阅只提示风险，不决定修复成功；有限回归测试不等于证明所有功能安全。

三项分开报告分数、可评估数和缺失原因，不强拼一个总分。没有看图后的修复样本时，条件修复能力是 N/A，而不是 0%。最终功能/视觉质量另用冻结官方 evaluator，不能用行为分替代。详见[两层评测协议](reports/visual_self_verification_two_level_evaluation_design.md)。

## 下一步最小工作（按顺序）

1. 审计命令示例组后半程失败；分清错误命令、应用/浏览器故障和预算截断。先保证工具接口稳定，不继续堆提示或模块。
2. 从已有 20 题中选择 3–5 个可启动、不同网站的代码版本，复测明确命令能否稳定引出检查；保存失败样例，不只选有效结果。
3. 在相同代码、工具与预算下比较允许图像反馈和仅文字运行反馈，另以等预算继续修改作对照；测前后质量和回退。需要包含不能只靠 404/异常日志解释的视觉问题。
4. 人工核对少量片段切分、视觉判断与修复标签，报告一致性和争议；再扩模型与任务，不先扩全量或开始训练。

CVPR 论文需要补齐的核心证据是：**哪些错误需要视觉证据、模型在哪一步失效、轻量干预能否稳定改善最终产物而非仅增加浏览器调用。** 目前已有工程与初步现象，还没有完整的论文主实验结论。

## 文档分工

- 本文：当前方向、进度、结论边界与下一步。
- [两层评测设计](reports/visual_self_verification_two_level_evaluation_design.md)：详细协议和实现历史。
- [研究目录](reports/README.md)：代码、数据、结果入口。
- [检查点构造](reports/checkpoint_verify_construction.md)：从轨迹恢复可运行输入的方法。

本次仅整理文档，未启动实验、修改实现或官方 evaluator，也未删除历史结果。当前项目目录不是 Git worktree。

---

# 早期讨论存档（非当前执行计划）

以下内容保留演进背景；出现的扩规模、multi-agent、强制 gate 或训练建议均未自动获批，当前范围与下一步以上文为准。

## Do Multimodal Coding Agents Verify What They Build?

> 原目标会议：ICLR 2027（已调整为 CVPR 2027）  
> 当前论文定位：**现象发现 + 系统诊断 + 轻量干预**  
> 当前状态：先验证研究现象，再决定是否建设完整 benchmark 或训练模型。

> 2026-08-27 更新：完整轨迹评测与固定检查点评测的工作设计已记录在
> [`reports/visual_self_verification_two_level_evaluation_design.md`](reports/visual_self_verification_two_level_evaluation_design.md)。
> 该文档仍是待逐项审查的工作稿，后续以验证结果为依据再合并进本计划。

---

## 1. 核心研究问题

现代多模态 coding agent 已经具备：

- 理解文本需求和视觉原型的能力；
- 阅读、生成和修改代码的能力；
- 使用终端、浏览器和截图工具的能力。

但它们在完成 Web 开发任务时，是否会像人类开发者一样，主动形成下面的闭环，仍不清楚：

```text
实现代码
→ 运行网站
→ 查看自己的实际页面
→ 对照视觉目标
→ 定位问题
→ 修改代码
→ 再次查看
→ 决定继续修改或提交
```

本文研究的不是“外部 judge 能否检查网站”，而是：

> **多模态 coding agent 是否会在完整开发过程中，自主检查并改进自己构建出来的视觉结果？**

---

## 2. 研究范围

### 2.1 主要对象

- 多模态 coding agent；
- Web 开发任务；
- 包含视觉目标的任务，例如原型图、参考截图或设计图；
- 静态网页、交互式前端和复杂 Web 应用。

### 2.2 Self-verification 的边界

允许 agent 使用外部工具：

- 浏览器；
- 页面截图；
- 终端；
- DevTools 或页面结构；
- 文件读取和代码编辑工具。

但在纯 self-verification 条件中：

- 不由外部模型告诉 agent 哪里错了；
- 不向 agent提供隐藏评测分数；
- 不由固定 harness 强制执行完整的“生成—检查—修改”流程；
- 是否检查、检查什么、是否修改以及是否复查，都由 coding policy 决定。

Multi-agent verifier 可以作为一种**解决方法或实验条件**，但不属于纯 self-verification 的定义。

---

## 3. 问题建模

一个任务表示为：

\[
x=(u,\mathcal I,P_0),
\]

其中：

- \(u\)：自然语言需求；
- \(\mathcal I\)：视觉输入，例如原型图；
- \(P_0\)：初始代码工作区。

Agent 在开发过程中不断执行动作并获得观察：

\[
\tau=(x,o_0,a_0,o_1,\ldots,a_{T-1},o_T).
\]

其中动作可以是：

- 读取或搜索代码；
- 修改文件；
- 执行终端命令；
- 启动网站；
- 操作浏览器；
- 获取截图；
- 结束任务。

### 3.1 视觉执行证据

只有由当前程序真实运行产生，并且实际进入模型上下文的视觉结果，才属于视觉执行证据，例如：

- 当前首页截图；
- 打开弹窗后的截图；
- 提交表单后的页面状态；
- 移动端或平板端的渲染结果。

用户提供的原型图只是视觉目标，不属于当前程序的执行证据。

### 3.2 Self-verification episode

当 coding policy 主动获取当前程序的视觉执行证据时，视为一次候选 self-verification episode 的开始。

该 episode 后续可以包括：

- 查看更多页面状态；
- 对照原型；
- 阅读相关代码；
- 修改实现；
- 决定无需修改；
- 决定结束任务。

如果 agent 修改代码后，再次观察更新后的页面，则形成一个更完整的闭环：

```text
查看当前版本
→ 修改代码
→ 查看新版本
```

---

## 4. 论文要回答的核心问题

只保留以下四个主问题，避免过度拆分。

### RQ1：Agent 会不会主动检查？

- 是否打开并查看自己运行出来的网站？
- 是否真正让截图进入模型上下文？
- 第一次视觉检查发生在开发过程的什么位置？

### RQ2：Agent 检查得是否完整？

- 是否只看首页？
- 是否检查关键路由、弹窗、表单、提交结果和响应式状态？
- 检查的页面状态是否覆盖任务中的主要视觉要求？

### RQ3：Agent 能否利用看到的结果？

- 看完后是否进行了相关修改？
- 修改是否真的改善视觉结果？
- 修改后是否再次检查？
- 视觉修复是否破坏原有功能？

### RQ4：简单干预能恢复多少能力？

比较：

- 自然开发；
- 同一个 agent 被提醒提交前检查；
- 同模型的新上下文 reviewer；
- multi-agent verifier；
- 更强的外部 critic。

---

## 5. 当前待验证的假设

这些是实验假设，不应提前写成 findings。

### H1：能力—行为差距

同一个模型在被明确要求检查时能够改善页面，但在自然开发轨迹中不会主动使用这项能力。

### H2：任务越复杂，自验证越弱

从静态页面到交互式前端，再到长流程 Web 应用，agent 更容易忽略视觉检查或只检查少量状态。

### H3：问题不一定只是视觉能力不足

如果同模型新上下文 reviewer 明显优于原上下文自检，可能说明已有开发上下文造成确认偏差或注意力负担。

### H4：视觉改进可能导致功能退化

模型为了匹配原型修改组件后，可能破坏按钮、路由、表单或数据状态，因此视觉分数和功能分数必须同时评测。

### H5：不同规模模型的瓶颈不同

强模型可能主要缺少主动触发和复查；较弱模型可能在视觉理解、代码定位和修复上同时存在问题。

---

## 6. 数据与 benchmark 方案

### 6.1 第一阶段：不从零造大 benchmark

先从已有 Web benchmark 中抽取任务，优先使用包含以下材料的数据：

- 文本需求或 PRD；
- 视觉原型；
- 资源文件；
- 可运行代码环境；
- 独立功能检查；
- 静态、交互式和复杂任务层级。

Vision2Web 可以作为第一阶段的主要任务来源。其他静态 UI 或迭代修复 benchmark 可以作为补充，但最终需统一任务格式。

### 6.2 Pilot 规模

先选择约 12–20 个任务：

- 4–6 个静态页面任务；
- 4–6 个交互式前端任务；
- 4–6 个复杂或全栈任务。

Pilot 的目标不是得出最终 leaderboard，而是确认：

1. 现象是否存在；
2. 轨迹能否完整记录；
3. 自然条件和提醒自检条件是否有明显差异；
4. 视觉和功能评测是否稳定。

### 6.3 后续 benchmark 规模

如果 pilot 成立，再扩展到约 60–90 个任务，并平衡：

- 任务复杂度；
- 页面数量；
- UI 状态数量；
- 是否包含后端；
- 是否需要跨页面数据；
- 响应式要求；
- 原型风格和业务类型。

新 benchmark 的主要贡献不是重新制作网页，而是增加：

- 完整 coding trajectory；
- 程序版本记录；
- 模型实际看到的截图；
- self-verification episode 标注；
- 自然条件与干预条件的配对结果；
- 新的行为评测指标。

---

## 7. 模型选择

### Pilot

先使用两个模型：

1. 一个较强的闭源多模态 coding model；
2. 一个较强的开源多模态模型。

### 完整实验

后续扩展到约 6–8 个模型：

- 2–3 个强闭源模型；
- 3–4 个开源模型；
- 尽量包含同系列不同规模；
- 如有合适模型，可包含 coding-oriented multimodal model。

### 控制变量

主实验应尽量统一：

- Agent harness；
- 工具权限；
- System prompt；
- Token 和时间预算；
- 浏览器环境；
- 任务输入；
- 最终评测器。

否则无法区分差异来自模型还是框架。

---

## 8. 核心实验条件

实际条件按 scaffold 定义；完整说明见两层评测设计工作稿。

### A. Official

保持原始 Vision2Web prompt 和官方 scaffold。需要注意，官方 prompt 本身已经宽泛要求测试部署、视觉和交互，因此该条件测量的是 **official-prompt behavior**，不能表述成完全无验证提示的 spontaneous condition。

### B. Browser-Enabled（仅 OpenHands）

保持官方 prompt 字节一致，仅启用完整原生 BrowserToolSet。原先功能级批量执行接口已停用，不属于当前条件。该条件与 Official 的差异只用于测量 browser affordance。

Claude Code 官方 scaffold 已经提供 `playwright-cli`，因此不设置重复的 Browser-Enabled 条件。

### C. Guided VSV

保持浏览器工具不变，在官方 prompt 后加入冻结的详细 Visual Self-Verification 流程：启动 `start.sh`、观察真实部署页面、按单个用户功能编写预期和短 action sequence、根据视觉与运行证据判断和修改、修复后复查并保护已通过功能。

OpenHands 使用 `Browser-Enabled → Guided VSV` 测 prompt 效果；Claude Code 使用 `Official → Guided VSV` 测 prompt 效果。`workflow.json`、官方 evaluator 和隐藏分数始终不可见。

### D. Same-Context / Fixed-Checkpoint Diagnostics

同上下文提醒、fresh-context verify-only 和固定 checkpoint verify-and-repair 不与上述完整轨迹条件混合统计。它们用于细粒度区分主动触发、检查设计、视觉判断和修复能力。

### E. Same-Model Fresh-Context Reviewer

使用相同模型权重，但开启新的上下文，让它检查原模型完成的实现。

用于区分：

- 模型能力不足；
- 原上下文造成的确认偏差或信息负担。

### F. Multi-Agent Verifier

一个独立 verifier agent 检查页面并向 builder 提供反馈。

这是合理的解决方法，也是和纯 self-verification 的重要对照。

### G. Strong External Critic

可作为上界参考，不必成为第一阶段的重点。

---

## 9. 评测方法

### 9.1 最终质量

每个最终程序至少评测：

- Visual Score：视觉是否接近原型；
- Functional Score：功能是否正确；
- 额外成本：token、时间、浏览器调用和修改次数。

最终 evaluator 只在轨迹结束后使用，不能把结果反馈给 coding agent。

### 9.2 行为指标

主文只保留四个核心维度：

#### Initiation

Agent 是否主动查看自己运行出来的视觉结果。

#### Coverage

Agent 是否检查了重要页面和 UI 状态。

#### Utilization

Agent 是否根据视觉证据采取了有效的后续动作。

#### Re-verification

Agent 修改之后是否再次检查更新结果。

可以额外记录：

- 第一次检查出现的位置；
- 查看后修改的比例；
- 完整“查看—修改—复查”闭环比例；
- 视觉提升与功能退化的关系。

### 9.3 人工标注

自动日志只能确定：

- 模型是否获取了截图；
- 截图是否进入上下文；
- 看图后是否修改了代码。

但模型打开浏览器也可能只是为了导航或完成任务。因此应人工标注一小部分候选 episode：

- visual verification；
- 普通任务操作；
- 运行错误调试；
- 偶然观察；
- 无法判断。

先通过小规模标注确认定义是否清晰，再考虑自动分类。

---

## 10. 最简单的改进方法

### Verify-Before-Commit（早期候选，当前不采用）

当 agent 准备结束任务时，增加一个提交门：

1. 要求 agent 自己选择需要检查的页面状态；
2. 获取当前页面截图；
3. 对照视觉要求；
4. 自己决定提交或继续修改；
5. 如果修改，至少再检查一次；
6. 最后运行功能测试，避免视觉修复破坏功能。

需要比较三个版本：

- Prompt：只在 system prompt 中要求自检；
- Gate：结束前必须进行一次检查决策；
- Verifier Agent：由独立 agent 执行检查。

该方法的目的不是声称彻底解决问题，而是验证：

> 如果只改变验证策略就能明显改善结果，那么自然开发失败并不完全来自模型缺少视觉能力。

---

## 11. 第一阶段的具体实施顺序

### 第 1 步：固定研究定义

完成并冻结：

- self-verification 定义；
- 视觉执行证据定义；
- 自然条件和干预条件；
- 主指标。

### 第 2 步：选 Pilot 任务

从现有 benchmark 中选 12–20 个任务，并按静态、交互和复杂任务分层。

### 第 3 步：选两个模型

一个强闭源模型和一个开源模型。

### 第 4 步：记录完整轨迹

每一步至少保存：

```text
task_id
run_id
model
condition
agent action
tool result
截图是否进入模型上下文
当前 URL
代码修改内容
当前程序版本
最终结束原因
```

### 第 5 步：运行 Natural 条件

让模型正常完成任务，保存第一次宣布完成时的代码和截图。

### 第 6 步：运行 Forced Self-Check 条件

从同一上下文继续，提醒同一个模型自己检查和修改。

### 第 7 步：评测前后两个版本

比较：

- 视觉质量；
- 功能质量；
- 是否主动看图；
- 是否修改；
- 是否复查；
- 额外成本。

### 第 8 步：分析 Pilot

重点回答：

1. 自然条件下模型是否主动检查？
2. 提醒后是否明显改善？
3. 改善来自哪里？
4. 是否发生功能退化？
5. 强模型和开源模型是否表现不同？

### 第 9 步：决定下一阶段

根据结果选择：

- 扩展完整 benchmark；
- 加入 fresh-context 和 multi-agent 条件；
- 设计更好的 intervention；
- 或开始构造训练数据。

---

## 12. Pilot 成功标准

满足以下条件后，再扩展大规模实验：

1. 可以完整记录和恢复 coding trajectory；
2. 能确定截图是否真实进入模型上下文；
3. 自然条件与提醒自检条件存在可测差异；
4. Visual Score 和 Functional Score 可以稳定评测；
5. 人工能够较一致地区分验证行为和普通浏览器操作。

---

## 13. 论文建议结构

```text
1. Introduction
2. Related Work
3. Method
   3.1 Problem Formulation
   3.2 Benchmark and Evaluation Protocol
   3.3 Experimental Conditions and Metrics
4. Natural Visual Self-Verification Behavior
5. Diagnosing the Verification Process
6. Simple Interventions
7. Analysis and Limitations
8. Conclusion
```

ICLR 主文篇幅有限，因此：

- 主文保留定义、实验协议、核心结果和主要分析；
- 工具实现、完整标注规则、更多样例和完整表格放附录；
- Method 第一小节保持为一个连续的 `Problem Formulation` subsection，不再拆出多个小标题。

---

## 14. 预期贡献写法

在实验完成前，先按目标贡献写，不要提前写成确定结论。

1. 提出并形式化多模态 coding agent 的视觉 self-verification 问题；
2. 构建用于观察完整 coding trajectory 的评测协议；
3. 系统比较不同模型在 initiation、coverage、utilization 和 re-verification 上的表现；
4. 通过配对干预实验区分模型能力和自然 agent 行为之间的差距；
5. 评估简单 self-check 和 multi-agent 方法是否能够改善最终视觉质量，并分析功能退化与成本。

---

## 15. 当前不要做的事情

- 不要一开始训练大模型；
- 不要立即从零制作数百个 Web 任务；
- 不要把 `workflow.json` 暴露给 coding agent；
- 不要把最终 judge 当作 self-verification；
- 不要仅凭“模型打开过浏览器”就断言它完成了验证；
- 不要提前把待验证假设写成 findings；
- 不要为了追求完全不同而排斥相关的 multi-agent 或外部反馈方法；
- 不要把论文变成过多零散问题的集合。

---

## 16. Codex 立即执行清单

建议先完成以下任务：

- [ ] 建立研究代码仓库；
- [ ] 保存当前 Problem Formulation；
- [ ] 写明 Natural 和 Forced Self-Check 两个实验条件；
- [ ] 从 Vision2Web 中选择第一批 12–20 个任务；
- [ ] 确定两个 Pilot 模型；
- [ ] 实现每一步 action、observation、截图和代码版本的日志；
- [ ] 跑通一个任务的 Natural 条件；
- [ ] 保存第一次完成时的 checkpoint；
- [ ] 在同一上下文运行 Forced Self-Check；
- [ ] 对前后版本分别计算视觉和功能结果；
- [ ] 扩展到所有 Pilot 任务；
- [ ] 汇总第一张核心表格；
- [ ] 根据 Pilot 决定是否加入 fresh-context、multi-agent 或训练实验。

---

## 17. 建议的第一张核心结果表

| Model | Task Level | Natural VS | Forced VS | ΔVS | Natural FS | Forced FS | Inspection Rate | Re-check Rate | Extra Cost |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Model A | Static |  |  |  |  |  |  |  |  |
| Model A | Interactive |  |  |  |  |  |  |  |  |
| Model A | Full-stack |  |  |  |  |  |  |  |  |
| Model B | Static |  |  |  |  |  |  |  |  |
| Model B | Interactive |  |  |  |  |  |  |  |  |
| Model B | Full-stack |  |  |  |  |  |  |  |  |

这张表首先回答最核心的问题：

> 同一个模型在自然完成后，被提醒自己检查，是否能够恢复一部分原本没有使用的视觉改进能力？
