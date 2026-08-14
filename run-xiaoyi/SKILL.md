---
name: run-xiaoyi
description: >-
  Route and orchestrate XiaoYi Runner, Judge, and HALO diagnosis requests from one
  user-facing entry. Run only the Runner by default; append the shared batch Judge
  only when the user explicitly requests Judge/评分/打分, and append embedded HALO
  diagnosis only when the user explicitly requests 诊断/HALO/Trace diagnosis. Use
  for generic “让小艺执行任务”, 文件整理任务/FileOrganization IDs backed by
  setup.json/expect.json/source/prompts, Task/task/WorkspaceBench requests backed
  by numeric IDs and metadata.json, person-grouped 周报/日报 requests backed by
  adapter: weekly-report metadata and deliverables_final data, existing
  handoff.json batches, or standalone OTel/OpenTelemetry JSONL trace diagnosis.
  Resolve the requested terminal stage, wording, ID shape, adapter field, and
  dataset schema before any HDC mutation.
---

# Route XiaoYi Runner Stages

Act as the single user-facing control entry. Select exactly one dataset Runner
when execution is requested, then execute only the stages explicitly requested.
The default terminal stage is Runner, not Judge. HALO's detailed diagnosis
contract and local tools are embedded resources of this Skill and load only for
an actual diagnosis stage.

Never start a Judge subagent while a selected Runner case or Task is pending.
Never run multiple execution children for one selector. Do not copy their mechanical
HDC rules or the shared Judge's scoring rules into this coordinator.

## Internal modes

The normal user-facing mode performs the routing rules below. The private
`halo-diagnose-one` mode is used only by `run-xiaoyi-halo-loop` diagnosis workers
or when this Skill has already isolated exactly one trace. In that mode, skip
dataset routing, Runner, Prepare, and Judge; read
`references/halo-diagnosis.md` completely and diagnose only the supplied trace.
This private-mode selection takes precedence over every later wording, ID, and
stage rule and is terminal: never invoke `run-xiaoyi-halo-loop`, a Runner, or a
Judge from inside it.

For a user request that diagnoses existing artifacts without running XiaoYi:

- route a valid batch `handoff.json` to `run-xiaoyi-halo-loop`, starting at its
  handoff-resolution phase;
- route an explicitly selected JSONL file or trace directory to the embedded
  HALO workflow below;
- never mutate HDC state for either path.

## Choose the dataset adapter

| Mode | Runner child | Distinguishing contract |
| --- | --- | --- |
| `FILE_ORGANIZATION` | `xiaoyi-auto-continue` | `FileOrganization_*`; setup.json, expect.json, source/, prompt TXT; serial Agent-driven 1+3 confirmation loop |
| `WEEKLY_REPORT` | `xiaoyi-weekly-report` | numeric IDs with `adapter: weekly-report`; `task/<person>/<id>/metadata.json` plus `deliverables_final/<person>`; one push and clear lifecycle per person |
| `WORKSPACE_BENCH` | `run-xiaoyi-loop` | non-negative numeric IDs; metadata.json with task and rubrics; one batch Runner start plus prepared evidence |

`Judge`, 评分, 打分, 诊断, and HALO are stage modifiers, never dataset-routing
signals.

## Choose the terminal stage independently

Resolve one stage before dispatch:

| Stage | User intent | Sequence |
| --- | --- | --- |
| `RUNNER_ONLY` | default; “运行/执行 XXX 任务，数据在……” or explicit “只运行/不要 Judge” | Runner only |
| `RUNNER_JUDGE` | explicit “并进行 Judge/评分/打分” | complete batch Runner → complete batch Judge |
| `RUNNER_JUDGE_DIAGNOSE` | explicit “并进行 Judge 和诊断”, HALO, or Trace diagnosis | complete batch Runner → Judge → diagnosis |

Do not infer Judge merely because metadata contains `rubrics`, because the user
provided a Judge-capable dataset, or because Runner produced Judgeable artifacts.
Do not infer diagnosis from ordinary words such as “查看结果” or “分析任务列表”.
Diagnosis requires Judge evidence, so an explicit diagnosis request implies the
Judge stage even when the user omits the word Judge. Explicit “只运行” always
stops after Runner. If one request contains both “只运行/不要 Judge” and an
explicit Judge or diagnosis instruction, treat the requested terminal stage as
contradictory and ask one question before HDC rather than guessing.

## Route in three passes

### 1. Choose a wording candidate

Choose `FILE_ORGANIZATION` when the user supplies any of:

- “文件整理任务”“文件整理 case”“整理文件任务”；
- “自动处理二次确认”“确认后继续”“最多继续三次”；
- `selected_cases.json` together with `test_file` and `prompts` paths.

Choose `WORKSPACE_BENCH` when the user supplies any of:

- `Task` or `task` with numeric selectors such as `112`, `14 25`, `1-10`, or
  `1..10`;
