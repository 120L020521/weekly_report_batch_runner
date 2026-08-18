---
name: halo-rlm-agent-driven
description: >-
  Diagnose OTel/OpenTelemetry JSONL traces locally with one deterministic
  Python workflow and the host agent as the final reasoner. The script converts
  traces, extracts error spans, repeated calls, terminal state and verbatim raw
  evidence, validates reports, reuses reports by Trace/Judge/metadata
  fingerprint, and renders batches. No external LLM API or extra API key.
  Supports Workspace-Bench, HarmonyOS XiaoYi FileOrganization_* and weekly
  report Runner-Judge-HALO flows.
---

# halo-rlm-agent-driven

Use `scripts/halo_workflow.py` as the only normal workflow entry. Python owns
all mechanical work. The host Agent reads one generated input packet and owns
only final attribution, recovery judgment, prioritization, and improvement
recommendations. Never request an API key or invoke an external HALO/LLM
engine.

## Requirements and boundaries

- Use Python >= 3.10; no third-party package is required.
- Read and write JSONL, JSON, prompt, manifest, and report files as UTF-8.
- Treat Trace, metadata, Judge inputs, and unrelated files as read-only.
- For a single Task, write generated artifacts only below the supplied
  `OUTPUT_ROOT`. For a Judge batch, write batch indexes and merged HTML below the
  supplied `OUTPUT_ROOT`, and honor an explicitly validated `haloDir =
  <taskRoot>/xiaoyi_halo` in each queue row for per-Task artifacts.
<<<<<<< HEAD
- In batch mode, use the Judge queue's exact, path-safe `taskId` for both the
  `<taskId>_halo` artifact directory and `<taskId>.halo.jsonl`; do not derive
  either name from the source Trace filename. For weekly reports this `taskId`
  is `metadata.absolute_id` and may be non-numeric.
=======
>>>>>>> 874a79aed3a126240813fa65fc8adbbee74439cb
- Pass the exact Trace declared by the caller or Judge queue. Never replace it
  with an ancestor directory and never scan for alternate inputs.
- Task and Judge data are evaluator context, not Trace evidence and never
  runner-visible input.
- Do not use `--force` merely to rerun a diagnosis. It intentionally disables
  report reuse and refreshes trace preparation.

## Single task

Run one command:

```powershell
& <python> -B "<skill_root>\scripts\halo_workflow.py" prepare `
  --trace "<trace.jsonl>" `
  --output-root "<output-root>" `
  [--metadata "<metadata.json>"] `
  [--judge "<judge_result.json>"] `
  [--adapter file-organization|weekly-report|workspacebench] `
  [--task-id "<task-id>"]
```

The JSON result has one of two successful states:

- `resumed`: `agent_required=false`; return the existing validated
  `report_path`. Do not invoke the Agent again.
- `ready_for_agent`: give the Agent only the returned `agent_input` path.

`prepare` performs all deterministic work in one process:

- detects and converts raw `event + payload` JSONL or copies HALO span JSONL;
- preserves multiple main/child traces and trace-local span indexes;
- builds the authoritative v9 diagnosis contract and editable-target allowlist;
- extracts per-trace root/terminal status;
- extracts OTel errors, `tool.is_error`, and structured semantic failures;
- normalizes tool arguments, groups exact repeated calls, records occurrence
  counts and mechanically recognizable fail-then-success retries;
- emits a compact complete tool timeline;
- maps every root, error candidate, and repeated call back to a contiguous,
  verbatim pre-conversion source excerpt;
- writes `halo_agent_input.json`, `halo_prompt.txt`, the prepared Trace,
  manifest, index cache when needed, and workflow state.

The fingerprint includes the source Trace, Judge JSON, metadata JSON, adapter,
editable surfaces, report contract, and deterministic implementation. A report
is reused only when the fingerprint matches and complete bundle validation
still succeeds. File existence alone is never sufficient.

## Agent-only phase

Open only `halo_agent_input.json`. Do not call `tool_cli`, `prepare_trace.py`,
`agent_cli source-evidence`, or search the raw Trace during the normal path.
The packet already contains:

