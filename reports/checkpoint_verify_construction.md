# Checkpoint 验证题构造（2026-09-08）

目的：固定已有实现，测新会话能否自行检查、判断和修复；复用现有三阶段评分，不提供标准错误答案。
只处理 Vision2Web。项目不是 Git worktree；没有修改官方 evaluator、官方 inference 路径或历史轨迹。

## 实现

- `src/multimodalcode/vsv_eval/checkpoint_tasks.py`：检查定位、快照/已审核应用版本恢复、公开输入打包、启动检查和 runner 输入准备。
- `scripts/vision2web/build_checkpoint_tasks.py`：`build` 构造，`validate` 在独立 CPU 容器检查启动。
- `prompts/vision2web/checkpoint_verify.txt`：追加提示；官方 prompt 和公开需求不替换。
- `configs/vision2web/smartrecruiters_checkpoint_sources.json`：原启动命令、来源哈希及三个已审核版本与检查事件的映射；不含故障标签或检查答案。
- `tests/test_vsv_checkpoint_tasks.py`：缺脚本/对象、版本与哈希匹配、去重、历史图像排除、输入篡改、启动准入和防覆盖测试。
- `reports/visual_self_verification_two_level_evaluation_design.md` §7：更新当前方案，旧 verify-only/resume 不再作为本轮入口。

构造不运行模型、不读取 workflow 内容。每次真实 browser 调用是一组，多场景不强拆；从组开始前的真实代码恢复。
同代码多次检查合为一道题，保留所有来源事件。这里去重的是输入代码，不是重定义原 episode。
当前定位能力继承原切分器；没有声称找全任意 Bash/浏览器脚本中的全部检查，候选仍需抽查。

## 输入与边界

每个 sample 只有以下结构：

```text
sample/
  prompt.txt       原始 inference prompt + 检查指令
  workspace/       可恢复的真实代码 + 原始公开任务材料
  task.json        输入清单、哈希、协议
  provenance.json  离线来源，不给模型
```

Agent 只接收 `prompt.txt` 和 `workspace/`；不要把整个数据集目录挂进 Agent。
启动检查的日志/截图在 `runs/` 下，只供筛选，不给被测模型。
恢复的是代码和公开输入，不是历史数据库、服务器进程或浏览器 cookie；需要外部状态的任务须另行固定初始化数据。
有 `start.sh` 就用原脚本；没有则使用轨迹里已成功执行的启动命令及工作目录，不新造脚本。
`node_modules` 等依赖按原启动方式准备；没有启动成功证据不能标为可运行题。启动成功也不等于功能正确。
新提示给出当前启动方式，但保留官方原始输入中最终交付 `start.sh` 的要求；被测模型可以自行补齐交付文件。

完整追加提示：

> The workspace already contains an implementation of the task above. Continue from this implementation rather than starting over. Its launch command is `{launch_command}`, run from `{launch_cwd}`. Use the available browser tools to inspect the running application's rendered appearance and interactive behavior against the original requirements and reference images. Fix problems supported by the observed evidence and recheck affected behavior, taking care not to break working functionality. You decide what to inspect, whether changes are necessary, and when to submit.

两个占位符仅填写启动信息；不填写旧模型的检查动作、视觉诊断或修复建议。已冻结的旧 Kimi 输入不改写。

## 复现

```bash
cd /data/miyapeng/mmcode/MultimodalCode
conda activate mmcode
python scripts/vision2web/build_checkpoint_tasks.py build \
  --run runs/vsv_eval/checkpoint-construction-0908/kimi-source/data/runs/kimi-k2.6-claude_code.json \
  --output data/vision2web/checkpoint_verify/another-build
```

输入必须是已有标准化 `run.json`，含 `timeline/development`，不是原始 Claude stream-json。
可以用 `--action 335` 固定选取某次已识别检查。不传则扫描该轨迹所有已识别检查并按代码版本去重。
输出目录不得存在，构造失败原因保存在 `manifest.json`；不回退使用最终版本。

经典 Opus 的三个应用版本：

```bash
python scripts/vision2web/build_checkpoint_tasks.py build \
  --run data/vision2web/vsv_eval_fixtures/frontend__smartrecruiters__claude-opus-4-8__self_verify/trajectory/run.json \
  --sources-config configs/vision2web/smartrecruiters_checkpoint_sources.json \
  --output data/vision2web/checkpoint_verify/another-opus-build
```

来源配置固定重建 manifest 和原轨迹哈希，验证每个代码文件；版本不能晚于检查，
版本边界到检查之间的工具调用必须已审核。V1 的间隔调用 247 只 reload/截图（截图失败），不改代码；
V2/V3 编辑成功后直接进入下一次检查。旧资源软链接由原始任务资源的等字节副本替代，包内无外部软链接。

在**全新 Vision2Web CPU 容器**内执行（不能直接在当前非空开发机 `/workspace` 跑）：