- `Workspace-Bench` or numeric Task data using metadata.json and rubrics;
- dataset folders named task, task1, or filestask with numeric case directories.

Choose `WEEKLY_REPORT` as the wording candidate when the user supplies any of:

- “周报生成任务”“日报生成任务”“按人员批跑周报”；
- a `deliverables_final/<person>` data directory together with numeric report metadata;
- a request to push one person's files, calendar, and memos once and then run that
  person's report Tasks serially.

Treat standalone `task` as weak evidence. “Task FileOrganization_0_003” remains
file organization.

### 2. Confirm every selected ID

Load a supplied case-list JSON before routing and check every entry:

- `^FileOrganization_[0-9]+_[0-9]+$` confirms file organization;
- non-negative integers, numeric ranges, and comma-separated numeric selectors
  confirm only a numeric metadata family; inspect `adapter` and schema to choose
  between Weekly Report and WorkspaceBench;
- a list containing both shapes is mixed mode—stop before HDC and ask the user
  to separate the batches;
- arbitrary strings confirm neither mode, so continue to schema inspection.

Never reinterpret the numeric suffix of a FileOrganization ID as a Task ID.

### 3. Verify the scoped schema

Confirm file organization execution when every selected case resolves to:

```text
<test_file_base>/<case_id>/setup.json
<test_file_base>/<case_id>/expect.json
<test_file_base>/<case_id>/source/
<prompts_dir>/<case_id>.txt
```

`<test_file_base>/<case_id>/metadata.json` is a later Judge input. Its absence
does not change the Runner route or prevent execution; the batch Judge records a
case-level input error after the Runner batch finishes.

Confirm WorkspaceBench when every selected numeric Task resolves to metadata.json
with a non-empty `task` and non-empty string `rubrics`, and no selected metadata
declares another adapter. Treat folder names only as hints: `filestask` may still
be WorkspaceBench.

Confirm Weekly Report when every selected numeric Task resolves to
`task/<person>/<id>/metadata.json` with `adapter: weekly-report`, `person` equal to
the containing directory, a non-empty `task`, and non-empty string `rubrics`, and
`deliverables_final/<person>/` exists. An explicit adapter takes precedence over
numeric ID shape and generic `task` directory wording. A selected batch mixing
weekly-report metadata with generic WorkspaceBench metadata is mixed mode—stop
before HDC and ask the user to separate it.

Prefer concrete schema over generic wording. Use ID shape only when schema is
incomplete but not contradictory. Ask one routing question before HDC when ID
and schema contradict or multiple modes remain plausible.

## Runtime artifact policy

Resolve `<agent_workspace>` as the calling Agent's runtime workspace. Keep all
generated artifacts there by default, never under an installed Skill or dataset:

```text
<agent_workspace>/xiaoyi_file_runs/run_<YYYYMMDD>/ # FileOrganization Runner
<agent_workspace>/xiaoyi_logs/                     # WorkspaceBench Runner
<weekly_project>/xiaoyi_logs/                       # Weekly Report Runner
<agent_workspace>/xiaoyi_judge/                    # shared Judge
<agent_workspace>/xiaoyi_halo/                     # HALO
```

Explicit user output paths win. Preserve one date-only FileOrganization `run_id`
formatted exactly as `YYYYMMDD` for the whole daily batch; never append a time or
uniqueness suffix.

## Dispatch file organization

1. Read and apply `xiaoyi-auto-continue` completely.
2. Pass the exact case list, dataset, prompts, config, and `<agent_workspace>`.
3. Let it finish each case's complete 1+3 loop serially and exhaust the host
   queue. Never call Runner batch mode.
4. Receive and verify `<run_dir>/runner_batch.json`, containing every selected
   case's terminal execution outcome, metadata path, and final outputs path.
5. For `RUNNER_ONLY`, return the handoff and stop. Do not read or invoke a Judge.
6. For `RUNNER_JUDGE`, read `judge-xiaoyi-results` completely and invoke its
   file-organization batch adapter once for the completed `runner_batch.json`.
   The Judge may run one case per subagent concurrently, but only after all
   selected Runner cases are terminal.
7. `RUNNER_JUDGE_DIAGNOSE` is not supported for FileOrganization while the
   diagnosis handoff accepts only numeric Task IDs. Detect this before HDC and
   ask whether the user wants Runner + Judge instead; never silently omit the
   requested diagnosis.

## Dispatch Weekly Report

Weekly Report currently supports `RUNNER_ONLY` only. If the resolved terminal
stage is `RUNNER_JUDGE` or `RUNNER_JUDGE_DIAGNOSE`, stop before HDC and state that
the weekly-report Judge/HALO adapter is not yet implemented. Ask whether the user
wants Runner-only; never send weekly metadata into the WorkspaceBench Judge,
Prepare, or HALO paths merely because its IDs are numeric and it has rubrics.

