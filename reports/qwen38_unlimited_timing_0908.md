# Qwen3.8 单题无整任务时限诊断 — 2026-09-08

目的：判断此前 7200 秒超时是否截断了仍能自行完成的生成过程。此实验单独保存，不混入原两小时预算结果。

## 已执行

- 停止 Qwen3.8 全量调度器（PID 1215151、1215215）。
- 12:25:11 UTC 停止 `webpage/ima` 的 CPU job `mmc-v2w-cc-off-32f8-60d33af`；保留已落盘轨迹和 development 记录。
- Qwen3.5 的调度、任务与模型服务未改动。
- 保留原 Qwen3.8 的 2 GPU vLLM 服务，供唯一一个诊断任务调用。
- 新 job 已确认 `RUNNING`，worker 于 **12:27:59 UTC** 启动；启动日志明确记录 `wall_time_seconds=0`。已出现实际 Bash 和 Read 调用，不只是排队状态。

## 单题配置

| 项目 | 设置 |
|---|---|
| 任务 | `webpage/home_instead`（Level 1） |
| 框架、输入 | Claude Code；官方 prompt、原型和资源 |
| 起点 | 新任务容器重新构建 workspace；不续接旧会话或旧代码 |
| 模型 | Qwen3.8-27B；原服务 `qwen38-27b-v2w-full-0904` |
| 推理设置 | 保持原 `xhigh`、模型服务采样参数、262144 context、TP=2 |
| 唯一预算变化 | 整任务 wall-time：7200 → 0（`Popen.wait(timeout=None)`） |
| 保持的限制 | 上下文、单次请求/工具限制不变；不强制模型提交 |
| 运行次数 | 1；Claude 重试 0；基础设施自动重试 0 |
| 资源 | 新 worker：0 GPU、8 vCPU、32 GiB；复用已有 2 GPU 推理服务 |
| ClusterX job | `mmc-v2w-cc-off-996b-0a23991` |

没有本地常驻调度器；单个 ClusterX job 后台运行，关闭本地终端不影响它。无整任务时限并不保证必然完成：若模型持续循环，需要人工查看后决定停止。

## 复现命令

在已配置代理的环境中执行（同名运行目录避免重复提交）：

```bash
cd /data/miyapeng/mmcode/MultimodalCode
/data/miyapeng/miniconda3/envs/mmcode/bin/python -u scripts/vision2web/submit_self_verify.py \
  --framework claude_code --phase full --modes official \
  --model Qwen3.8-27B \
  --run-label qwen38-27b-cc-official-unlimited-home-instead-0908 \
  --server-run qwen38-27b-v2w-full-0904 \
  --max-active 1 --max-infra-retries 0 --wall-time 0 \
  --case-ids webpage/home_instead --once
```

## 记录与判定

运行根目录：`runs/vision2web_generation/qwen38-27b-cc-official-unlimited-home-instead-0908/`。

案例目录：`agents/Qwen3.8-27B/vision2web/claude_code/official/webpage__home_instead/`。

- `claude.events.attempt-1.jsonl`：原始模型与工具事件。
- `claude.capture.attempt-1.jsonl`：实际时间顺序；`development/` 保存开发证据。
- `result.json`：结束后检查 `status`、`duration_seconds`、`returncode` 和 `error`，不能仅以文件存在判断完成。
- `workspace/`：结束后导出的代码；`start-validation.json` 仅是部署诊断，不等于官方功能/视觉评分。
- 计时以 agent 开始至退出为准，排队时间单独记录。当前尚无完成用时结论。

## 最小实现变更及测试

- `scripts/vision2web/submit_self_verify.py`：增加 `--wall-time`；默认仍为 7200；记录预算并拒绝在同一已有 run 中改变预算。
- `scripts/vision2web/run_generation_case.sh`：转发可选第八参数；不改变 prompt、工具或官方 evaluator。
- `tests/test_vision2web_task_deadline.py`：验证 0 确实映射为无任务 deadline，以及默认预算、参数传递与防混用保护。
- 相关测试：19 passed；shell 语法检查通过。使用系统 pytest，不安装或修改 mmcode 依赖。

启动时源码 SHA256：

```text
c70bfb6927c4803d40e060a60089afc25339259d8cede8f1789de0bf67c46c93  scripts/vision2web/run_generation_case.sh
6ee40d3efcb08c5e9066b0805fc9509953aa475a30c10abb02ebec869a3e798c  scripts/vision2web/submit_self_verify.py
11d0a26b2759154680cec2773d34a62e7d7b23e784e0563f8c60bf5943bde7d5  src/multimodalcode/agent_harness/claude_code_runner.py
```

项目根目录不是 Git worktree；本次修改文件为以上两个脚本、一个测试文件、本报告，以及已停止的旧 controller 状态 JSON。未修改冻结的官方 evaluator，未删除历史结果。
