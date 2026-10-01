# VSV 三阶段评测：学术审计与集成

> 下文保留首轮审计和历史分数。09-07 后续已修订联合 Test 覆盖、跨片段视觉证据和配置化 Safe Repair；当前实现与测试见 [协议修订记录](vsv_protocol_v2_implementation.md)。旧 11 处视觉标签不代表新版完整定位结果。

更新：2026-09-07。依据实际代码、输入包、原始轨迹与重放结果；新增一次不改代码的浏览器滚动对照，没有新增模型实验。

## 1. 结论

**Test → Visual Judgment → Safe Repair 的划分可以保留。现在是可运行的诊断 pilot，还不是经过验证的论文评测协议。** 主要缺口在测量有效性与实验控制，不在于少几个模块。本文档不保证投稿录用。

三个问题分别是：**检查是否合理；对所见反馈的判断是否有依据；修改是否解决目标且未破坏已测功能。** 它们不是三个可直接相加的分数，也不应由第四个模型再决定一次“总正确性”。

## 2. 需要补的关键漏洞

| 问题 | 当前直接证据 / 对结论的影响 | 最小补救 |
|---|---|---|
| **视觉相关性不等于视觉因果作用** | 截图入模、随后编辑、重放改善，只证明时间关联；DOM、代码检查和额外推理也可能解释改善 | 轨迹级只报告“视觉证据后的行为”。要声称视觉带来增益，在同一 checkpoint、工具与预算下比较图像+文本反馈与文本反馈；单独控制多一次继续思考的收益 |
| **选样与切分偏差** | 目前主要在一条较好的历史 guided 轨迹上调规则；5 个修复目标是开发标注。307 提及 stray brandmark，却因当前片段无新图而未进入视觉判断候选 | episode 是分析边界，不是模型上下文重置。允许回溯真实先前图像并核对版本；先人工审计是否漏判。该任务留作开发集，留出任务报告区间/判断定位 precision、recall，并保留失败和无检查轨迹 |
| **Judge 尚未可靠，且测量对象混杂** | 254 用原型的白色字标推断实际素材本身为白色；346 用 hero 区域支持 testimonial 判断。当前 partial 还混合“有错”与“解释缺证据” | 首先盲审这 11 处并建立独立留出标注；报告混淆矩阵/一致性。下一版将“可见现象判断”作为主标签，原因解释仅附证据支持状态；不把解释缺证据自动算成视觉判断错误。旧标签不回写 |
| **修复的对照与安全范围有限** | V0–V3 是审计区间重建。实测 V0 不改代码、只滚动，5 处隐藏内容就显示；原 reveal 修复测的是特定截图条件。轮播变化也可能由时间触发。5 组探针不能证明全面无回归 | 区分截图呈现改善与正常用户操作故障；冻结前后 reset/素材/启动条件，加入少量重复重放及不点击对照。保留必需内容、限定已测回归范围，不把观察条件造成的差异一概算代码故障 |
| **分母与相关性** | 12 组调用、11 处判断、5 次目标修复来自同一个任务；Gartner 还包含两次尝试。直接合并会夸大样本量；只看有明确判断的片段会漏掉沉默/未检查行为 | 同时报告发生率和条件正确率；无检查的覆盖为 0，未表达判断/未尝试修复不填正确率。正式比较按任务聚合，按任务重采样置信区间，不把每张截图当独立样本 |
| **workflow 和官方分数的含义** | 官方 workflow 是有限注释，不是穷尽需求；覆盖某项不等于该功能通过。当前 Qwen Judge/自定义重放不是官方功能或视觉评分 | 合理性优先对齐原始任务，workflow 仅作独立覆盖参照；未被标注但合理的检查不能判无效。保留独立官方最终分数，冻结 evaluator revision、Judge 和配置 |