For Runner-only:

1. Read and apply `xiaoyi-weekly-report` completely.
2. Pass the exact external data root containing `task/` and `deliverables_final/`,
   plus selected people, numeric Task IDs, device, and runtime flags. The child
   bundles its own Runner and lifecycle scripts. Do not broaden a subset.
3. Let it complete the person lifecycle serially: clear → push once → run all
   selected Tasks → fetch evidence → clear. Never invoke the child once per Task,
   because that would resend and clear one person's shared data eight times.
4. Receive and verify
   `<data_root>/xiaoyi_logs/weekly_runner_batch.json` unless the user selected
   another output root. Do not expect a weekly-report `_runs` directory.
   Require `adapter: weekly-report`, `runnerFinished: true`, and exactly one entry
   for each selected Task.
5. Return the handoff and stop. Do not Prepare, Judge, diagnose, or reinterpret
   the report rubrics in the coordinator.

## Dispatch WorkspaceBench

1. Preserve exact directory-to-ID bindings and start Runner exactly once for the
   selected batch.
2. For `RUNNER_ONLY`, apply `run-xiaoyi-loop` in `runner-only` mode. Wait until
   every Task has a terminal Runner outcome, return raw Trace/output paths, and
   stop without Prepare or Judge.
3. For `RUNNER_JUDGE`, apply `run-xiaoyi-loop` in
   `runner-and-prepare-only` mode. Wait until every selected Task is prepared or
   has a recorded Runner/prepare failure, then apply `judge-xiaoyi-results` once
   to the whole WorkspaceBench batch.
4. For `RUNNER_JUDGE_DIAGNOSE`, apply `run-xiaoyi-halo-loop`; it owns the explicit
   Runner → Judge → diagnosis sequence and must not duplicate Runner or Judge.
   Its per-Task diagnosis workers call this Skill in `halo-diagnose-one` mode;
   they do not resolve a separate HALO Skill.

## Run embedded HALO diagnosis

Enter this workflow only for an explicit diagnosis stage, a trace-only request,
or private `halo-diagnose-one` mode. Do not load it for Runner-only or
Runner+Judge requests.

1. Read `references/halo-diagnosis.md` completely before preparing or inspecting
   a trace. It is the authoritative evidence, classification, report, and
   validation contract.
2. Resolve `<run_xiaoyi_root>/scripts/halo` as `<halo_scripts>`. Run
   `prepare_trace.py` from that directory and run every `python -m halo_rlm...`
   command with `<halo_scripts>` as the working directory.
3. For private `halo-diagnose-one`, accept exactly one raw JSONL and the exact
   output/context paths supplied by `run-xiaoyi-halo-loop`. Never scan a sibling
   Task or substitute a batch directory for the exact trace.
4. For a direct trace-only directory request, let `prepare_trace.py` enumerate
   only that explicitly selected directory, then diagnose each returned manifest
   entry once.
5. Write generated artifacts only under the resolved HALO output root and finish
   only after the embedded contract's manifest-bound report validation succeeds.

## Judge existing artifacts only

When the user explicitly asks only to Judge existing artifacts, skip both
Runners. Route by the supplied IDs and artifact schema, then invoke only the
matching `judge-xiaoyi-results` adapter.

## Examples

| Request | Route | Sequence |
| --- | --- | --- |
| “执行文件整理任务，列表在 selected_cases.json” | file organization | serial 1+3 Runner only |
| “执行文件整理任务，并进行 Judge” | file organization | serial 1+3 Runner for all cases → parallel batch Judge |
| “执行何沐的 8 个七月周报任务” with weekly-report metadata | weekly report | one person lifecycle, Runner only |
| “执行 Task 21，metadata.adapter 是 weekly-report” | weekly report | Weekly Report Runner only; numeric ID does not imply WorkspaceBench |
| “执行周报任务并评分” | weekly report | stop before HDC; ask whether Runner-only is acceptable |
| “执行 Task 112、117 并完成 Judge” | WorkspaceBench | one batch Runner/prepare → parallel Agent Judge |
| “执行 Task 112、117，数据在 D:\\tasks” | WorkspaceBench | one batch Runner only; no Prepare/Judge |
| “执行 filestask 下的 39” with numeric metadata | WorkspaceBench | folder name does not override numeric ID/schema |
| “执行 Task FileOrganization_0_003” | file organization | standalone Task is weak; FileOrganization ID wins |
| “执行 Task 112，Judge 后做 HALO 诊断” | WorkspaceBench HALO | run → Judge → HALO |
| “诊断 D:\\logs\\task112.jsonl，不要运行小艺” | embedded HALO | trace-only diagnosis; no HDC/Runner/Judge |

Before dispatch, state the selected mode and decisive signals in one sentence.
After completion, report only stages that actually ran. Never imply Judge or
diagnosis ran merely because their input artifacts are available.
