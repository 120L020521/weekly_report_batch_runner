# 小艺批跑 Skills

本目录包含小艺任务的统一批跑、评分和诊断 Skills。通常只需要让 Agent 使用 `run-xiaoyi`；它会识别任务场景并调用对应的子 Skill。

## 流程

```text
run-xiaoyi
  -> 识别任务场景
  -> 对应 Runner
  -> 生成统一 judge_batch.json
  -> judge-xiaoyi-results
  -> halo-rlm-agent-driven（需要诊断时）
  -> 合并诊断 HTML
```

Runner 必须完成整个选定批次后才能进入 Judge；Judge 必须完成整个批次后才能进入 HALO。

## Skill 分工

| Skill | 职责 |
| --- | --- |
| `run-xiaoyi` | 面向用户的统一入口。识别场景，控制流程停止在 Runner、Judge 或 HALO。 |
| `xiaoyi-weekly-report` | 执行周报、日报及指定时间段报告任务；每条 Task 先由 note 脚本清空并推送数据，再串行执行并收集报告、worklog、summary 和 Trace。 |
| `xiaoyi-auto-continue` | 执行文件整理任务，并保留既定的二次确认与继续对话逻辑。 |
| `run-xiaoyi-loop` | 执行 WorkspaceBench 或其他数字编号、带 `metadata.json` 的兼容任务。 |
| `judge-xiaoyi-results` | 读取统一 `judge_batch.json`，准备证据并按场景选择评估器，输出统一 Judge 结果。 |
| `halo-rlm-agent-driven` | 根据 Judge 结果和原始 Trace 诊断任务；支持每个任务由独立子 Agent 分析并合并 HTML。 |

`run-xiaoyi-loop` 不是总入口，也不用于周报或文件整理。

## 场景识别

| 场景 | 主要特征 | Runner |
| --- | --- | --- |
| 周报/日报 | `deliverables_final/`、人员目录，或 metadata 中的 `adapter: weekly-report` | `xiaoyi-weekly-report` |
| 文件整理 | `FileOrganization_<n>_<n>`，并包含 setup、expect、source 和任务提示文件 | `xiaoyi-auto-continue` |
| WorkspaceBench | 数字 Task ID，且 `metadata.json` 包含非空 `task` 和 `rubrics` | `run-xiaoyi-loop` |

一次批次只能选择一种场景。周报任务只使用 `metadata.absolute_id` 命名；该值
可以是非数字，但必须路径安全、与 Task 目录名一致，并在所有人员之间全局唯一。
周报日志名为 `<absolute_id>.jsonl`，HALO 诊断目录名为
`<absolute_id>_halo`，不会添加 `task` 前缀。
周报 worklog 和同级 summary 分别保存在 Runner 的独立 `worklog/`、`summary/` 目录，仅供用户查看；Judge 不复制或检查它们。

## 周报 note 快速说明

正常批跑不需要人工单独运行 note。`xiaoyi-weekly-report` 会在每个待执行任务前
调用仓库内的：

```text
note/data_yangshi/jiaoben/run_data_mock.py <target>
```

固定顺序为：

```text
note 清空+推送 → 执行小艺 → 拉取 Trace、周报、worklog 和 summary
```

`run_data_mock.py` 先调用 `change_file.py` 更新时间并生成 `static_file.zip`，再调用
`make_data.py` 清理设备旧数据、生成 mock 响应并推送当前数据。Runner 按人员和
“第一周/第二周”自动选择 `z1..f2`；也可在 metadata 中显式写 `mock_target`。
当前 note 只声明周泽宇、苏晚、唐可、陈景明、方一诺的第一周和第二周，其他人员
或周次必须先准备对应 note 数据与 target。当前仓库还有一个已知数据路径问题：
`z2` 声明为 `new/周泽宇/第二周`，实际目录为 `new/zhouzeyu/第二周`，因此 `z2`
暂不可用；由 note 维护者统一路径后才能运行。

`make_data.py` 内仍使用旧绝对路径 `D:\Code\Personal\note`。不修改 note 源码时，
每台运行机需一次性把该路径建立为指向当前仓库 `note` 的目录联接：