方法依据：LLM Judge 的位置、长度等偏差及人工验证需求已有实证，不能把聊天任务的验证结果直接迁移为本评测的可靠性保证。[MT-Bench](https://arxiv.org/abs/2306.05685)；同模型自评也有自偏好风险，换模型只能作为核验设计，不能代替人工校准。[Self-preference](https://arxiv.org/abs/2404.13076)

**滚动对照的直接证据：** `runs/vsv_eval/audit-controls-0907/result.json`；同一 V0、1440×900、7 个 reveal 元素中，隐藏数从 5 变为 0，无 runtime error。`before-scroll.png` 与 `after-scroll.png` 可看到指标区及 Winston 内容恢复。代码没有变化，控制脚本在同目录。原目标明确要求 full-page capture，故不暗改旧的“fixed”标签；但论文只能称其为该观察条件下的改善，不能扩展为自然浏览故障被修复。

执行证据优于仅凭代码猜测交互效果，与 [The Verification Horizon](https://arxiv.org/abs/2606.26300) 的研究方向一致，但它并不证明我们当前断言和 Judge 已有效。官方 [Vision2Web](https://github.com/zai-org/Vision2Web) 的功能/视觉评分与这里的轨迹诊断应分开报告。小样本只报点估计的问题可参考 [Statistical Precipice](https://arxiv.org/abs/2108.13264)；本文建议按任务重采样是对当前相关数据结构的设计选择。

## 3. 去掉冗余，保留必要证据

- **不再默认使用第四个总结 Judge。** 三阶段结果由规则汇总；文字总结不能覆盖分项或形成额外模型总分。
- **Test 不重复启动浏览器验证可执行性。** 复用原工具反馈；执行失败不自动等于检查目的不合理。当前 247 是执行失败，272 是部分反馈。
- **重放仅用于 Safe Repair 的前后对照与相关回归。** 同一版本/检查的产物共享；不用另一个 GUI Agent 自动改动作后冒称原动作重放。
- **不重切原 episode、不复制全部 events。** 集成结果引用已有组编号、判断事件和修复目标，原始证据仍在原位置。

## 4. 最小统计协议

| 阶段 | 报告什么 | 分母和边界 |
|---|---|---|
| Test | 执行状态；合理性；完整/部分 workflow 覆盖 | 合理性分母为获得有效 Judge 标签的调用组，并列出未评估数；覆盖按本任务唯一官方条目 ID 去重，不因重查增加覆盖 |
| Visual Judgment | 四档分布；可判定判断中的严格正确比例；可判定覆盖 | API/格式失败与语义证据不足分开。只测已表达判断是否被当时证据支持，不称为所有视觉错误的发现率 |
| Safe Repair | 目标前后状态；此前通过的已测功能是否回归 | 修复率条件为修改前真实失败且前后可判；原本正确不计修复成功。目标次数、代码区间数、任务数分别记录 |

三阶段不能互相替代：合理检查可以发现错误页面；正确诊断未必产生有效补丁；补丁成功也不能反推之前的解释正确。Visual Judgment 只看判断前证据，Safe Repair 才可使用后续版本。模型没显式表达判断时留作未表达，不靠编辑动作补造其想法。

正式实验至少并列给出：任务级主动检查发生率、上述条件性诊断、最终官方效果；不只展示成功片段。Test 的 workflow 完整覆盖率不是视觉外观覆盖率。

## 5. 本次实际集成与修正

统一入口：`scripts/vision2web/evaluate_trajectory.py`；实现：`src/multimodalcode/vsv_eval/pipeline.py`。

```text
同一原始轨迹与任务
  ├── Test：原调用组 → 执行事实 / 原需求合理性 / workflow 覆盖
  ├── Visual Judgment：真实图像反馈 → 可见判断 → 独立 VL Judge
  └── Safe Repair：已审计代码版本 → 同条件重放 → 目标及相关回归
                          ↓
                scores.json + manifest.json + index.html
```

已修复：

- 旧组合入口读取过时 repair 字段，导致漏标检查后编辑；现在读取当前 parser 字段，但明确因果联系仍未判定。
- 去掉从 `visual_judgment.actual_status == correct` / workflow 对齐推断功能已通过的逻辑；只有执行通过的 before 基线可用于回归判断。
- 拒绝用缺少可比 before failure 的 replay 或单独截图意见补成“修复成功”；缺少基线不能自动填安全。
- Judge 技术失败不再混入“证据不足”的语义标签；无检查的已提取 workflow 覆盖为 0，不虚造合理性准确率。
- 新入口校验来源、episode 集合、判断原话/时间边界、代码哈希与重放规范，保留各阶段分母和证据索引；不同任务、抽评分集混入全轨迹会报错。

**边界：Test/Visual Judgment 可复用到已有标准化轨迹；Safe Repair 当前只实测支持这条 SmartRecruiters 的 Edit-only 审计区间。** 其他任务缺少重建/运行适配时标为缺失，不自动生成伪分数。新增入口完成的是离线评测编排，不改变 coding agent 的行为。

所有导入证据在集成时记录 SHA256；旧 Test/Visual 文件未在最初评分时冻结 source hash，因此不能声称追溯证明完整历史环境。当前目录不是 Git worktree，未修改官方 evaluator 或原始产物。

复用已完成结果，不调用 API、不重放浏览器：

```bash
cd /data/miyapeng/mmcode/MultimodalCode
conda run -n mmcode python scripts/vision2web/evaluate_trajectory.py \
  --config configs/vision2web/vsv_smartrecruiters_pipeline.json \
  --output-dir runs/vsv_eval/smartrecruiters-integrated-reproduce
```

输出目录必须新建且为空。需要重新运行某阶段时，另存一份配置并移除该阶段的 `scores` 字段，加 `--run-missing`；会沿用已列明的 profile 调用原阶段 CLI。显式指定但丢失的历史文件会报错，不自动重跑。Safe Repair 仍使用其现有 Node/Playwright 环境；本轮未新增依赖。

当前 pilot 复用结果：1 个任务、3 个片段；Test 12 组（10 成功/1 失败/1 部分）、12 组合理、workflow 3/13 完整+3 部分；Visual Judgment 11 处（自动 8 正确/3 部分正确）；Safe Repair 4 版本、3 区间、5 目标（3 修好/2 未修好），5 组探针未观察到回归。**这些不是人工真值、独立样本或官方分数。**

集成结果目录：`runs/vsv_eval/smartrecruiters-integrated-0907-final/`。回归测试覆盖 53 项，包括原阶段、缺失结果、跨任务/子集混用、无检查轨迹，以及复用真实三阶段产物；未启动推理服务或 GPU 任务。

变更范围：新增 `vsv_eval/pipeline.py`、`scripts/vision2web/evaluate_trajectory.py`、`configs/vision2web/vsv_smartrecruiters_pipeline.json` 和 `tests/test_vsv_pipeline.py`；调整现有 `vsv_eval/{scoring,checks,visual_judgment}.py`、`scripts/vision2web/{score_vsv,score_repair_pilot}.py`、`configs/vision2web/vsv_scoring.json` 及对应测试；同步本审计和主设计文档。历史报告、Judge 输出、截图、代码版本和官方评测目录均保留。

## 6. 下一步顺序

1. 先完成留出轨迹的切分审计，以及视觉 Judge 人工校准；冻结评分口径后再扩批量。现有四档历史分数保留，不因本次审计暗改。
2. 补稳定的前后重放和受影响视觉区域回归，冻结 task-level 标注。当前 20-case 子集用于 pilot，不单凭一条成功轨迹得泛化结论。
3. 在匹配 checkpoint 和预算的实验中区分“工具/提示触发检查”“额外计算收益”“视觉证据增益”。针对 CVPR，第三项比再增加一个评分模块更重要。

论文贡献宜聚焦：**模型能否把动作产生的视觉证据转化为正确判断和可靠修复，以及失效发生在哪一环。** 这套评分系统是支撑结论的测量工具，本身还不能被称为已验证的新方法贡献。
