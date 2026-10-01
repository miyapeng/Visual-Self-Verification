# Vision2Web 全量推理续跑（2026-09-08）

## 当前安排

沿用 Claude Code `official` 路径，仅恢复代码生成，不启动付费 Judge 或官方评分。

| 模型 | 保留的历史任务 | 本次续跑 | 同时运行的 case | 复用服务 |
| --- | --- | --- | --- | --- |
| Qwen3.5-9B | 134 个正常结束 | 59：47 未提交、6 缺结果、6 进程失败 | 3 | `mmc-q35-v2w-full-0904`，2 GPU |
| Qwen3.8-27B | 39 个 timeout（两目录去重） | 154 个尚未覆盖任务 | 1 | `mmc-q38-v2w-full-0904`，2 GPU |

“正常结束”不是功能评分通过。Qwen3.8 的 71 次历史尝试只有 39 个不同任务，timeout 原样保留，本轮不重复运行这 39 题。

未修改任务 prompt、工具、模型服务或单题 7200 秒预算；沿用输入上下文设置 229376、最大输出 32768、Qwen3.5 effort=high、Qwen3.8 effort=xhigh。Qwen3.8 仅将调度并发降为 1，避免两个旧控制器争用同一服务。墙钟预算受并发影响，因此新旧尝试须标注调度差异，不能宣称已解决历史 timeout。

每个 case 任务申请 0 GPU、8 CPU、32 GiB 内存。仅复用已有 4 张 GPU，没有增加模型部署，也没有停止或修改其他项目任务。

## 续跑与保存

- 配置：`configs/vision2web/resume_official_0908.json`。
- 小型入口：`scripts/vision2web/resume_official.py`。只读取旧结果、冻结待跑清单，然后调用原有 `submit_self_verify.py`；文件锁阻止同一续跑入口重复运行。
- Qwen3.5 新目录：`runs/vision2web_generation/qwen35-9b-claude-code-official-resume-0908/`。
- Qwen3.8 新目录：`runs/vision2web_generation/qwen38-27b-claude-code-official-resume-0908/`。
- 各目录的 `resume-selection.json` 保存全部 193 题的选取决定、历史结果路径和控制器命令；`controller-claude-code-full.json` 保存实时状态，`supervisor.log` 保存控制器日志。
- 轨迹：`agents/<model>/vision2web/claude_code/official/<case>/claude.events.attempt-1.jsonl`。同期保存 capture、workspace/browser events 和版本产物。
- 历史 `full-0904`、`full-0904-v2` 目录均未删除或覆盖。实际代码根目录是 `/data/miyapeng/mmcode/MultimodalCode`，本目录目前不是 Git worktree。

控制器已经在开发机 tmux 中后台运行：

```bash
tmux attach -t v2w-q35-resume-0908
tmux attach -t v2w-q38-resume-0908
```

断开 SSH 不会终止 tmux；开发机本身被停止则仍会中断控制器。退出 tmux 观察窗口使用 Ctrl-b、d，不要发送 Ctrl-c。

若控制器已退出，可从项目根目录恢复同一清单（现有结果由原控制器跳过）：

```bash
proxy_on
export PATH=/data/miyapeng/persistent/bin:$PATH
/data/miyapeng/miniconda3/envs/mmcode/bin/python -u scripts/vision2web/resume_official.py --config configs/vision2web/resume_official_0908.json --model Qwen3.5-9B
# 另一个模型使用同一命令，将 --model 改为 Qwen3.8-27B。
```

## 首批执行核验

2026-09-08 03:43–03:46 UTC：

- Qwen3.5：`webpage/abc`、`webpage/aspose`、`webpage/egginfo` 三个 CPU job 均 RUNNING，已出现真实 Bash/Read 工具调用与非空事件文件。
- Qwen3.8：`webpage/home_instead` 的 CPU job RUNNING，已出现真实 Bash/Read 工具调用与非空事件文件。
- 两台 vLLM 的请求数及生成 token 均已增长，最新观测分别有 3、1 个推理请求正在处理；四个 agent stderr 当时均为空。尚未产生本批最终任务结果。
- `/data` 文件系统显示约 2.3 TB 可用，本轮清单、状态和轨迹写入成功；这不等价于获取到了用户配额上限，后续仍需观察配额错误。
- 续跑选择测试：`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src pytest -q tests/test_vision2web_resume_official.py`，4 passed；对正在运行的同一入口再次调用也已验证被锁拒绝，没有重复提交。

原控制器将存在 `generation-summary.json` 的尝试视为已结束（其中可能是 failed/missing），并不保证最终全是成功结果。后续统计仍须读取真实 `result.json`；基础设施异常再单独补跑，模型 timeout 不自动伪装成可重试基础设施错误。
