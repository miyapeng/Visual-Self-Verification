# 现有轨迹中的自检：Qwen3.5-9B / Claude Code official

审计日期：2026-09-12。只读已有轨迹，不重新生成网站，不调用 Judge，不运行浏览器或官方评分。

## 样本与结论

合并 `qwen35-9b-claude-code-official-full-0904` 与 `qwen35-9b-claude-code-official-resume-0908`，每个任务选择 started_at 最新的运行，不按成败挑选。共 193 个任务：192 个进程正常结束，1 个失败（website/bettys）。正常结束不代表任务正确或官方评测通过。

| 观察到的行为（任务数） | L1 / 100 | L2 / 66 | L3 / 27 | 合计 / 193 |
|---|---:|---:|---:|---:|
| 浏览器打开自己部署的网站 | 0 | 1 | 0 | 1（0.52%） |
| 应用截图实际进入模型上下文 | 0 | 0 | 0 | 0 |
| 收到应用截图后修改代码 | 0 | 0 | 0 | 0 |
| 看图、修改后再次浏览检查 | 0 | 0 | 0 | 0 |
| 浏览器文字反馈后修改并复查 | 0 | 1 | 0 | 1 |

**这批运行几乎没有主动浏览，更没有观察到视觉自检闭环；但不能据此说模型没有修复能力。** 没有看图后的修复样本，条件修复成功率应为 N/A，不是 0%。这里只覆盖这个模型和配置，不代表其他模型。

## 统计依据

- 直接关联 `claude.events.attempt-*.jsonl` 中 tool_use 和 tool_result，保留原文件路径、行号、工具 ID 和执行顺序；包含子会话工具调用。
- 打开应用：浏览器实际返回 localhost:3000 页面 URL；请求打开但失败、curl/HTTP 200、原型图浏览均不计入。
- 看到截图：必须有工具返回的 image 内容；执行 screenshot 命令或返回 PNG 路径不算看到图片，`snapshot` 返回 YAML 也不算。
- 修改：核对成功的源文件 Edit/Write 返回及 workspace 变化，排除截图、日志等产物新增。唯一浏览器案例的修改均为显式 Edit；另查 Bash 与写入代码中的浏览器自动化候选，未发现漏计的脚本截图路径。
- 142 个任务有图片 Read 返回，但都是原型/素材。alberta_alis 将原型复制到 public/images 后读取：已核对先前 cp 调用与源/目标文件 SHA256，单独记录出处，不算应用截图。
- 当前图片与浏览器候选没有未解决项。脚本遇到未知图片来源会记 unknown，不直接当成没有自检。

## 唯一浏览器案例

`frontend/newsday_co_tt`，resume-0908 下同名 case 的 `claude.events.attempt-1.jsonl`：

| 原始行号（调用 → 返回） | 实际过程 |
|---|---|
| 4219 → 4222 | 打开 localhost:3000，页面 HTTP 500、console 错误 |
| 4227 → 4230 | 读取 console，取得 React LinkComponent 错误 |
| 4241–4293 | 9 次成功 Edit，涉及 page.tsx、MainNav.tsx、ArticleCard.tsx、article-page.tsx |
| 4303 → 4306 | reload，返回页面标题及状态 |
| 4328 → 4331；4358 → 4361 | 两次成功点击，进入列表/文章页 |

这是**运行错误/页面结构文字 → 修改 → 再检查**，不是视觉自检。期间没有应用截图 image 返回。部分点击因 ref 无效失败，也保留在原轨迹中；重新加载与点击可执行不等于全部功能修复成功。

workspace 记录器可能延迟合并记录变化，因此本例编辑顺序以真实工具调用/成功返回为准，不能把异步落盘时间当作精确编辑时间。

## 解释时需要保留的边界

- 193/193 个任务的系统初始化注册了 playwright-cli skill；193/193 保存的 prompt hash 与官方 prompt hash 一致，没有 guided_vsv 附加提示。官方 prompt 本身包含启动/测试要求，并不是完全不提验证。
- 135 个任务在 Agent 运行期间被记录器探测到 HTTP 200（L1 99、L2 33、L3 3）。这只说明端口有响应，不保证正确应用可用；它说明缺少浏览不能全部归因于没有任何服务响应。
- 63 个任务曾出现依赖下载、代理或网络错误（L1 17、L2 45、L3 1），不是说 63 个都最终受阻。环境因素尤其影响 L2/L3，不能把所有不检查都解释成模型能力缺陷。
- 工具注册不等于在每个任务中已验证截图全链路可用。本统计回答实际发生了什么，不替代确定性浏览器能力测试，也不评判修复质量。
- 审计规则做了 5 项回归检查：文字 snapshot、导航失败、原型图、未知图片、检查前已发生的编辑。

## 产物与复现

逐任务结果：[cases.csv](../runs/vsv_eval/qwen35-official-self-check-audit-0912-final/cases.csv)。
事件证据：[cases.json](../runs/vsv_eval/qwen35-official-self-check-audit-0912-final/cases.json)。
汇总：[summary.json](../runs/vsv_eval/qwen35-official-self-check-audit-0912-final/summary.json)。
完整候选索引与原始文件哈希：[inventory.json](../runs/vsv_eval/qwen35-official-self-check-audit-0912-final/inventory.json)。历史结果未改动。

从 MultimodalCode 目录运行（输出目录必须是新的）：

```bash
python scripts/vision2web/audit_observed_self_checks.py \
  --run-root runs/vision2web_generation/qwen35-9b-claude-code-official-full-0904 \
  --run-root runs/vision2web_generation/qwen35-9b-claude-code-official-resume-0908 \
  --output-dir runs/vsv_eval/qwen35-official-self-check-audit-repeat
```

新增实现仅为 `scripts/vision2web/audit_observed_self_checks.py`，不修改推理、浏览器或评测器。当前 MultimodalCode 不是 Git worktree，无法提供 git diff。