```bash
python3.12 /data/miyapeng/mmcode/MultimodalCode/scripts/vision2web/build_checkpoint_tasks.py validate \
  --sample /data/miyapeng/mmcode/MultimodalCode/data/vision2web/checkpoint_verify/smartrecruiters-kimi-0908-clean/samples/frontend__smartrecruiters__aa9b07edd0ff \
  --output /data/miyapeng/mmcode/MultimodalCode/runs/vsv_eval/checkpoint-construction-0908/new-runtime-check \
  --timeout 180
```

`prepare_agent_case(sample, fresh_workspace, runtime_check)` 仅接受匹配 task 哈希的成功启动结果，
返回现有 runner 可用的 `AgentCase`。后续 Claude Code 不传 `resume_session_id`；OpenHands 用原生 browser_enabled。
传入的 prompt 已完整，不再追加 guided_vsv。实验协议是 checkpoint_verify，不声称这是官方原始推理条件。
运行时还需沿用现有被动记录器保存 P_start/新版本和新轨迹，再进入原三阶段评分；本轮没有运行新的 Agent 推理。

## 真实构造检查

- 早期严格要求 `start.sh` 的构造版：经典 Opus 得到 0 道题，记录保留于 `data/vision2web/checkpoint_verify/pilot-0908/opus-classic-v2/manifest.json`。这不是中间代码无法运行：事件 192 已通过 `node server.js` 启动，193 返回各页面 HTTP 200；脚本直到 318 才创建。当前实现已解除该文件名限制，新增结果见下表。
- Kimi-K2.6 / Claude Code / guided_vsv：原任务 `frontend/smartrecruiters`。复用原导出器生成标准化源：`runs/vsv_eval/checkpoint-construction-0908/kimi-source/data/runs/kimi-k2.6-claude_code.json`，534 个原始事件；不是新模型运行。正式构造输出为 `data/vision2web/checkpoint_verify/smartrecruiters-kimi-0908-clean/`。去除实际 Chrome 命令生成的截图（包括 shell 循环输出），不把旧截图当新任务输入。
- 0 GPU 启动任务 `mmcode-vsv-checkpoint-boot-0908`：运行原始 start.sh，停留在 npm 安装，180 秒未启动；已结束。证据：`runs/vsv_eval/checkpoint-construction-0908/kimi-runtime/runtime-check.json` 与 `server.log`。这是环境/启动尚未打通，不是模型验证能力得分。
- 已定位 npm 网络差异：开发机直连探针失败，现有 `proxy_on` 返回 HTTP 200。仅做一次修正后的重试：`mmc-vsv-checkpoint-proxy-0908`，仍为 0 GPU / 4 CPU / 16 GiB；向同一官方镜像传入现有代理，代码与 start.sh 不变。原脚本安装 133 个 npm 包，Vite 5.4.21 启动，首页 HTTP 200、真实截图非空；该 job 已 `Succeeded`。成功证据在 `runs/vsv_eval/checkpoint-construction-0908/kimi-runtime-proxy/`，包含 `runtime-check.json`、`server.log`、`initial.png` 和 `initial.html`。
- 成功题为事件 **335 前**的 `frontend__smartrecruiters__aa9b07edd0ff`，该冻结 task 的 SHA-256 为 `a78885046673f21a023b38cef460a81adcc39d6f5796038fade18b51c1d598b3`。这是部署可用证明，不是功能正确或 Agent 自检成功证明。console 另有一条资源请求 407，已原样记录，不能把环境资源失败混作模型判断错误。
- 最终结果：8 个候选检查调用中，335、375、381 对应的应用代码相同（差别只是旧截图），合并为 **1 道题**；另 5 个没有精确匹配快照的暂时排除。最终包已逐文件复核，无旧截图，且与成功启动检查的 task 哈希完全一致。
- `prepare_agent_case()` 也已实际执行：成功核验准入结果，准备包含原 start.sh、6 张原型图和完整 prompt 的新 `AgentCase`；输出在 `runs/vsv_eval/checkpoint-construction-0908/prepare-smoke/workspace`。没有调用模型，后续推理应再使用新的空 workspace。

`pilot-0908/kimi`、`kimi-v2` 和 `smartrecruiters-kimi-0908/` 是构造调试产物，不作正式样本入口；原始产物保留。
部署探测不会评估交互正确性，也不覆盖复杂任务的数据库状态。正式跨模型运行仍需固定依赖/镜像和网络条件。
测试使用系统 Python 的 pytest（关闭无关插件自动加载），构造脚本另在 mmcode Python 3.10 下执行；没有安装、卸载或变更 mmcode 的任何包。
当前构造测试 24 项及原三阶段评测回归测试，共 **93 passed**：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src /usr/bin/python -m pytest -q \
  tests/test_vsv_checkpoint_tasks.py tests/test_vsv_eval.py tests/test_vsv_checks.py \
  tests/test_vsv_visual_judgment.py tests/test_vsv_repair_pilot.py tests/test_vsv_pipeline.py
