# VSV 新协议试评分与抽查

日期：2026-09-08。范围：经典 SmartRecruiters / Opus / Claude Code 轨迹的新版三阶段评分，另抽查 Kimi 与 GLM 的同网站轨迹。没有重新生成任务、修改官方 evaluator 或新增 GPU 部署。

**状态：经典轨迹三阶段补评分完成，另两条轨迹抽查完成；独立人工校准尚未完成。** 下文直接证据复核由 AI 完成，不是人工金标，不报告人工一致率。

结果入口：[统一得分 JSON](../runs/vsv_eval/calibration-0908/integrated/scores.json)、[逐阶段 HTML](../runs/vsv_eval/calibration-0908/integrated/index.html)、[逐项复核对照](../runs/vsv_eval/calibration-0908/review_comparison.json)。三阶段执行状态均为 complete，研究状态保留 `development_pilot_uncalibrated`。

## 1. 本轮修正

| 实际问题 | 最小修正 | 验证 |
| --- | --- | --- |
| Test 将截图检查混成页面评分，或给直达截图算点击流程覆盖 | 联合 episode prompt 明确只评检查方法；截图回执不要求 Judge 再看图；只完成页面定位不算交互 workflow 子步骤 | 保留原始调用组与执行标签；补 prompt 回归测试 |
| URL 跳转被判完整覆盖，但 workflow 还要求检查目标页内容 | 明确同时核对 actions 与 validations；仅返回 URL 对“目标内容可见”的要求只能算部分覆盖 | 保留已完成但口径有问题的首轮 C，不作为最终覆盖结果 |
| 纯控制台/JSON 功能判断被定位进视觉判断分母 | 收紧定位 prompt：必须解释已观察图像或相关视觉问题；纯文本测试不算，混合判断和跨段视觉证据保留 | 原始首轮保留；新定位仅重选事件，不改模型原话或切分 |
| shell 默认变量生成的截图漏识别；素材拼图误继承最近页面 URL | 截图必须关联实际输出命令；支持 `${page:-home}`，PIL 派生图须追溯真实页面图 | Kimi 补入 385/389；GLM 应用图仅 460/591/608，素材事件原样保留 |
| 截图记录的 URL 停留在 example.com，导致 About/Pricing 等配错原型 | 优先依据截图文件名，再参考本地应用 URL；明确标记匹配依据 | 真实输入包及单测；文件名仍是启发式，不是假定可靠的页面标注 |
| 完整历史使 GLM Judge 请求超过 262K 上下文 | 原历史完整落盘；Judge 按完整事件选择证据，优先当前图片回执、被判断文字涉及的代码、近期事件；记录被省略的事件编号 | 实际文本输入约 12 万字符；缺失证据不能被判成原模型错误 |

实现：`src/multimodalcode/vsv_eval/{episodes,checks,visual_judgment}.py`；测试：`tests/test_vsv_checks.py`、`tests/test_vsv_visual_judgment.py`。项目不是 Git worktree，无法提供 Git diff；没有修改 Agent、原始轨迹或冻结的官方评分代码。

## 2. 经典轨迹

输入：`data/vision2web/vsv_eval_fixtures/frontend__smartrecruiters__claude-opus-4-8__self_verify/trajectory/run.json`。

切分保持 **232–298 / 299–315 / 341–346**；共 12 组原始调用，记录执行结果为 **10 成功、1 失败、1 部分执行**。这是所切片段内的调用统计，不是全轨迹工具成功率。

评分采用现有 Qwen3.8-27B，temperature=0、max_tokens=8192、thinking enabled / low。补跑配置将 HTTP 等待从 240 秒延长到 600 秒，不改变模型采样设置。网络超时与无效 Judge 输出单列，不算被测模型错误。最终产物无缺失评分项；旧版得分不沿用。

| 指标 | 自动试评分 | 分母说明 |
| --- | ---: | --- |
| T：成功执行且合理的检查 | **83.33**（10/12） | 原始调用组；1 次失败、1 次部分执行不计严格成功 |
| C：完整 workflow 条目覆盖 | **30.77**（4/13） | 原始 13 条含相似/重复导航条目，不等于独立需求覆盖；争议见 §5 |
| J：严格判断正确率 | **81.82**（9/11） | 9 正确、2 部分正确；不将纯文本功能测试混入 |
| R：修好且未观察到相关回归 | **60.00**（3/5） | 五个目标尝试，来自三次代码版本转换；不是五个独立任务 |

不计算加权总分，以上不是 Vision2Web 官方成绩。新版视觉判断位置为 `237/254/259/264/278/282/286/292/298/307/346`；纯控制台/JSON 的 `274/301/304/312/315` 从 J 排除，仍保留原事件及 Test。307 对此前截图的混合判断保留，不能仅按 episode 内是否有新图排除。

Safe Repair 固定四个真实代码版本、五个目标与五项回归探针。`reveal` 只度量**未滚动全页截图**的可见性：已有滚动控制显示 V0 滚动后也能显示，不能把这一项写成自然浏览时的功能缺陷。删除白框若同时删除原型要求的品牌内容，也不能算完整修复。

本轮重放完成：20 次功能检查、164 张截图；五个目标为 reveal 修好、Gartner 第一次未修好、Gartner 第二次修好、Pricing logo 修好、testimonial 品牌框未修好，即 R=3/5=60。三次版本转换的五项已测功能均无退化。源图复核中，Pricing 的 V1 字标是极淡、低对比度，V2 为清晰黑色；Judge 的“完全空白”表述过强，但其可读性改善有实际截图支持。

