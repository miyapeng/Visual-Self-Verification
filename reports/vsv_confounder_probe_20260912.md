# 视觉自检的小规模排障实验

目标：先区分工具故障与模型没有主动检查，不直接推断任务无需视觉自检。

## 固定设计

第一例：`webpage/classic-clashes`，采用 Qwen3.5-9B / Claude Code official-full-0904 已保存的 workspace。不从零生成、不注入错误、不读取 workflow 或评分结果。该例使用 Python 静态服务，减少 npm 安装干扰。它是排障样例，不代表全部任务难度。

1. **基础设施**：冻结镜像中运行原有 start.sh，用原生 playwright-cli 打开页面、snapshot、screenshot、点击、再次截图并记录 console。外部指定的点击只测试执行能力，不评判需求覆盖。
2. **图像传递**：同一 Qwen3.5-9B 通过 Claude Code Read 实际页面 PNG；核对 image 工具返回与后续描述。此步骤需要可用模型端点，尚未运行，不能把截图成功等同于模型看到图片。
3. **行为对照**：两个独立容器复制相同 workspace、使用新会话。`checkpoint_baseline` 使用官方任务 prompt + “已有实现，请继续完成”；`checkpoint_guided` 仅再加具体视觉检查指导。两组同一模型/端点、相同默认采样与 effort、1200 秒上限、零自动重试。保存完整输入、初始文件哈希、原始工具返回与最终代码。

这不是原版 official 全程生成对照，而是**受控的中间代码续写实验**。不把它与原来 193 条完整轨迹直接计算 prompt 因果增益。1200 秒截断只用于限制小实验，timeout 单独标记。

主要观察：是否部署；截图是否进入模型；是否提出有根据的视觉问题；是否修改及复查。修复正确性需要人工对照原型和前后页面，不以修改次数/自述代替。第一例通过后才扩到 3–5 例；若想证明视觉有独立增益，还需同预算的文字反馈对照，本轮不提前下这个结论。

## 实现和状态

唯一新增入口：`scripts/vision2web/probe_vsv_confounders.py`。复用现有 Claude Code runner 和镜像中的 playwright-cli，不修改官方工具 schema 或官方 prompt 文件，不改变历史输出。

2026-09-12 提交零 GPU 任务 `mmc-vsv-infra-0912`（4 CPU / 16 GiB）。已确认镜像运行版本为 Claude Code 2.1.197、playwright-cli 0.1.18。

产物目录：`runs/vsv_eval/confounder-probe-0912/infrastructure/`；`commands.json` 为命令及原始返回，`initial_files.json` 为复制后输入文件哈希，`server.log` 为真实 start.sh 日志，`result.json` 为完成状态。

实测已完成：`browser_status=passed`。start.sh、打开、文字 snapshot、前后两张 PNG、一次原生 run-code 点击、console 获取均成功。这里没有新增浏览器封装接口。点击的是页面首个链接，仅证明可执行，不声称通过功能测试。

人工查看 `before.png`：主视觉图片显示 broken-image/alt 文本，大片空白上的白色标题可读性很差；console 同时报告该图片请求 404。这是原有代码的可观察问题，不是人为植入。由于 404 也能从文字日志发现，它尚不能证明视觉输入的独立增益，也不表示只有看图才能解决。

模型尚未参与，因此不能把本次研究助手查看 PNG 当成 Qwen 已接收图片。图像进入 Claude/Qwen 上下文、主动性与修复收益三项仍待测。

模型阶段当前未提交：旧 Qwen3.5 地址 `10.119.255.142:18002` 实测超时，没有运行中的可复用服务。本用户另外五个任务合计使用 16 GPU，未修改或停止它们，也未新增 GPU。

### 后续授权与执行更新

用户随后允许临时新增 **1 GPU**。已提交 `mmc-q35-vsv-probe-0912`，配置 TP=1、max_model_len=262144、max_num_seqs=1、qwen3_coder tool parser、qwen3 reasoning parser。此处并发/TP 与原全量运行不同，两组新对照彼此配置相同。启动日志显示 vLLM 0.25.1，实际运行配置保存于服务 ready.json；不声称与历史全量推理软件版本完全一致。

`run_confounder_probe_controller.py` 在 tmux 会话 `vsv-probe-0912` 中运行，等待服务就绪，随后顺序提交 3 个零 GPU 容器：图像通路检查、checkpoint_baseline、checkpoint_guided。图像工具返回及后续 assistant 事件没有出现时，不自动进行行为比较。这个自动门槛只是输入传递证据，图像理解正确性仍需查看实际描述。

