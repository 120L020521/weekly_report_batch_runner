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

Use the calling Agent workspace unless the user supplies another output root.
Make the Task the primary browsing unit; do not create stage-first roots:

```text
<agent_workspace>/
├── <task_key>/
│   ├── xiaoyi_file_runs/  # Runner-native artifacts
│   ├── xiaoyi_judge/      # frozen evidence and judge_result.json
│   └── xiaoyi_halo/       # per-Task HALO evidence and report
└── _xiaoyi_batches/
    └── run_<YYYYMMDD>/    # one batch index per day
```

<<<<<<< HEAD
`<task_key>` is the full `FileOrganization_<n>_<n>` ID for file organization,
`task<ID>` for numeric WorkspaceBench tasks, and the exact
`metadata.absolute_id` for weekly-report tasks. Never add a `task` prefix to a
weekly-report ID. Batch-only
=======
`<task_key>` is the full `FileOrganization_<n>_<n>` ID for file organization
and `task<ID>` for numeric WorkspaceBench or weekly-report tasks. Batch-only
>>>>>>> 874a79aed3a126240813fa65fc8adbbee74439cb
indexes, queues, summaries, and merged HTML belong below `_xiaoyi_batches`; do
not duplicate them into every Task directory. Existing explicit output paths
remain valid for backward compatibility, but new runs use this layout.

Do not write runtime artifacts inside an installed Skill or source dataset.

## Run the selected adapter

Read the selected Runner Skill completely and obey it. Pass only selected IDs,
dataset paths, configuration, and the Agent workspace.

- File organization: run the complete serial 1+3 confirmation lifecycle. For
  each Case, pass `<agent_workspace>` through `--task-artifacts-root`; the
  launcher derives `<case_id>/xiaoyi_file_runs`. After the batch, write the
  unified `runner_batch.json` under
  `_xiaoyi_batches/run_<YYYYMMDD>/`.
- Weekly report: preserve the one-device serial person/task lifecycle, but route
  each Task's Runner output root to
<<<<<<< HEAD
  `<agent_workspace>/<absolute_id>/xiaoyi_file_runs`; write the unified
=======
  `<agent_workspace>/task<ID>/xiaoyi_file_runs`; write the unified
>>>>>>> 874a79aed3a126240813fa65fc8adbbee74439cb
  `weekly_runner_batch.json` under the batch index directory.
- WorkspaceBench: preserve serial execution and exact dataset bindings. Route
  each Task's `--logs-dir` to
  `<agent_workspace>/task<ID>/xiaoyi_file_runs`; record the terminal Runner
  handoff under the batch index directory.

For `RUNNER_ONLY`, return the Runner handoff and stop. Metadata rubrics never
make a Runner-only task fail and never implicitly enable Judge.

## Build one unified Judge batch

For either Judge terminal stage, read `judge-xiaoyi-results/SKILL.md` completely.
After every selected Runner item is terminal, write one UTF-8
`<agent_workspace>/_xiaoyi_batches/run_<YYYYMMDD>/judge_batch.json`:

```json
{
  "schema_version": 1,
  "producer": "run-xiaoyi",
  "runner_finished": true,
  "run_id": "20260817",
  "artifact_root": "D:/workspace",
  "judge_root": "D:/workspace/_xiaoyi_batches/run_20260817",
  "tasks": [
    {
      "task_id": "21",
      "adapter": "weekly-report",
      "runner_status": "completed",
      "execution_outcome": "completed",
      "evidence_ready": true,
      "metadata": "D:/workspace/task/何沐/21/metadata.json",
      "data": "D:/workspace/task/何沐/data",
<<<<<<< HEAD
      "outputs": "D:/workspace/21/xiaoyi_file_runs/outputs",
      "runner_dir": "D:/workspace/21/xiaoyi_file_runs",
      "trace": "D:/workspace/21/xiaoyi_file_runs/21.jsonl",
      "task_root": "D:/workspace/21",
      "judge_dir": "D:/workspace/21/xiaoyi_judge",
      "halo_dir": "D:/workspace/21/xiaoyi_halo"
=======
      "outputs": "D:/workspace/task21/xiaoyi_file_runs/task21/outputs",
      "runner_dir": "D:/workspace/task21/xiaoyi_file_runs/task21",
      "trace": "D:/workspace/task21/xiaoyi_file_runs/task21/task21.jsonl",
      "task_root": "D:/workspace/task21",
      "judge_dir": "D:/workspace/task21/xiaoyi_judge",
      "halo_dir": "D:/workspace/task21/xiaoyi_halo"
>>>>>>> 874a79aed3a126240813fa65fc8adbbee74439cb
    }
  ]
}
```

Write every path field explicitly and absolutely; use `null` only for optional
`data`, `runner_dir`, or `trace`. Also write the Runner-native terminal state as
`execution_outcome` and whether frozen Judge inputs are complete as
`evidence_ready`. Normalize Runner state to `completed`, `failed`, `timeout`,
`unknown`, or `not-run`. Copy paths and state from the Runner handoff; never scan
for substitutes. For the task-centric layout, keep every `task_root` unique and
below `artifact_root`, require `judge_dir = <task_root>/xiaoyi_judge`, and require
`halo_dir = <task_root>/xiaoyi_halo`. `judge_root` contains batch-only files.
<<<<<<< HEAD

For weekly reports, keep `runner_dir` and `trace` in the batch/queue only for
orchestration and HALO. The Judge Prepare step must not copy them into
`xiaoyi_judge/`. Keep Runner-collected worklog and summary outside `outputs`, and never pass,
copy, inspect, or score them in weekly Judge.
=======
>>>>>>> 874a79aed3a126240813fa65fc8adbbee74439cb

For file organization, map `complete` to `runner_status = completed`; map
`incomplete-after-3-continues` and `execution-error` to `runner_status = failed`,
while preserving the exact value in `execution_outcome`. Copy `runnerDir`,
`trace`, and `evidenceReady` directly to `runner_dir`, `trace`, and
`evidence_ready`. A valid final directory snapshot may therefore be Judgeable
even when XiaoYi's dialogue did not complete. Trace presence is independent: a
null Trace does not block Judge but makes that task ineligible for HALO.

For a legacy or non-file Runner handoff without an explicit readiness flag, set
`evidence_ready = (runner_status == "completed")`. Existing schema-v1 Judge
batches without `execution_outcome` or `evidence_ready` remain accepted by the
Judge with the same fallback.

Invoke the shared Judge once for this whole batch. It performs one common
Prepare, creates the common result layout, dispatches the adapter-specific
evaluator, validates each result, and writes `batch_summary.json`. Do not run
Judge on `runner-failure` or `input-error` entries. These statuses mean the frozen
evidence itself is unavailable or invalid, not merely that the Runner's business
outcome was unsuccessful.

For `RUNNER_JUDGE`, return the Runner handoff, `judge_batch.json`, Judge queue,
and batch summary, then stop.

## Continue directly into HALO

For `RUNNER_JUDGE_DIAGNOSE`, read `halo-rlm-agent-driven/SKILL.md` completely
after the Judge batch is terminal. Pass the exact `judge_queue.json` and the
current `_xiaoyi_batches/run_<YYYYMMDD>` directory as its batch output root.
HALO must honor each queue row's `haloDir` for per-Task artifacts. Do not create
a second handoff file.

HALO reads the common Judge queue, assigns each Trace-bearing task to one fresh
diagnosis subagent, preserves failure isolation, and merges valid per-task
reports into one HTML. The queue already contains each task's adapter, Runner
status, native execution outcome, evidence readiness, raw Trace, prepared Judge
directory, and result path. HALO owns the
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
