#!/usr/bin/env python3
"""One mechanical entry point around host-agent HALO diagnosis.

``prepare`` converts/indexes a trace, builds the authoritative prompt, extracts
mechanical findings and verbatim source evidence, and writes one bounded agent
input packet. ``finalize`` validates the agent-authored report and records a
reusable fingerprint. Batch variants consume the Judge queue without scanning.
No command in this module calls an LLM API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from halo_rlm.agent_cli import (
    ADAPTER_EDITABLE_SURFACES,
    _validate_report,
)
from halo_rlm.better_harness import DEFAULT_EDITABLE_SURFACES, build_halo_prompt
from halo_rlm.report_contract import RAW_LOG_EXCERPT_MAX_CHARS
from halo_rlm.source_evidence import build_source_evidence, choose_source_excerpt
from prepare_trace import _artifact_paths, detect_format, prepare_trace


WORKFLOW_SCHEMA_VERSION = 1
REPORT_CONTRACT_VERSION = 9
AGENT_INPUT_NAME = "halo_agent_input.json"
STATE_NAME = "halo-workflow-state.json"
SEMANTIC_KEYS = {"error", "errors", "exception", "is_error", "ok", "passed", "status", "success", "timeout", "timed_out"}
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_object(path: Path | None, label: str) -> dict[str, Any]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object: {path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _artifact_id(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("taskId must be a string or integer")
    normalized = str(value).strip()
    invalid_chars = '<>:"/\\|?*'
    if (
        not normalized
        or normalized in {".", ".."}
        or ".." in normalized
        or normalized.endswith((" ", "."))
        or any(char in normalized for char in invalid_chars)
        or any(ord(char) < 32 for char in normalized)
    ):
        raise ValueError(f"unsafe taskId: {value!r}")
    return normalized


def _sha256(path: Path | None) -> str:
    if path is None:
        return "MISSING"
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _text(value: Any, limit: int = 2000) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, default=str)
    if len(value) <= limit:
        return value
    return value[:limit] + f"... [HALO truncated: original {len(value)} chars]"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "error", "failed"}


def _status(span: dict[str, Any]) -> tuple[str, str]:
    raw = span.get("status")
    if isinstance(raw, dict):
        return str(raw.get("code") or "STATUS_CODE_UNSET"), str(raw.get("message") or "")
    return str(span.get("status_code") or raw or "STATUS_CODE_UNSET"), str(span.get("status_message") or "")


def _is_error_status(code: str) -> bool:
    return code.upper() in {"ERROR", "STATUS_CODE_ERROR", "2"}


def _structured_semantic_failures(value: Any, prefix: str = "", depth: int = 0) -> list[str]:
    if depth > 4:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
        return _structured_semantic_failures(parsed, prefix, depth + 1)
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            normalized = str(key).lower()
            if normalized in SEMANTIC_KEYS:
                failed = (
                    (normalized in {"ok", "passed", "success"} and child is False)
                    or (normalized in {"is_error", "timeout", "timed_out"} and _truthy(child))
                    or (normalized in {"error", "errors", "exception"} and child not in (None, "", False, [], {}))
                    or (normalized == "status" and str(child).lower() in {"error", "failed", "failure", "timeout"})
                )
                if failed:
                    findings.append(f"{path}={_text(child, 500)}")
            findings.extend(_structured_semantic_failures(child, path, depth + 1))
    elif isinstance(value, list):
        for index, child in enumerate(value[:50]):
            findings.extend(_structured_semantic_failures(child, f"{prefix}[{index}]", depth + 1))
    return findings[:20]


def _normalize_input(value: Any) -> tuple[str, str]:
    if value is None:
        canonical = ""
    elif isinstance(value, str):
        try:
            canonical = json.dumps(json.loads(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (json.JSONDecodeError, TypeError):
            canonical = " ".join(value.split())
    else:
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), _text(canonical, 1000)


def _load_spans(trace_path: Path) -> tuple[list[dict[str, Any]], int]:
    spans: list[dict[str, Any]] = []
    skipped = 0
    per_trace_index: Counter[str] = Counter()
    with trace_path.open("r", encoding="utf-8-sig") as stream:
        for line_number, raw in enumerate(stream, 1):
            if not raw.strip():
                continue
            try:
                span = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{trace_path}:{line_number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(span, dict) or not span.get("trace_id") or not span.get("span_id"):
                skipped += 1
                continue
            trace_id = str(span["trace_id"])
            span["__halo_span_index"] = per_trace_index[trace_id]
            per_trace_index[trace_id] += 1
            spans.append(span)
    if not spans:
        raise ValueError(f"prepared trace contains no spans: {trace_path}")
    return spans, skipped


def _span_record(span: dict[str, Any]) -> dict[str, Any]:
    attrs = span.get("attributes") if isinstance(span.get("attributes"), dict) else {}
    code, message = _status(span)
    tool_name = str(attrs.get("tool.name") or "")
    input_hash, input_preview = _normalize_input(attrs.get("input.value"))
    semantic = _structured_semantic_failures(attrs.get("output.value"))
    return {
        "trace_id": str(span["trace_id"]),
        "span_id": str(span["span_id"]),
        "span_index": int(span["__halo_span_index"]),
        "parent_span_id": str(span.get("parent_span_id") or ""),
        "name": str(span.get("name") or ""),
        "start_time": span.get("start_time"),
        "end_time": span.get("end_time"),
        "status_code": code,
        "status_message": _text(message, 1000),
        "tool": tool_name,
        "tool_call_id": str(attrs.get("tool.call_id") or ""),
        "tool_is_error": _truthy(attrs.get("tool.is_error")),
        "input_fingerprint": input_hash,
        "input_preview": input_preview,
        "output_preview": _text(attrs.get("output.value"), 2000),
        "semantic_failure_markers": semantic,
    }


def _mechanical_evidence(source_path: Path, trace_path: Path) -> dict[str, Any]:
    spans, skipped = _load_spans(trace_path)
    records = [_span_record(span) for span in spans]
    evidence_map = build_source_evidence(source_path, trace_path)
    by_trace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_trace[record["trace_id"]].append(record)

    roots: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    repeated_source_keys: set[tuple[str, str]] = set()
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if not record["parent_span_id"]:
            roots.append(record)
        if record["tool"]:
            tools.append(record)
            grouped[(record["tool"], record["input_fingerprint"])].append(record)
        if _is_error_status(record["status_code"]) or record["tool_is_error"] or record["semantic_failure_markers"]:
            candidates.append(record)

    repeated: list[dict[str, Any]] = []
    for (tool, fingerprint), calls in grouped.items():
        if len(calls) < 2:
            continue
        for call in calls:
            repeated_source_keys.add((call["trace_id"], call["span_id"]))
        status_sequence = [call["status_code"] for call in calls]
        repeated.append({
            "tool": tool,
            "input_fingerprint": fingerprint,
            "input_preview": calls[0]["input_preview"],
            "occurrence_count": len(calls),
            "status_sequence": status_sequence,
            "possible_retry_recovery": any(_is_error_status(code) for code in status_sequence[:-1]) and not _is_error_status(status_sequence[-1]),
            "calls": [{
                "trace_id": call["trace_id"],
                "span_id": call["span_id"],
                "span_index": call["span_index"],
                "start_time": call["start_time"],
                "status_code": call["status_code"],
                "tool_is_error": call["tool_is_error"],
                "output_preview": call["output_preview"],
            } for call in calls],
        })
    repeated.sort(key=lambda item: (-item["occurrence_count"], item["tool"], item["input_fingerprint"]))

    raw_keys = {
        (record["trace_id"], record["span_id"])
        for record in roots + candidates
    } | repeated_source_keys
    raw_evidence: list[dict[str, Any]] = []
    for key in sorted(raw_keys, key=lambda item: (item[0], evidence_map[item].span_index if item in evidence_map else 10**9)):
        mapped = evidence_map.get(key)
        if mapped is None:
            continue
        excerpt = choose_source_excerpt(mapped, max_chars=RAW_LOG_EXCERPT_MAX_CHARS)
        raw_evidence.append({
            "trace_id": mapped.trace_id,
            "span_id": mapped.span_id,
            "span_index": mapped.span_index,
            "source_line_numbers": list(mapped.source_line_numbers),
            "raw_log_excerpt": excerpt,
        })

    root_codes = [root["status_code"] for root in roots]
    if not roots or any(code in {"", "STATUS_CODE_UNSET", "UNSET", "0"} for code in root_codes):
        hint = "UNKNOWN"
    elif any(_is_error_status(code) for code in root_codes):
        hint = "FAILED"
    elif candidates:
        hint = "SUCCEEDED_WITH_UNPROVEN_RECOVERY"
    else:
        hint = "SUCCEEDED_CLEANLY"

    trace_summaries = []
    for trace_id, trace_records in by_trace.items():
        trace_roots = [item for item in trace_records if not item["parent_span_id"]]
        trace_summaries.append({
            "trace_id": trace_id,
            "span_count": len(trace_records),
            "tool_call_count": sum(bool(item["tool"]) for item in trace_records),
            "error_candidate_count": sum(item in candidates for item in trace_records),
            "start_time": min((item["start_time"] for item in trace_records if item["start_time"]), default=None),
            "end_time": max((item["end_time"] for item in trace_records if item["end_time"]), default=None),
            "root_spans": trace_roots,
        })

    return {
        "overview": {
            "trace_count": len(by_trace),
            "span_count": len(records),
            "tool_call_count": len(tools),
            "error_candidate_count": len(candidates),
            "repeated_signature_count": len(repeated),
            "skipped_jsonl_lines": skipped,
        },
        "trace_summaries": trace_summaries,
        "terminal_status": {
            "root_spans": roots,
            "mechanical_classification_hint": hint,
            "warning": "This is a mechanical hint only; the Agent owns final classification and recovery reasoning.",
        },
        "error_span_candidates": candidates,
        "repeated_calls": repeated,
        "tool_call_counts": dict(sorted(Counter(item["tool"] for item in tools).items())),
        "tool_timeline": tools,
        "raw_evidence_by_span": raw_evidence,
    }


def _surfaces(adapter: str) -> list[str]:
    return list(ADAPTER_EDITABLE_SURFACES.get(adapter.strip().lower(), DEFAULT_EDITABLE_SURFACES))


def _adapter_guidance(adapter: str) -> str:
    if adapter.strip().lower() != "file-organization":
        return ""
    reference = Path(__file__).resolve().parents[1] / "references" / "file-organization-diagnosis.md"
    return reference.read_text(encoding="utf-8") if reference.is_file() else ""


def _implementation_fingerprint() -> str:
    script_root = Path(__file__).resolve().parent
    files = [
        Path(__file__).resolve(),
        script_root / "prepare_trace.py",
        script_root / "halo_rlm" / "agent_cli.py",
        script_root / "halo_rlm" / "better_harness.py",
        script_root / "halo_rlm" / "report_contract.py",
        script_root / "halo_rlm" / "source_evidence.py",
    ]
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _fingerprint(source: Path, prepared: Path, metadata: Path | None, judge: Path | None, adapter: str, surfaces: list[str]) -> tuple[str, dict[str, str]]:
    parts = {
        "trace_sha256": _sha256(source),
        "prepared_trace_sha256": _sha256(prepared),
        "metadata_sha256": _sha256(metadata),
        "judge_sha256": _sha256(judge),
        "adapter": adapter.strip().lower(),
        "editable_surfaces": json.dumps(surfaces, ensure_ascii=False, separators=(",", ":")),
        "workflow_schema_version": str(WORKFLOW_SCHEMA_VERSION),
        "report_contract_version": str(REPORT_CONTRACT_VERSION),
        "mechanical_implementation_sha256": _implementation_fingerprint(),
    }
    digest = hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return digest, parts


def _validate_existing(report: Path, manifest: Path, adapter: str) -> tuple[bool, str]:
    try:
        _validate_report(SimpleNamespace(
            report=str(report), manifest=str(manifest), adapter=adapter, surface=None
        ))
        return True, ""
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, str(exc)


def _prepare_artifacts(
    source: Path,
    output_root: Path,
    force: bool,
    artifact_id: str | None = None,
) -> dict[str, Path]:
    detected = detect_format(source)
    selected, prompt, report, manifest = _artifact_paths(
        source,
        detected,
        source.parent,
        output_root,
        logical_name_override=artifact_id,
    )
    previous_mtime = selected.stat().st_mtime_ns if selected.is_file() else None
    _, prepared = prepare_trace(source, selected, force)
    entry = {
        "source": str(source),
        "selected": str(prepared),
        "prompt_path": str(prompt),
        "report_path": str(report),
        "manifest_path": str(manifest),
        "action": "reused" if previous_mtime is not None and prepared.stat().st_mtime_ns == previous_mtime else "prepared",
    }
    _write_json(manifest, {
        "schema_version": 3,
        "input_directory": str(source.parent),
        "output_directory": str(output_root),
        "snapshot_jsonl_count": 1,
        "prepared_traces": [entry],
        "errors": [],
    })
    return {
        "artifact_dir": selected.parent,
        "source": source,
        "trace": prepared,
        "prompt": prompt,
        "report": report,
        "manifest": manifest,
        "agent_input": selected.parent / AGENT_INPUT_NAME,
        "state": selected.parent / STATE_NAME,
    }


def prepare_one(args: argparse.Namespace) -> dict[str, Any]:
    source = args.trace.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    metadata_path = args.metadata.expanduser().resolve() if args.metadata else None
    judge_path = args.judge.expanduser().resolve() if args.judge else None
    for path, label in ((source, "Trace"), (metadata_path, "metadata"), (judge_path, "Judge")):
        if path is not None and not path.is_file():
            raise ValueError(f"{label} file does not exist: {path}")
    adapter = args.adapter.strip().lower()
    surfaces = _surfaces(adapter)
    task = _read_object(metadata_path, "metadata")
    artifact_id = _artifact_id(args.task_id) if args.task_id else None
    if args.task_id:
        task["task_id"] = artifact_id
    elif task and not any(key in task for key in ("task_id", "id", "taskId")):
        task["task_id"] = metadata_path.parent.name if metadata_path else source.stem
    judge = _read_object(judge_path, "Judge")
    additional = (
        "All deterministic trace inspection has already been performed in halo_agent_input.json. "
        "Use mechanical_evidence and raw_evidence_by_span as the authoritative evidence package. "
        "Do not re-scan the Trace unless the packet explicitly reports missing evidence. Your only "
        "reasoning work is final attribution, recovery assessment, prioritization, and concrete changes."
    )
    prompt_text = build_halo_prompt(
        task,
        judge,
        surfaces,
        additional_request=additional,
        evidence_packet=True,
    )
    artifacts = _prepare_artifacts(source, output_root, args.force, artifact_id)
    fingerprint, fingerprint_parts = _fingerprint(
        source,
        artifacts["trace"],
        metadata_path,
        judge_path,
        adapter,
        surfaces,
    )
    state = _read_object(artifacts["state"], "workflow state") if artifacts["state"].is_file() else {}
    if not args.force and state.get("input_fingerprint") == fingerprint and artifacts["report"].is_file():
        valid, error = _validate_existing(artifacts["report"], artifacts["manifest"], adapter)
        if valid:
            state.update({"status": "complete", "last_reused_at": _now()})
            _write_json(artifacts["state"], state)
            return {
                "status": "resumed",
                "agent_required": False,
                "input_fingerprint": fingerprint,
                "report_path": str(artifacts["report"]),
                "agent_input": str(artifacts["agent_input"]),
                "message": "Trace/Judge/metadata fingerprint matched and the existing report passed complete validation.",
            }
        state["reuse_validation_error"] = error

    # Safely adopt a valid pre-workflow report only when its authoritative
    # prompt proves that the same metadata/Judge/adapter context was used.
    if (
        not args.force
        and not state.get("input_fingerprint")
        and artifacts["report"].is_file()
        and artifacts["prompt"].is_file()
    ):
        existing_prompt = artifacts["prompt"].read_text(encoding="utf-8").rstrip()
        legacy_prompt = build_halo_prompt(task, judge, surfaces).rstrip()
        if existing_prompt in {prompt_text.rstrip(), legacy_prompt}:
            valid, _error = _validate_existing(
                artifacts["report"], artifacts["manifest"], adapter
            )
            if valid:
                adopted = {
                    "schema_version": WORKFLOW_SCHEMA_VERSION,
                    "status": "complete",
                    "created_at": _now(),
                    "last_reused_at": _now(),
                    "input_fingerprint": fingerprint,
                    "fingerprint_parts": fingerprint_parts,
                    "adapter": adapter,
                    "agent_input": str(artifacts["agent_input"]),
                    "manifest_path": str(artifacts["manifest"]),
                    "report_path": str(artifacts["report"]),
                    "report_sha256": _sha256(artifacts["report"]),
                    "adopted_legacy_report": True,
                }
                _write_json(artifacts["state"], adopted)
                return {
                    "status": "resumed",
                    "agent_required": False,
                    "input_fingerprint": fingerprint,
                    "report_path": str(artifacts["report"]),
                    "agent_input": str(artifacts["agent_input"]) if artifacts["agent_input"].is_file() else "",
                    "message": "An existing report with an identical authoritative prompt passed complete validation and was fingerprinted for reuse.",
                }

    artifacts["prompt"].write_text(prompt_text.rstrip() + "\n", encoding="utf-8")
    mechanical = _mechanical_evidence(source, artifacts["trace"])
    packet = {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "status": "ready_for_agent",
        "created_at": _now(),
        "input_fingerprint": fingerprint,
        "fingerprint_parts": fingerprint_parts,
        "adapter": adapter,
        "editable_surfaces": surfaces,
        "paths": {key: str(value) for key, value in artifacts.items() if key not in {"artifact_dir", "state"}},
        "task_context": task,
        "judge_context": judge,
        "mechanical_evidence": mechanical,
        "adapter_guidance": _adapter_guidance(adapter),
        "diagnosis_contract": prompt_text,
        "agent_job": {
            "read": "This file only.",
            "reason_about": [
                "final root-cause attribution",
                "same-operation recovery and final execution classification",
                "priority ranking",
                "surgical improvement recommendations",
            ],
            "write": str(artifacts["report"]),
            "then_run": f'{sys.executable} -B "{Path(__file__).resolve()}" finalize --agent-input "{artifacts["agent_input"]}"',
            "prohibited": [
                "inventing evidence",
                "changing raw_log_excerpt or span_index",
                "editing files outside the report path",
            ],
        },
    }
    _write_json(artifacts["agent_input"], packet)
    _write_json(artifacts["state"], {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "status": "ready_for_agent",
        "created_at": packet["created_at"],
        "input_fingerprint": fingerprint,
        "fingerprint_parts": fingerprint_parts,
        "adapter": adapter,
        "agent_input": str(artifacts["agent_input"]),
        "manifest_path": str(artifacts["manifest"]),
        "report_path": str(artifacts["report"]),
    })
    return {
        "status": "ready_for_agent",
        "agent_required": True,
        "input_fingerprint": fingerprint,
        "agent_input": str(artifacts["agent_input"]),
        "report_path": str(artifacts["report"]),
        "finalize_command": packet["agent_job"]["then_run"],
        "mechanical_overview": mechanical["overview"],
    }


def finalize_one(args: argparse.Namespace) -> dict[str, Any]:
    agent_input = args.agent_input.expanduser().resolve()
    packet = _read_object(agent_input, "agent input")
    if packet.get("schema_version") != WORKFLOW_SCHEMA_VERSION:
        raise ValueError(f"unsupported agent input schema: {agent_input}")
    paths = packet.get("paths")
    if not isinstance(paths, dict):
        raise ValueError(f"agent input paths are missing: {agent_input}")
    report = Path(str(paths.get("report") or "")).resolve()
    manifest = Path(str(paths.get("manifest") or "")).resolve()
    adapter = str(packet.get("adapter") or "workspacebench")
    result = _validate_report(SimpleNamespace(
        report=str(report), manifest=str(manifest), adapter=adapter, surface=None
    ))
    state_path = agent_input.parent / STATE_NAME
    state = _read_object(state_path, "workflow state") if state_path.is_file() else {}
    if state.get("input_fingerprint") != packet.get("input_fingerprint"):
        raise ValueError("workflow state and agent input fingerprints do not match")
    state.update({
        "status": "complete",
        "completed_at": _now(),
        "report_sha256": _sha256(report),
    })
    _write_json(state_path, state)
    return {
        **result,
        "status": "complete",
        "input_fingerprint": packet["input_fingerprint"],
        "state_path": str(state_path),
    }


def _selected(item: dict[str, Any], mode: str, judge: dict[str, Any]) -> bool:
    return (
        mode == "all"
        or item.get("runnerStatus") != "completed"
        or judge.get("status") != "success"
        or judge.get("passed") is False
    )


def _batch_task_output_root(item: dict[str, Any], fallback: Path, task_id: str) -> Path:
    raw = item.get("haloDir")
    if not isinstance(raw, str) or not raw.strip():
        return fallback
    task_root_raw = item.get("taskRoot")
    if not isinstance(task_root_raw, str) or not task_root_raw.strip():
        raise ValueError(f"task {task_id} haloDir requires taskRoot")
    task_root = Path(task_root_raw).expanduser().resolve()
    halo_dir = Path(raw).expanduser().resolve()
    if halo_dir != (task_root / "xiaoyi_halo").resolve():
        raise ValueError(f"task {task_id} haloDir must equal <taskRoot>/xiaoyi_halo")
    return halo_dir


def prepare_batch(args: argparse.Namespace) -> dict[str, Any]:
    queue_path = args.queue.expanduser().resolve()
    queue = _read_object(queue_path, "Judge queue")
    if queue.get("version") != 1 or queue.get("producer") != "judge-xiaoyi-results":
        raise ValueError("unsupported Judge queue schema")
    items = queue.get("tasks")
    if not isinstance(items, list):
        raise ValueError("Judge queue tasks must be an array")
    output_root = args.output_root.expanduser().resolve()
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Judge queue tasks must contain objects")
        task_id = str(item.get("taskId") or "")
        result_path = Path(item["result"]).resolve() if isinstance(item.get("result"), str) else None
        judge = _read_object(result_path, "Judge") if result_path and result_path.is_file() else {}
        if not _selected(item, args.mode, judge):
            rows.append({"task_id": task_id, "status": "skipped_by_mode", "agent_required": False})
            continue
        trace = Path(item["trace"]).resolve() if isinstance(item.get("trace"), str) else None
        if trace is None or not trace.is_file():
            rows.append({"task_id": task_id, "status": "skipped_missing_trace", "agent_required": False})
            continue
        prepared_dir = Path(item["preparedDir"]).resolve() if isinstance(item.get("preparedDir"), str) else None
        metadata = prepared_dir / "metadata.json" if prepared_dir and (prepared_dir / "metadata.json").is_file() else None
        if metadata is None and isinstance(item.get("metadata"), str):
            metadata = Path(item["metadata"]).resolve()
        try:
            task_output_root = _batch_task_output_root(item, output_root, task_id)
            row = prepare_one(SimpleNamespace(
                trace=trace,
                output_root=task_output_root,
                metadata=metadata,
                judge=result_path if result_path and result_path.is_file() else None,
                adapter=str(item.get("adapter") or "workspacebench"),
                task_id=task_id or None,
                force=args.force,
            ))
            row["task_id"] = task_id
            rows.append(row)
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            rows.append({"task_id": task_id, "status": "error", "agent_required": False, "error": str(exc)})
    batch_input = output_root / "halo_agent_queue.json"
    payload = {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "created_at": _now(),
        "judge_queue": str(queue_path),
        "output_root": str(output_root),
        "mode": args.mode,
        "tasks": rows,
    }
    _write_json(batch_input, payload)
    return {
        "status": "error" if any(row["status"] == "error" for row in rows) else "ready",
        "agent_queue": str(batch_input),
        "agent_inputs": [row["agent_input"] for row in rows if row.get("agent_required")],
        "ready_count": sum(bool(row.get("agent_required")) for row in rows),
        "reused_count": sum(row["status"] == "resumed" for row in rows),
        "tasks": rows,
    }


def finalize_batch(args: argparse.Namespace) -> dict[str, Any]:
    queue_path = args.agent_queue.expanduser().resolve()
    queue = _read_object(queue_path, "HALO agent queue")
    tasks = queue.get("tasks")
    if queue.get("schema_version") != WORKFLOW_SCHEMA_VERSION or not isinstance(tasks, list):
        raise ValueError("unsupported HALO agent queue schema")
    results = []
    errors = []
    for item in tasks:
        if item.get("status") not in {"ready_for_agent", "resumed"}:
            continue
        try:
            results.append(finalize_one(SimpleNamespace(agent_input=Path(item["agent_input"]))))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append({"task_id": item.get("task_id"), "error": str(exc)})
    render_result: dict[str, Any] | None = None
    if not errors:
        renderer = Path(__file__).with_name("render_batch_report.py")
        command = [
            sys.executable, "-B", str(renderer),
            "--queue", str(queue["judge_queue"]),
            "--output-root", str(queue["output_root"]),
            "--mode", str(queue.get("mode") or "all"),
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            errors.append({"task_id": "BATCH_RENDER", "error": (completed.stderr or completed.stdout).strip()})
        else:
            render_result = json.loads(completed.stdout)
    return {
        "status": "error" if errors else "complete",
        "validated_count": len(results),
        "errors": errors,
        "render": render_result,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare", help="Create or reuse one complete mechanical evidence packet")
    prepare.add_argument("--trace", type=Path, required=True)
    prepare.add_argument("--output-root", type=Path, required=True)
    prepare.add_argument("--metadata", type=Path)
    prepare.add_argument("--judge", type=Path)
    prepare.add_argument("--adapter", default="workspacebench")
    prepare.add_argument("--task-id")
    prepare.add_argument("--force", action="store_true")
    prepare.set_defaults(handler=prepare_one)

    finalize = commands.add_parser("finalize", help="Validate one agent report and make it reusable")
    finalize.add_argument("--agent-input", type=Path, required=True)
    finalize.set_defaults(handler=finalize_one)

    batch = commands.add_parser("prepare-batch", help="Prepare selected rows from one Judge queue")
    batch.add_argument("--queue", type=Path, required=True)
    batch.add_argument("--output-root", type=Path, required=True)
    batch.add_argument("--mode", choices=("all", "failed"), default="all")
    batch.add_argument("--force", action="store_true")
    batch.set_defaults(handler=prepare_batch)

    finish_batch = commands.add_parser("finalize-batch", help="Validate prepared batch reports and render HTML")
    finish_batch.add_argument("--agent-queue", type=Path, required=True)
    finish_batch.set_defaults(handler=finalize_batch)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = args.handler(args)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result.get("status") == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