状态文件：`runs/vsv_eval/confounder-probe-0912/model-experiment/state.json`。控制器完成或出错后只释放本次模型/未完成 worker，不触碰其他任务；模型任务另有 5400 秒硬上限。结果未产出前不得把已提交记为通过。

## 后续执行

在同一个冻结任务镜像 `registry.pjlab.org.cn/ccr-t-llm-frontier/vision2web:official-577f939` 的**新容器**执行，每次输出目录必须不同，/workspace 必须为空：

```bash
export PYTHONPATH=/data/miyapeng/mmcode/MultimodalCode/src
python3.12 /data/miyapeng/mmcode/MultimodalCode/scripts/vision2web/probe_vsv_confounders.py \
  --condition infrastructure \
  --base-url http://AVAILABLE_QWEN_HOST:18002 \
  --source /data/miyapeng/mmcode/MultimodalCode/runs/vision2web_generation/qwen35-9b-claude-code-official-full-0904/agents/Qwen3.5-9B/vision2web/claude_code/official/webpage__classic-clashes/workspace \
  --output /data/miyapeng/mmcode/MultimodalCode/runs/vsv_eval/confounder-probe-0912/image-context
```

该端点是原生 Anthropic 服务根地址，需要先确认模型为 Qwen3.5-9B。图像通路确认后，将 condition 分别换成 `checkpoint_baseline` / `checkpoint_guided`，输出目录对应改名，分别在新容器运行。两组不能串用同一个被修改过的 workspace。完整指导文本在入口文件的 `GUIDANCE`，实际传给模型的完整输入另存 `input.txt`。

## 2026-09-13：正确命令示例对照（已结束，局部行为已审计）

前一轮图像 Read 通路通过；baseline 仅读取原型，guided 尝试其他 Playwright 命令/安装但没有拿到应用截图。因此增加 `checkpoint_guided_commands`：完全保留 guided 输入，只追加已安装 playwright-cli 的三条命令示例（open、snapshot、screenshot），以及必须 Read PNG、snapshot 仅返回文字、无需额外安装的说明。不提示实际缺陷或修复方案。

复用相同原始 workspace，保持模型、TP=1、262K、并发1、1200秒预算和零重试。单样例单次结果仅用于排障，不作显著性或普遍能力结论。服务 `mmc-q35-vsv-cmd-0913` 新增1 GPU、最长3600秒；CPU worker 在 tmux `vsv-cmd-0913` 下自动等待并运行，完成后释放服务。

状态与产物：`runs/vsv_eval/confounder-probe-0913/command-example/state.json` 及其 `checkpoint_guided_commands/` 子目录。后续依次审计真实命令、截图 image 返回、反馈后的判断、代码修改和再次检查，不按进程 success 认定视觉自检成功。

### 09-17 文档补记：已核对的运行结果

| 条件 | 结果 | 观察 |
|---|---|---|
| infrastructure | 图像工具返回后有模型回答 | Read `/workspace/probe-page.png`，返回真实 image；随后描述页面颜色与布局，不代表行为主动性 |
| checkpoint_baseline | success，533 秒 | 只 Read 3 张原型；改代码并 curl，没有应用截图 |
| checkpoint_guided | success，1188 秒 | 尝试错误命令及安装 Playwright，未接收应用图像 |
| checkpoint_guided_commands | timeout，1200 秒 | 4 次真实应用 image 输入，图片路径修改后再次截图/Read；字体修改后陷入操作与恢复尝试 |

最后一行原始证据位于 `claude.events.attempt-1.jsonl`：317/318 首次 Read/image；1485/1486 将 `El_clasico-image.jpg` 改为 `el-clasico-image.jpg` 且编辑成功；2053/2054 修复后 Read/image；2259/2260 和 2448/2449 为后续桌面/平板图像。观察绑定记录在 `development/*/browser.events.jsonl`。没有完成最后字体修改的有效视觉复查，不记为完整任务成功。

说明正确命令示例能在该样例引出局部“观察—定位—编辑—再观察”；图片路径问题也能通过文字请求/文件检查发现，不证明视觉反馈的独立因果收益。后半段持续浏览器失败尚未完成根因审计，不一概归因于模型。新轨迹尚未补三阶段评分与官方前后质量分。两次临时服务在各轮结束时均核实为 STOPPED。
