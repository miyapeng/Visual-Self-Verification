# VSV 评测机制修订

2026-09-07。本轮只修改离线评测，不改变 Agent、不重切原片段、不调用 Judge。原始轨迹、官方 evaluator、历史分数和展示网站均保留。

## 改了什么

| 阶段 | 当前做法 | 保留的边界 |
|---|---|---|
| Test | 一个片段一次 Judge 请求；逐调用保留执行事实和合理性，联合相关调用判断 workflow 覆盖 | 每条覆盖引用有序 action/observation 链；不能跨编辑、部署、显式 reset 或失败重试拼接。覆盖不等于功能通过 |
| Visual Judgment | 候选可引用此前片段的图像；评分包包含判断之前的记录，并标注图像对应的 workspace hash | 不含未来编辑或评分信息；图像消费历史不等于模型压缩后的精确上下文 |
| Safe Repair | 配置指定需求、原检查、前后版本、启动方式、结果断言和回归项；输出 `repair_records.json` | 只有真实改变的代码可计修复尝试；原本正常不算修好；仅以前版本实际通过的检查判断回归 |

Safe Repair 不再在公共 Python/JS 中写死五个功能的判断。五组历史检查仍保留在示例配置中，含义未改变。配置可使用已核验的 checkpoint manifest，或明确审计过的 Edit-only 重建；当前重放适配器仍要求可解析的原始 `playwright-cli run-code`，不是任意轨迹自动重放器。

截图选择优先显式提及的图及各页面最新图，省略项落盘。307 输入包现包含此前 7 张页面图和 6 张任务原型，最后证据为 306；没有 308 的编辑。该包是记录前缀，不声称恢复了原模型的上下文压缩状态。

## 实际验证

- **66 项回归测试通过**：包括跨调用覆盖、跨编辑/reset 拒绝拼接、307 跨片段取图、未来证据排除、配置断言、版本核验以及旧结果兼容。
- **真实浏览器重放完成**：V0–V3，24 次页面访问、20 组功能检查、100 条操作记录、164 张截图；20 组配置断言均通过，已测范围内三次版本转换未发现回归。
- **未生成新模型评分**：Test 合理性/覆盖、Visual Judgment 和五个视觉修复目标仍待 Judge；集成结果正确显示 `partial`，未套用历史标签。
- 使用现有 Python/Node/Chromium，无安装、无模型服务或 GPU 任务。临时应用服务已退出。

证据位于 [`runs/vsv_eval/protocol-v2-smoke-0907/`](../runs/vsv_eval/protocol-v2-smoke-0907/)：

- [`test/test_inputs/`](../runs/vsv_eval/protocol-v2-smoke-0907/test/test_inputs/)：三段完整联合评分输入，仍为 12 个原调用组。
- [`judgment-307-packet.json`](../runs/vsv_eval/protocol-v2-smoke-0907/tests/test_cross_episode_judgment_300/judgment-307-packet.json)：跨片段视觉证据包。
- [`repair/repair_records.json`](../runs/vsv_eval/protocol-v2-smoke-0907/repair/repair_records.json)：五个目标与真实动作、版本的对应关系。267 原本是 Bash 动作，已标明，未伪装成模型视觉判断。
- [`repair/replays/`](../runs/vsv_eval/protocol-v2-smoke-0907/repair/replays/)：四版本逐步截图、DOM、输出、错误和启动日志。
- [`integrated/scores.json`](../runs/vsv_eval/protocol-v2-smoke-0907/integrated/scores.json)：统一结果；这是流程 smoke，不是新模型分数。

## 入口与复现

新评分配置：[`vsv_smartrecruiters_pipeline_v2.json`](../configs/vision2web/vsv_smartrecruiters_pipeline_v2.json)。旧配置继续只用于复用历史结果；结果记录来源 schema，不能把旧分数改称新版协议分数。

```bash
cd /data/miyapeng/mmcode/MultimodalCode

# 仅测试规则与已有产物，不调用模型。
conda run -n mmcode env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src \
  pytest -q tests/test_vsv_eval.py tests/test_vsv_checks.py \
  tests/test_vsv_visual_judgment.py tests/test_vsv_repair_pilot.py \
  tests/test_vsv_pipeline.py

# 仅准备真实版本与 repair_records，不启动应用或 Judge。
conda run -n mmcode python scripts/vision2web/score_repair_pilot.py \
  --fixture data/vision2web/vsv_eval_fixtures/frontend__smartrecruiters__claude-opus-4-8__self_verify \
  --spec configs/vision2web/smartrecruiters_repair_v2.json \
  --output-dir runs/vsv_eval/repair-v2-prepare-next --prepare-only
```

完整新评分命令（**本轮未执行**；先确认 `vsv_scoring.json` 中两个 Qwen Judge profile 的端点可用）：

```bash
export VSV_PLAYWRIGHT_PACKAGE=/data/miyapeng/miniconda3/envs/mmcode/lib/python3.10/site-packages/playwright/driver/package
export VSV_CHROMIUM=/data/miyapeng/mmcode/MultimodalCode/.runtime/research/playwright/chromium-1223/chrome-linux64/chrome
conda run -n mmcode python scripts/vision2web/evaluate_trajectory.py \
  --config configs/vision2web/vsv_smartrecruiters_pipeline_v2.json \
  --output-dir runs/vsv_eval/smartrecruiters-protocol-v2-scored-next --run-missing
```

输出目录须为新的空目录。浏览器 smoke 使用同一 repair 命令去掉 `--prepare-only`、加 `--offline --port 18949`，并设置上述两个浏览器变量。pytest 复用系统测试程序；两个生产 CLI 均已用 mmcode Python 实测。

## 修改位置

- `src/multimodalcode/vsv_eval/{checks,scoring,visual_judgment,repair_pilot,pipeline}.py`
- `scripts/vision2web/{score_repair_pilot.py,replay_recorded_browser.cjs}`
- 新增 `configs/vision2web/{smartrecruiters_repair_v2,vsv_smartrecruiters_pipeline_v2}.json`
- 五个上述测试文件；主设计文档、本记录及旧审计的状态链接。

当前项目目录不是 Git worktree，因此没有 Git diff/commit；未改官方 `evaluate/` 路径。下一步可用新版输入重跑 Judge，然后在第二条可恢复版本的轨迹上检验配置复用；本次没有宣称评分已推广到其他任务。
