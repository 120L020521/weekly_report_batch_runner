---
name: xiaoyi-weekly-report
description: >-
  Run HarmonyOS XiaoYi weekly-report and daily-report batches from external
  per-person deliverables_final data and numeric metadata.json tasks marked
  adapter: weekly-report. Use for Runner-only report generation that must push
  each person's files, calendar, and memos once; execute that person's selected
  tasks serially; pull log-declared reports and worklogs; fetch evidence; and
  clear the device before the next person. All executable code and default
  configuration are bundled in this Skill; task and deliverable data remain external.
---

# Run XiaoYi Weekly Reports

Use `scripts/run_weekly.py` as the only launcher. It owns HDC interaction,
prompt submission, JSONL monitoring, artifact pulling, and person-data lifecycle.
Do not reproduce those steps manually.

## Resolve external data

Resolve `<data_root>` to a user-supplied directory containing:

```text
<data_root>/
├── deliverables_final/
│   └── <person>/
└── task/
    └── <person>/
        └── <numeric_id>/metadata.json
```

Do not require `xiaoyi_batch_runner/` or a project-level `scripts/` directory.
All runtime scripts and default settings are inside this Skill. Keep
`deliverables_final/`, `task/`, and generated `xiaoyi_logs/` outside the Skill.

Require every selected `metadata.json` to contain:

- `adapter` equal to `weekly-report`;
- `person` equal to the containing person directory;
- `absolute_id` equal to the numeric Task directory;
- a non-empty `task` string;
- a non-empty string list in `rubrics`.

Reject `FileOrganization_*`, setup/expect/source datasets, and generic numeric
WorkspaceBench metadata without `adapter: weekly-report`.

## Preserve the person lifecycle

Execute people and Tasks strictly serially on one device. Before the first active
person, force-stop XiaoYi once, clear the configured calendar range, memos,
Desktop, Documents, and Download, then push that person's data. For each person:

1. Push that person's files, calendar, and memos exactly once.
2. Run selected Tasks in numeric order without clearing or re-pushing between Tasks.
3. Append the configured desktop-output suffix to every XiaoYi prompt.
4. Pull the current JSONL log, parse only bytes after the Task baseline, map
   log-declared output paths, and pull only declared files or directories into
   `<output_root>/task<numeric_id>/outputs/`.
5. After each Task's log and artifacts are safely local, force-stop XiaoYi exactly
   once. Start the next Task through `PCAgentTaskAbility`; do not clear or push
   again when it belongs to the same person.
6. Fetch device calendar and memo evidence plus the local source-file mirror into
   `<metadata_root>/<person>/data/` after the person's selected Tasks are terminal.
7. Clear the person's device data before continuing to the next person. Run all
   fetch, clear, and subsequent push calls to `BatchToolExecuteAbility` with
   `--keep-app-running`; never force-stop between those lifecycle substeps.
8. When the previous person's final clear succeeded, skip the next person's
   initial clear and push the next data directly. Retry the initial clear only
   when the previous cleanup failed.

Never parallelize people or Tasks. Require a JSONL baseline. Do not use Desktop,
Documents, or Download directory snapshots. If the current log does not declare
a usable report path, fail the Task instead of scanning a directory and guessing.
Do not explicitly relaunch XiaoYi between lifecycle substeps. Starting the next
Task through `PCAgentTaskAbility` is the relaunch point.

## Execute

Preview external metadata without touching HDC:

```powershell
& <python> -B "<skill_root>\scripts\run_weekly.py" `
  --project-root "<data_root>" --list
```

Run a selected batch:

```powershell
& <python> -B "<skill_root>\scripts\run_weekly.py" `
  --project-root "<data_root>" `
  --person "<person>" `
  --task "<numeric_id>" `
  --log-hdc
```

Repeat `--person` and `--task` as required. Omit both only when the user explicitly
selects the complete dataset. Preserve the exact selection.

Use separate data paths only when the directories do not share one root:

```powershell
& <python> -B "<skill_root>\scripts\run_weekly.py" `
  --metadata-root "<task_dir>" `
  --deliverables-root "<deliverables_dir>" `
  --output-root "<logs_dir>" `
  --person "<person>" --task "<numeric_id>"
```

Use `--config <json>` only to override runtime settings such as month, calendar
range, timeouts, intervals, prompt suffix, or remote output roots. The launcher
always replaces `scripts_root` with its bundled runtime and CLI data paths take
precedence over config paths.

Use `--dry-run` for a no-HDC lifecycle preview. Do not pass `--skip-clear`,
`--skip-push`, `--skip-fetch`, or `--skip-initial-clear` during a normal run.
Do not use `--rerun` unless the user explicitly requests replacement. Treat the
runner as a long-running quiet process; wait on the same process and never relaunch
it merely because output is quiet.

## Return the Runner handoff

This Skill is Runner-only. If asked for Judge, scoring, HALO, or diagnosis, return
`unsupported-stage` before HDC.

After a normal batch, require:

```text
<output_root>/weekly_runner_batch.json
```

Store each Task's Runner evidence in one prefixed directory:

```text
<output_root>/task<numeric_id>/
├── task<numeric_id>.jsonl
├── task<numeric_id>.meta.json
├── task<numeric_id>.prompt.txt
├── task<numeric_id>.content.txt
├── metadata.json
├── artifacts_manifest.json
├── completed.json | failed.json | interrupted.json
└── outputs/
```

Do not create `.run`, `_runs`, or `run_<date>` directories. Store batch-level
evidence directly under `<output_root>` as `weekly_runner_batch.json`,
`hdc_commands_<YYYYMMDD>.log`, `<person>.<YYYYMMDD>.lifecycle.log`, and
`<person>.<YYYYMMDD>.person_result.json`.

Verify that `adapter` is `weekly-report`, `runnerFinished` is true, and every
selected Task appears exactly once. Return the handoff path and one row per Task
with person, Task ID, Runner outcome, Trace path, and outputs directory. Use the
handoff outcome and Task marker rather than process exit alone to determine success.
