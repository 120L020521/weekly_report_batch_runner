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
| `xiaoyi-weekly-report` | 执行周报、日报及指定时间段报告任务；按人员串行处理任务并收集报告、worklog 和 Trace。 |
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

一次批次只能选择一种场景。周报任务的 Task ID 必须在所有人员之间全局唯一。

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
  xiaoyi_file_runs/   # 文件整理 Runner
  xiaoyi_logs/        # 周报和 WorkspaceBench Runner 日志、输出
  xiaoyi_judge/       # Judge 批次与结果
  xiaoyi_halo/        # HALO 诊断结果和 HTML
```

关键批次文件：

- 周报 Runner：`xiaoyi_logs/weekly_runner_batch.json`
- 统一 Judge 输入：`xiaoyi_judge/<run_id>/judge_batch.json`
- Judge 后续队列：`xiaoyi_judge/<run_id>/judge_queue.json`
- Judge 汇总：`xiaoyi_judge/<run_id>/batch_summary.json`
- HALO 汇总：位于 `xiaoyi_halo/` 下的合并诊断 HTML

所有数据和运行产物都应位于外部 workspace，不要写入已安装的 Skill 目录。

## 单独调用子 Skill

一般应使用 `run-xiaoyi`。只有在已经明确任务场景和终止边界时才直接调用子 Skill，例如：

- 只运行周报 Runner：`xiaoyi-weekly-report`
- 只运行文件整理 Runner：`xiaoyi-auto-continue`
- 只运行数字任务 Runner：`run-xiaoyi-loop`
- 对现有统一批次评分：`judge-xiaoyi-results`
- 对现有 Judge 队列或单条 JSONL Trace 诊断：`halo-rlm-agent-driven`