## 3. 两条其他轨迹的源证据抽查

| 轨迹 | 保留的连续片段 | 直接核对结果 |
| --- | --- | --- |
| Kimi-K2.6 / Claude Code / guided_vsv | 335–516 | 407 的 Careers 奖项缺图有依据，但 About Us 缺头像不成立（393 实际显示 8 张头像）；473 的奖项和 values 图已显示，撤回最初缩略图误判；“整站功能正常”的范围需人工统一口径 |
| GLM-5.3-Flash / Claude Code / official | 457–610 | 460→591→608 是真实页面检查与修改序列；中间素材拼图不算已部署页面观察；593 的留白描述有证据，待验证原因应按假设理解 |

逐项源事件、理由及 11 个判断点的复核意见见 [review_annotations.json](../runs/vsv_eval/calibration-0908/review_annotations.json)。GLM/Kimi 各选评 3 个判断点，Qwen 均给出 1 正确、2 部分正确，均无 API 缺失；不将其当作两条完整评分结果。这三条均为同一网站的 Claude Code 轨迹，不能用于跨任务/框架泛化或独立样本置信区间。

需要重点独立复核：Opus 254（截图不能证明素材存在可恢复的白色 logo）、346（白框消失与完整还原品牌内容不同）；Kimi 473、GLM 610 的“functional / very closely”属于范围不明确的宽泛判断。GLM 610 的 Judge 还把截图文件较大当作未损坏的依据，这一推断不成立。保留模型分数与复核意见两列，不人为改成一致。

## 4. 产物与复现

所有新产物在 `runs/vsv_eval/calibration-0908/`；历史结果未覆盖。最终使用 `classic-test-validated/`、`classic-visual-final/`、`classic-repair-final/`，由 `integration.json` 汇总到 `integrated/`。`classic-v2/` 的旧定位评分主动中止，已返回响应保留；`classic-test-calibrated/` 与 `classic-test-final/` 保留修订前结果，`glm-visual-audit/` 保留超上下文错误，均不进入最终得分。缓存仅在完整请求哈希相同时复用；经典轨迹原首轮仍使用未限制的长历史，与新版输入不同，因此新版视觉评分实际重新请求。`judges.json` 固定本轮 Judge 参数。

原轨迹 SHA256：`b79e6b0600907598b04070ed7067891fede37a691a989a300c75e6171833bc2c`。输入、实现与证据哈希见 `integrated/manifest.json`。111 项相关测试通过（系统 pytest，禁用无关自动插件）；统一汇总通过输入及代码版本检查。本轮各评分进程已结束，未停止原有 Qwen 推理服务。

```bash
cd /data/miyapeng/mmcode/MultimodalCode
conda activate mmcode
export VSV_PRIMARY_BASE_URL=http://10.119.255.238:18004/v1
export VSV_PRIMARY_API_KEY=EMPTY
export NO_PROXY='*' no_proxy='*'

# 只汇总本轮已保存评分，不调用模型或重放。使用未存在的输出目录。
python scripts/vision2web/evaluate_trajectory.py \
  --config runs/vsv_eval/calibration-0908/integration.json \
  --output-dir runs/vsv_eval/calibration-review-new

# 复现 Test；新目录保留每次原始响应。
python scripts/vision2web/score_vsv.py --test-only \
  --run-json data/vision2web/vsv_eval_fixtures/frontend__smartrecruiters__claude-opus-4-8__self_verify/trajectory/run.json \
  --task-root data/vision2web/vsv_eval_fixtures/frontend__smartrecruiters__claude-opus-4-8__self_verify/scorer_task_root \
  --config runs/vsv_eval/calibration-0908/judges.json \
  --output-dir runs/vsv_eval/calibration-test-new

# 如需完整重评：真实启动保存版本、重放，并调用现有 Judge。
export VSV_PLAYWRIGHT_PACKAGE=/data/miyapeng/miniconda3/envs/mmcode/lib/python3.10/site-packages/playwright/driver/package
export VSV_CHROMIUM=/data/miyapeng/mmcode/MultimodalCode/.runtime/research/playwright/chromium-1223/chrome-linux64/chrome
python scripts/vision2web/evaluate_trajectory.py \
  --config runs/vsv_eval/calibration-0908/reproduce.json \
  --output-dir runs/vsv_eval/calibration-full-new --run-missing
```

## 5. 校准边界

本轮先验证证据来源、请求可执行性、评分边界，并保留 AI 与 Judge 的分歧，不通过修改标签强求一致。独立人工仍须核对争议点，尤其是“现象正确、原因未证实”“整体很像”等宽泛判断；正式论文不能把本轮自动标签当金标。先裁定口径并人工核对，再扩到不同网站；无需新增评分阶段。

覆盖明细还有三处待裁定，未私自改自动标签：`1.0` 要求从首页进入 Pricing，但 305 实际从 Winston 进入；`4.1` 的 Judge 理由声称 URL 未变，303 实际未返回 URL；`5.0` 已执行 Company→About 的前段，却被标为 uncovered 而非 partial。因此 C 目前是**自动试评分**，不是经独立校准的最终覆盖率。