- `task_context` and `judge_context`;
- `mechanical_evidence.overview` and per-trace summaries;
- `terminal_status` with a non-authoritative mechanical classification hint;
- `error_span_candidates`;
- `repeated_calls`, tool counts, and the complete compact tool timeline;
- `raw_evidence_by_span` with exact `trace_id`, `span_id`, zero-based
  `span_index`, source line numbers, and verbatim `raw_log_excerpt`;
- adapter-specific guidance, the authoritative diagnosis contract, allowed
  targets, exact report path, and exact finalize command.

The Agent must only:

1. decide which mechanical candidates are material;
2. attribute root cause without using Judge outcome as execution proof;
3. decide whether a later compatible operation proves recovery;
4. assign the final execution classification and P0-P4 priorities;
5. propose surgical changes on allowed targets;
6. write exactly one v9 JSON object to `agent_job.write`.

Copy TRACE `span_index` and `raw_log_excerpt` unchanged from
`raw_evidence_by_span`. Never invent or splice evidence. Non-TRACE evidence may
use TASK, JUDGE, SOURCE_FILE, or OUTPUT_FILE as allowed by the embedded
contract.

Then run the packet's `agent_job.then_run`, equivalently:

```powershell
& <python> -B "<skill_root>\scripts\halo_workflow.py" finalize `
  --agent-input "<artifact-dir>\halo_agent_input.json"
```

Fix only `halo_report.json` and rerun `finalize` until it returns
`status=complete`. Finalization enforces schema, adapter targets, manifest
bindings, freshness, real trace/span references, exact span indexes, verbatim
source excerpts, outcome-bearing TRACE evidence, and report/state fingerprint
binding.

## Batch from Judge

Consume the exact unified `judge_queue.json`; do not create `handoff.json` and
do not scan task directories:

```powershell
& <python> -B "<skill_root>\scripts\halo_workflow.py" prepare-batch `
  --queue "<judge-root>\judge_queue.json" `
  --output-root "<batch-dir>" `
  --mode all
```

Use `--mode failed` only when the user explicitly requests failed-only
diagnosis. It selects Runner failures, missing/errored Judge results, and
`passed=false`; `all` selects every Trace-bearing task. A missing Trace skips
only that task.

The result supplies `agent_inputs`. Process only entries whose task row has
`agent_required=true`; a reused task needs no Agent work. For each ready input,
perform the Agent-only phase above. Parallel Agent execution is optional and
must follow the caller's concurrency policy; the deterministic Python work is
already complete.

After all ready reports are written, run:

```powershell
& <python> -B "<skill_root>\scripts\halo_workflow.py" finalize-batch `
  --agent-queue "<output-root>\halo_agent_queue.json"
```

This validates every selected report and, only when validation succeeds,
generates `batch_diagnosis_report.html` with the fixed template and archive
policy. Isolated preparation errors remain recorded in the queue instead of
changing other task inputs.

## Classification and report rules

Choose exactly one execution classification:

- `FAILED`: a root error or explicit terminal failure.
- `SUCCEEDED_WITH_RECOVERED_ERRORS`: root success and every material error has
  a later success/verification for the same operation with compatible args.
- `SUCCEEDED_WITH_UNPROVEN_RECOVERY`: root success but a material recovery is
  bypassed, tolerated, or unproven.
- `SUCCEEDED_CLEANLY`: root success without material failures.
- `UNKNOWN`: terminal evidence is missing, ambiguous, or conflicting.

Root success proves execution completion, not external correctness. An
unrelated OK Span never proves recovery. Exact report fields, evidence limits,
Chinese narrative requirements, P0-P4 policy, allowed components, and
classification-dependent proposed-change counts are authoritative in the
packet's `diagnosis_contract`; do not duplicate or weaken them here.

Default editable targets are selected mechanically:

- `file-organization`: `xiaoyi-auto-continue/SKILL.md`, `run_test.py`,
  `task_executor.py`, `setup_device.py`;
- `weekly-report`: `xiaoyi-weekly-report/SKILL.md`, `weekly_runner.py`,
  `task_executor.py`, `batch_execute_tools.py`;
- other/unknown: `runner_skill.md`, `workspace_bench_tools.ts`.

Legacy helper CLIs remain internal for tests and exceptional debugging. They
are not part of the normal Agent workflow.
