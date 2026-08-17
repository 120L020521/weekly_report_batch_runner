---
name: judge-xiaoyi-results
description: >-
  Judge frozen XiaoYi artifacts through one unified batch input, common Prepare
  layout, common result schema, and adapter-specific evaluators. Use after a
  complete FileOrganization, WorkspaceBench, or weekly-report Runner batch; for
  Judge-only or re-Judge requests backed by an explicit judge_batch.json; and as
  the Judge stage inside run-xiaoyi. Never runs XiaoYi or mutates device state.
---

# Judge XiaoYi Results

Evaluate frozen local evidence only. Never launch XiaoYi, use HDC, continue a
dialog, clean the device, call an external Judge API, or infer missing paths by
scanning directories.

## Unified batch input

Accept exactly one Agent-written `judge_batch.json` after the whole Runner batch
is terminal:

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

Require globally unique ordered string IDs, one of `file-organization`,
`workspacebench`, or `weekly-report`, normalized Runner status, explicit absolute
paths, and a unique `judge_dir` below `judge_root`. Optional `data`, `runner_dir`,
and `trace` may be `null`; every path key must still exist. Only
`runner_status = completed` is Judgeable.

## Common Prepare

Run once for the whole batch:

```powershell
& <python> -B "<skill_root>\scripts\judge_batch.py" prepare `
  --batch "<judge_batch.json>"
```

Do not pass `--force` unless the user explicitly requests re-Judge. Read the
returned `judge_queue.json`. Every ready task has the same frozen structure:

```text
<judge_dir>/
├── metadata.json
├── case_manifest.json
├── data/                 # only when supplied
├── output/
└── runner/               # only when runner_dir or trace is supplied
```

Prepare copies only declared sources, omits an embedded runner `output/` or
`outputs/` subtree, fingerprints the frozen evidence, and records exact source
paths in `case_manifest.json`. `runner-failure` and `input-error` queue entries
are terminal and must not be repaired or sent to an evaluator.

Every queue row also preserves the explicit source `metadata` and raw `trace`
paths. HALO consumes this same `judge_queue.json` after Judge; do not generate a
second HALO handoff.

`metadata.json` and `case_manifest.json` are Judge inputs. They are produced by
the common Prepare stage, never by an adapter evaluator. `judge_result.json` is
the evaluator output.

## Dispatch adapter-specific evaluators

Wait for Prepare to finish. Assign every `status = ready` item to exactly one
fresh Judge worker, up to available concurrency. Never give one worker multiple
tasks and never spawn a worker for `resumed`.

### File organization

Use the deterministic evaluator against the prepared snapshot:

```powershell
& <python> -B "<skill_root>\scripts\judge_file_organization.py" `
  --metadata "<judge_dir>\metadata.json" `
  --outputs "<judge_dir>\output" `
  --case-manifest "<judge_dir>\case_manifest.json" `
  --result "<judge_dir>\judge_result.json"
```

Treat metadata rubrics as the complete final-state contract. Require exact
`Desktop`, `Download`, and `Documents` roots; ignore only
`outputs_manifest.json` bookkeeping; reject path traversal; compare direct
children exactly; verify file/directory types and MD5 values; and continue after
an unsupported individual rubric by marking that rubric failed. The evaluator
must copy the Prepare fingerprint and must not create or change metadata or the
case manifest.

### WorkspaceBench

Give the worker only its task ID, prepared directory, result path, and this
Skill. Require it to inspect metadata, manifest, all relevant `data/`, `output/`,
and `runner/` evidence. Use artifact-specific skills for documents,
spreadsheets, PDFs, slides, or images. Evaluate each rubric independently in
metadata order; insufficient evidence fails the rubric. Write only the assigned
result with `judgeType = codex-subagent`.

### Weekly report

Use the same Agent-evaluator contract as WorkspaceBench, with these additional
checks when required by rubrics: output format, exact reporting date range,
person/department/job identity, source-supported facts, required sections,
readability, and worklog presence. Verify time range against both generated
report content and dated source evidence. Material facts outside the requested
period fail the relevant rubric. Write only the assigned result with
`judgeType = codex-subagent`.

## Common result schema and validation

Every successful evaluator writes:

```json
{
  "version": 1,
  "taskId": "21",
  "status": "success",
  "judgeType": "codex-subagent",
  "inputFingerprint": {"algorithm": "sha256", "value": "...", "fileCount": 3},
  "rubrics": [
    {
      "index": 0,
      "rubric": "rubric text",
      "passed": true,
      "confidence": 0.95,
      "evidence": "specific frozen-artifact evidence"
    }
  ],
  "summary": {"total": 1, "passed": 1, "failed": 0},
  "passed": true,
  "score": 1.0,
  "feedback": "1/1 rubrics passed."
}
```

Use `deterministic-file-organization` only for the file adapter and
`codex-subagent` for the other two. Copy `inputFingerprint` exactly from the
manifest. For an unrecoverable evaluator failure, write the same common identity
fields with `status = error` and a non-empty `error`.

After each worker, validate the file rather than trusting its message:

```powershell
& <python> -B "<skill_root>\scripts\judge_batch.py" validate-result `
  --prepared-dir "<judge_dir>"
```

Fix only the assigned result and repeat until valid, or replace it with a valid
error result. Then summarize once:

```powershell
& <python> -B "<skill_root>\scripts\judge_batch.py" summarize `
  --judge-root "<judge_root>"
```

Return `judge_batch.json`, `judge_queue.json`, `batch_summary.json`, and each
prepared/result path to `run-xiaoyi`. Treat `judge_queue.json` as the direct
batch input for HALO. Successful Judge execution and rubric pass/fail are
distinct states.