```

无检查原型标签、无故障合成、无付费 API 请求、无 GPU 新占用。

## 新增 Opus 中间状态（已启动验证）

数据：[`smartrecruiters-opus-0908/manifest.json`](../data/vision2web/checkpoint_verify/smartrecruiters-opus-0908/manifest.json)。
来源是已经审核的 `runs/vsv_eval/smartrecruiters-full-0907/repair/versions/reconstruction.json`，
仅提取版本/文件 manifest，不读取既有评分或据此筛选。

| 输入版本 | 检查动作前 | 样本 ID 后缀 | 启动证据 |
|---|---:|---|---|
| V1：编辑至 245 | 249 | `943e11eab139` | [runtime-check](../runs/vsv_eval/checkpoint-construction-0908/opus-v1-runtime/runtime-check.json) |
| V2：编辑至 269 | 272 | `4a3dafcd77bb` | [runtime-check](../runs/vsv_eval/checkpoint-construction-0908/opus-v2-runtime/runtime-check.json) |
| V3：编辑至 308 | 310 | `52c46a16a99f` | [runtime-check](../runs/vsv_eval/checkpoint-construction-0908/opus-v3-runtime/runtime-check.json) |

三个包分别包含 12 个应用源文件、6 张原型图及原始任务材料。运行 `node server.js`，cwd 为 `/workspace/app`。
官方镜像 `vision2web:official-577f939` 中 **3/3 首页 HTTP 200、截图非空、初始 console error 为 0**。
每条证据目录保留 `server.log`、`initial.png`、`initial.html`，并以 task 哈希绑定输入。
任务 `mmc-vsv-opus-v1-0908`、`mmc-vsv-opus-v2-0908`、`mmc-vsv-opus-v3-0908` 均已 Succeeded；各 0 GPU / 4 CPU / 16 GiB，无新模型运行。

这三个样本标记为 **`audited_application`**：能恢复审核范围内的应用源文件和原始资源，
不能声称恢复了历史完整 workspace、数据库或浏览器状态。V0 的记录边界在 237，晚于首次检查 232，
本轮不自动倒推这个映射；341 的完整状态仍不以最终代码替代。

加上旧 Kimi 样本，目前是 **4 个可启动 checkpoint / 1 个原始网站任务**。
用途是验证构造及后续同一套 Test → Visual Judgment → Safe Repair；尚不能估计跨任务模型能力。
扩充时按原任务划分数据集，同一网站的不同版本不要跨训练/测试集合；保留可能已正确的状态，不只挑后来修好的状态。

## 可选验证接手入口

`verification_handoff` 仅用于补充 analysis，不替代 `checkpoint_verify` 或官方基线。
复用相同冻结输入和启动准入结果，通过 `prepare_agent_case(..., handoff=True)` 构造新会话：
原始任务指令以哈希核验后作为参考规范；新指令见 [`verification_handoff.txt`](../prompts/vision2web/verification_handoff.txt)。
只要求进行本轮检查、允许证据驱动的局部修复，并返回检查/证据/改动/遗留问题，不要求完成全部交付。
这种范围由 prompt 指导，并非对“局部修复”的程序性保证；是否越界需检查实际轨迹和 diff。

在空 `/workspace` 的独立官方任务容器中运行，模型和端点沿用对应框架已验证的配置：

```bash
cd /data/miyapeng/mmcode/MultimodalCode
python3.12 scripts/vision2web/build_checkpoint_tasks.py handoff \
  --sample data/vision2web/checkpoint_verify/smartrecruiters-opus-0908/samples/frontend__smartrecruiters__943e11eab139 \
  --runtime-check runs/vsv_eval/checkpoint-construction-0908/opus-v1-runtime/runtime-check.json \
  --framework claude_code \
  --model "$VERIFIER_MODEL" --base-url "$VERIFIER_BASE_URL" \
  --api-key-env LLM_API_KEY --timeout 600 \
  --output runs/vsv_eval/verification-handoff/new-run
```

替换成 `--framework openhands` 使用完整原生 BrowserToolSet（browser_enabled）；不注入 guided_vsv 或批量工具。
`LLM_API_KEY` 由运行环境提供，密钥不写入配置。每次运行使用新的输出目录和空 workspace，单会话、有限预算、无自动重试。
构造阶段不启动网站，启动及检查仍由模型自主决定；本轮没有实际执行上述推理命令。

输出 `input.json` 保存有效 prompt、来源及预算；原始 `*.events.jsonl` 保留模型最终交接文本及全部工具调用；
`result.json` 指向被动记录器生成的 timeline、workspace 变化和 `P_final`。
此处 `P_final` 只是 **B 退出时的代码版本**，`success` 只是进程正常结束，都不表示整题已提交或修复正确。
默认不把结果自动交回 A；将来接入 A 时须明确交接消息及代码变更，不能伪造同一模型的历史。

增量实现复用原模块，仅新增一个 prompt 文件。两个 runner 的改动仅限 `verification_handoff` 下不强制生成 start.sh。
本地新增 6 项测试覆盖两框架调用参数、只改副本、密钥不落盘，以及有/无该协议时的交付判定；
假 CLI 测试不是模型能力实验。主流程、官方 prompt、历史样本与评测器保持不变。