```powershell
New-Item -ItemType Directory -Path "D:\Code\Personal" -Force
New-Item -ItemType Junction `
  -Path "D:\Code\Personal\note" `
  -Target "D:\codes\weekly-report-batch-runner\note"
```

创建前若 `D:\Code\Personal\note` 已存在，先检查它；不要覆盖普通目录或错误联接。
仓库移动后需要重建联接。此配置每台机器只做一次，不是每个任务都做。

只查看和调试 note target 时可运行：

```powershell
python -B note/data_yangshi/jiaoben/run_data_mock.py --list
python -B note/data_yangshi/jiaoben/run_data_mock.py c1
```

第二条命令会真实清理并推送设备数据，只用于调试；正常批跑交给 Runner 调用。

## 执行阶段

`run-xiaoyi` 根据用户要求选择终止阶段：

- 未特别说明或明确“只执行 Runner”：只执行 Runner，不评分、不诊断。
- 明确要求 Judge、评分或打分：执行 Runner 和 Judge。
- 明确要求 HALO、诊断或 Trace 分析：执行 Runner、Judge 和 HALO。
- 已有 `judge_batch.json`：可以跳过 Runner，从 Judge 开始。
- 已有 `judge_queue.json`：可以跳过 Runner 和 Judge，直接进入 HALO。

Runner-only 不根据 rubrics 判断任务成功或失败；rubrics 由 Judge 使用。

## 对 Agent 的说法

周报完整流程：

```text
使用 run-xiaoyi 执行 <数据目录> 中 <人员和任务 ID> 的周报任务。
所有 Runner 任务完成后统一执行 Judge，再对有 Trace 的任务执行 HALO，并生成合并 HTML。
```

周报只执行 Runner：

```text
使用 run-xiaoyi 执行 <数据目录> 中 <人员和任务 ID> 的周报任务，只执行 Runner，不执行 Judge 和 HALO。
```

文件整理完整流程：

```text
使用 run-xiaoyi 执行 <数据目录> 中指定的文件整理任务，完成 Runner、Judge 和 HALO。
```

WorkspaceBench 指定任务：

```text
使用 run-xiaoyi 执行 <数据目录> 中的 Task 14、15 和 17，并完成 Judge；不执行 HALO。
```

## 数据与运行产物

业务数据不要放进 Skill。将数据目录和选定任务明确告诉 Agent。

未指定输出根目录时，运行产物默认写入 Agent workspace：

```text
<agent_workspace>/
  <task_key>/
    xiaoyi_file_runs/ # Runner 产物
    xiaoyi_judge/     # Judge 产物
    xiaoyi_halo/      # HALO 单任务产物
  _xiaoyi_batches/
    run_<YYYYMMDD>/   # 批次索引、队列、汇总与合并 HTML
```

关键批次文件：

- 周报 Runner：`_xiaoyi_batches/run_<YYYYMMDD>/weekly_runner_batch.json`
- 统一 Judge 输入：`_xiaoyi_batches/run_<YYYYMMDD>/judge_batch.json`
- Judge 后续队列：`_xiaoyi_batches/run_<YYYYMMDD>/judge_queue.json`
- Judge 汇总：`_xiaoyi_batches/run_<YYYYMMDD>/batch_summary.json`
- HALO 汇总：`_xiaoyi_batches/run_<YYYYMMDD>/batch_diagnosis_report.html`

所有数据和运行产物都应位于外部 workspace，不要写入已安装的 Skill 目录。

## 单独调用子 Skill

一般应使用 `run-xiaoyi`。只有在已经明确任务场景和终止边界时才直接调用子 Skill，例如：

- 只运行周报 Runner：`xiaoyi-weekly-report`
- 只运行文件整理 Runner：`xiaoyi-auto-continue`
- 只运行数字任务 Runner：`run-xiaoyi-loop`
- 对现有统一批次评分：`judge-xiaoyi-results`
- 对现有 Judge 队列或单条 JSONL Trace 诊断：`halo-rlm-agent-driven`
