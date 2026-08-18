#!/usr/bin/env python3
"""Merge validated per-task HALO reports from one Judge queue into HTML."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PREFIX = "const batch = "
PAYLOAD_VERSION = 2
DEFAULT_ARCHIVE_THRESHOLD = 500
TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "halo_diagnostic_report.template.html"


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_dir(output_root: Path, task_id: str) -> Path:
    leaf = f"task{task_id}_halo" if task_id.isdigit() else f"{task_id}_halo"
    return output_root / leaf


def _task_output_root(item: dict[str, Any], output_root: Path, task_id: str) -> Path:
    raw = item.get("haloDir")
    if not isinstance(raw, str) or not raw.strip():
        return output_root
    task_root_raw = item.get("taskRoot")
    if not isinstance(task_root_raw, str) or not task_root_raw.strip():
        raise ValueError(f"task {task_id} haloDir requires taskRoot")
    task_root = Path(task_root_raw).expanduser().resolve()
    halo_dir = Path(raw).expanduser().resolve()
    if halo_dir != (task_root / "xiaoyi_halo").resolve():
        raise ValueError(f"task {task_id} haloDir must equal <taskRoot>/xiaoyi_halo")
    return halo_dir


def read_batch_payload(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    document = path.read_text(encoding="utf-8")
    start = document.find(PREFIX)
    end = document.find(";\n", start + len(PREFIX))
    if start < 0 or end < 0:
        raise ValueError(f"existing HTML is not a HALO batch report: {path}")
    value = json.loads(document[start + len(PREFIX):end])
    if not isinstance(value, dict) or not isinstance(value.get("tasks"), list):
        raise ValueError(f"existing HALO batch report is invalid: {path}")
    return value


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count("__BATCH_DATA__") != 1:
        raise ValueError("HALO HTML template must contain one __BATCH_DATA__ marker")
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded = encoded.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(template.replace("__BATCH_DATA__", encoded), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _identity(task: dict[str, Any]) -> str:
    return f"{task.get('task_id')}:{task.get('trace_fingerprint', '')}"


def _merge(existing: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = {_identity(task) for task in current}
    ids = {str(task.get("task_id")) for task in current}
    kept = [
        task for task in existing
        if _identity(task) not in keys and str(task.get("task_id")) not in ids
    ]
    return kept + current


def _archive_path(output: Path, now: datetime, index: int) -> Path:
    while True:
        suffix = "" if index == 1 else f"-{index}"
        candidate = output.with_name(
            f"{output.stem}.archive-{now.strftime('%Y%m%dT%H%M%SZ')}{suffix}{output.suffix}"
        )
        if not candidate.exists():
            return candidate
        index += 1


def _task_record(item: dict[str, Any], output_root: Path, mode: str) -> tuple[dict[str, Any], str | None]:
    task_id = str(item.get("taskId", ""))
    if not task_id or "/" in task_id or "\\" in task_id or ".." in task_id:
        raise ValueError(f"unsafe taskId in Judge queue: {task_id!r}")
    trace = Path(item["trace"]).resolve() if isinstance(item.get("trace"), str) else None
    prepared = Path(item["preparedDir"]).resolve() if isinstance(item.get("preparedDir"), str) else None
    metadata_path = prepared / "metadata.json" if prepared else None
    if metadata_path is None or not metadata_path.is_file():
        metadata_path = Path(item["metadata"]).resolve() if isinstance(item.get("metadata"), str) else None
    metadata = _read_json(metadata_path, "Task metadata") if metadata_path and metadata_path.is_file() else {}
    result_path = Path(item["result"]).resolve() if isinstance(item.get("result"), str) else None
    judge = _read_json(result_path, "Judge result") if result_path and result_path.is_file() else {}
    judge_ok = judge.get("status") == "success"
    selected = (
        mode == "all"
        or item.get("runnerStatus") != "completed"
        or not judge_ok
        or judge.get("passed") is False
    )
    artifact = _artifact_dir(_task_output_root(item, output_root, task_id), task_id)
    report_path = artifact / "halo_report.json"
    record: dict[str, Any] = {
        "task_id": task_id,
        "adapter": item.get("adapter"),
        "task": metadata.get("task") or metadata.get("description"),
        "trace_fingerprint": _sha256(trace) if trace and trace.is_file() else "",
        "runner_status": item.get("runnerStatus"),
        "execution_outcome": item.get("executionOutcome"),
        "evidence_ready": item.get("evidenceReady"),
        "judge_status": judge.get("status", "missing"),
        "judge_passed": judge.get("passed"),
        "judge_score": judge.get("score"),
        "halo_status": "skipped_by_mode" if not selected else "skipped_missing_trace",
        "execution_classification": "",
        "primary_failure_mode": "",
        "trace_ids": [],
        "expected_output_files": [],
        "judge_summary": "",
        "error_findings": [],
        "proposed_changes": [],
        "report_path": "",
        "report_uri": "",
        "judge_result_uri": result_path.as_uri() if result_path and result_path.is_file() else "",
        "trace_uri": trace.as_uri() if trace and trace.is_file() else "",
        "halo_message": "",
    }
    if not selected:
        return record, None
    if trace is None or not trace.is_file():
        record["halo_message"] = "Trace missing; diagnosis skipped."
        return record, None
    if not report_path.is_file():
        message = f"task {task_id}: HALO report is missing: {report_path}"
        record.update({"halo_status": "error", "halo_message": message})
        return record, message
    report = _read_json(report_path, "HALO report")
    summary = report.get("report_summary")
    diagnosis = report.get("diagnosis")
    changes = report.get("proposed_changes")
    if (
        report.get("schema_version") != 9
        or not isinstance(summary, dict)
        or str(summary.get("task_id")) != task_id
        or not isinstance(diagnosis, dict)
        or not isinstance(diagnosis.get("error_findings"), list)
        or not isinstance(changes, list)
    ):
        message = f"task {task_id}: HALO report structure or task_id is invalid: {report_path}"
        record.update({"halo_status": "error", "halo_message": message})
        return record, message
    record.update({
        "task": summary.get("task") or record["task"],
        "halo_status": "success",
        "execution_classification": diagnosis.get("execution_classification", ""),
        "primary_failure_mode": diagnosis.get("primary_failure_mode", ""),
        "trace_ids": summary.get("trace_ids", []),
        "expected_output_files": summary.get("expected_output_files", []),
        "judge_summary": summary.get("judge_summary", ""),
        "error_findings": diagnosis["error_findings"],
        "proposed_changes": changes,
        "report_path": str(report_path),
        "report_uri": report_path.as_uri(),
    })
    return record, None


def render(args: argparse.Namespace) -> int:
    queue_path = args.queue.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    queue = _read_json(queue_path, "Judge queue")
    if queue.get("version") != 1 or queue.get("producer") != "judge-xiaoyi-results":
        raise ValueError("unsupported Judge queue schema")
    items = queue.get("tasks")
    if not isinstance(items, list):
        raise ValueError("Judge queue tasks must be an array")
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Judge queue tasks must contain objects")
        record, error = _task_record(item, output_root, args.mode)
        records.append(record)
        if error:
            errors.append(error)

    output = args.output.resolve() if args.output else output_root / "batch_diagnosis_report.html"
    existing = read_batch_payload(output)
    merged = _merge(existing.get("tasks", []) if existing else [], records)
    archives = list(existing.get("archives", [])) if existing else []
    now = datetime.now(timezone.utc)
    created: list[str] = []
    if args.archive_threshold and len(merged) > args.archive_threshold:
        old, merged = merged[:-args.archive_threshold], merged[-args.archive_threshold:]
        for index in range(0, len(old), args.archive_threshold):
            chunk = old[index:index + args.archive_threshold]
            path = _archive_path(output, now, index // args.archive_threshold + 1)
            _write_payload(path, {
                "payload_schema_version": PAYLOAD_VERSION,
                "source": str(queue_path),
                "diagnose_mode": args.mode,
                "generated_at": now.isoformat(),
                "batch_runs": 1,
                "tasks": chunk,
                "errors": [],
                "archives": [],
                "is_archive": True,
            })
            archives.append({"file": path.name, "task_count": len(chunk), "created_at": now.isoformat()})
            created.append(str(path))
    _write_payload(output, {
        "payload_schema_version": PAYLOAD_VERSION,
        "source": str(queue_path),
        "diagnose_mode": args.mode,
        "generated_at": now.isoformat(),
        "batch_runs": (existing.get("batch_runs", 1) + 1) if existing else 1,
        "tasks": merged,
        "errors": errors,
        "archives": archives,
        "is_archive": False,
    })
    print(json.dumps({
        "status": "partial" if errors else "ok",
        "html_report": str(output),
        "html_archives_created": created,
        "task_count": len(records),
        "errors": errors,
    }, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--mode", choices=("all", "failed"), default="all")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--archive-threshold", type=int, default=DEFAULT_ARCHIVE_THRESHOLD)
    args = parser.parse_args()
    if args.archive_threshold < 0:
        parser.error("--archive-threshold must be >= 0")
    try:
        return render(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
