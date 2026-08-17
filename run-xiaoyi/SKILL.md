---
name: run-xiaoyi
description: >-
  Route and orchestrate XiaoYi tasks through one user-facing control flow:
  scenario detection, the matching Runner, unified Judge preparation and
  adapter-specific evaluation, then generic HALO diagnosis and merged HTML.
  Use for FileOrganization file-organizing tasks, numeric WorkspaceBench tasks,
  weekly-report tasks, Runner-only execution, Judge requests, or full
  Runner-Judge-HALO runs. Runner-only is the default unless Judge or diagnosis
  is explicitly requested.
---

# Run XiaoYi

Act as the only user-facing controller. Keep each mechanical stage in its child
Skill and pass explicit artifact paths between stages. Never start Judge before
the entire selected Runner batch is terminal. Never start diagnosis before the
entire Judge batch is terminal.

## Select the terminal stage

- Default or explicit “只执行 Runner”: `RUNNER_ONLY`.
- Explicit Judge/评分/打分: `RUNNER_JUDGE`.
- Explicit HALO/诊断/Trace diagnosis: `RUNNER_JUDGE_DIAGNOSE`; diagnosis implies
  Judge unless the user supplies existing Judge and Trace artifacts.
- Contradictory “只运行” and Judge/diagnosis wording requires one clarification
  before any HDC mutation.

## Detect exactly one adapter

| Adapter | Decisive evidence | Runner Skill |
| --- | --- | --- |
| `file-organization` | `FileOrganization_<n>_<n>` plus setup.json, expect.json, source/, prompt TXT | `xiaoyi-auto-continue` |
| `weekly-report` | 周报/日报, `deliverables_final/`, or metadata `adapter: weekly-report` plus person | `xiaoyi-weekly-report` |
| `workspacebench` | numeric IDs and metadata with non-empty task and rubrics | `run-xiaoyi-loop` |

Schema wins over folder names and vague wording. Preserve full FileOrganization
IDs. Reject a mixed-adapter batch. For weekly reports, require globally unique
Task IDs across people.

Before dispatch, state the selected adapter and terminal stage in one sentence.

## Runtime artifacts

Use the calling Agent workspace unless the user supplies another output root:

```text
<agent_workspace>/xiaoyi_file_runs/   # file-organization Runner
<agent_workspace>/xiaoyi_logs/        # weekly/workspace Runner evidence
<agent_workspace>/xiaoyi_judge/       # unified Judge input/output
<agent_workspace>/xiaoyi_halo/        # diagnosis and HTML
```

Do not write runtime artifacts inside an installed Skill or source dataset.

## Run the selected adapter

Read the selected Runner Skill completely and obey it. Pass only selected IDs,
dataset paths, configuration, and the Agent workspace.

- File organization: run the complete serial 1+3 confirmation lifecycle and
  receive `runner_batch.json`.
- Weekly report: run the one-device serial person/task lifecycle and receive
  `weekly_runner_batch.json` with `runnerFinished = true`.
- WorkspaceBench: start the selected batch once and receive its terminal Runner
  handoff with exact metadata, data, output, Runner-log, and Trace paths.

For `RUNNER_ONLY`, return the Runner handoff and stop. Metadata rubrics never
make a Runner-only task fail and never implicitly enable Judge.

## Build one unified Judge batch

For either Judge terminal stage, read `judge-xiaoyi-results/SKILL.md` completely.
After every selected Runner item is terminal, write one UTF-8
`<agent_workspace>/xiaoyi_judge/<run_id>/judge_batch.json`:

```json
{
  "schema_version": 1,
  "producer": "run-xiaoyi",
  "runner_finished": true,
  "run_id": "20260817",
  "judge_root": "D:/workspace/xiaoyi_judge/20260817",
  "tasks": [
    {
      "task_id": "21",
      "adapter": "weekly-report",
      "runner_status": "completed",
      "metadata": "D:/workspace/task/何沐/21/metadata.json",
      "data": "D:/workspace/task/何沐/data",
      "outputs": "D:/workspace/xiaoyi_logs/task21/outputs",
      "runner_dir": "D:/workspace/xiaoyi_logs/task21",
      "trace": "D:/workspace/xiaoyi_logs/task21/task21.jsonl",
      "judge_dir": "D:/workspace/xiaoyi_judge/20260817/task21"
    }
  ]
}
```

Write every path field explicitly and absolutely; use `null` only for optional
`data`, `runner_dir`, or `trace`. A completed Runner requires metadata and
outputs. Normalize Runner state to `completed`, `failed`, `timeout`, `unknown`,
or `not-run`. Copy paths from the Runner handoff; never scan for substitutes.
Keep every `judge_dir` unique and below `judge_root`.

Invoke the shared Judge once for this whole batch. It performs one common
Prepare, creates the common result layout, dispatches the adapter-specific
evaluator, validates each result, and writes `batch_summary.json`. Do not run
Judge on `runner-failure` or `input-error` entries.

For `RUNNER_JUDGE`, return the Runner handoff, `judge_batch.json`, Judge queue,
and batch summary, then stop.

## Continue directly into HALO

For `RUNNER_JUDGE_DIAGNOSE`, read `halo-rlm-agent-driven/SKILL.md` completely
after the Judge batch is terminal. Pass the exact `judge_queue.json` and
`<agent_workspace>/xiaoyi_halo` output root to its batch-diagnosis workflow.
Do not create a second handoff file.

HALO reads the common Judge queue, assigns each Trace-bearing task to one fresh
diagnosis subagent, preserves failure isolation, and merges valid per-task
reports into one HTML. The queue already contains each task's adapter, Runner
status, raw Trace, prepared Judge directory, and result path. HALO owns the
adapter-to-editable-surface policy; do not add another resolver or handoff layer.

The complete full-flow boundary is:

```text
scenario -> adapter Runner -> unified judge_batch.json -> common Prepare
         -> adapter-specific evaluator -> common judge_result.json
         -> HALO task subagents -> merged HTML
```

## Existing artifacts

- Existing unified `judge_batch.json`: skip Runner and enter
  `judge-xiaoyi-results`.
- Existing unified `judge_queue.json`: skip Runner/Judge and enter HALO batch
  diagnosis directly.
- One explicitly selected raw JSONL without batch context: route directly to
  `halo-rlm-agent-driven` and do not mutate HDC state.

After completion, report only stages that actually ran and their authoritative
handoff/result paths.
