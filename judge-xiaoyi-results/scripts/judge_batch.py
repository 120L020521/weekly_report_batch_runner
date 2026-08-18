#!/usr/bin/env python3
"""Prepare, validate, and summarize a unified XiaoYi Judge batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ADAPTERS = {"file-organization", "workspacebench", "weekly-report"}
RUNNER_STATUSES = {"completed", "failed", "timeout", "unknown", "not-run"}
EXPECTED_JUDGE_TYPES = {
    "file-organization": "deterministic-file-organization",
    "workspacebench": "codex-subagent",
    "weekly-report": "codex-subagent",
}
EXCLUDED_FINGERPRINT_FILES = {"case_manifest.json", "judge_result.json"}


class JudgeBatchError(ValueError):
    """Raised when a shared Judge contract is invalid."""


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise JudgeBatchError(f"cannot read valid {label} JSON object from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise JudgeBatchError(f"{label} must be a JSON object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _task_id(value: Any, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise JudgeBatchError(f"{label} must be a string or integer")
    normalized = str(value).strip()
    if not normalized or any(char in normalized for char in ("/", "\\", "..")):
        raise JudgeBatchError(f"{label} is unsafe: {value!r}")
    return normalized


def _absolute_path(value: Any, label: str, *, nullable: bool = False) -> Path | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip():
        suffix = " or null" if nullable else ""
        raise JudgeBatchError(f"{label} must be a non-empty absolute path{suffix}")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise JudgeBatchError(f"{label} must be absolute: {value}")
    return path.resolve()


def _copy_file(source: Path, destination: Path, label: str) -> None:
    if not source.is_file():
        raise JudgeBatchError(f"{label} is missing: {source}")
    if source.is_symlink():
        raise JudgeBatchError(f"{label} must not be a symlink: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _copy_tree(source: Path, destination: Path, label: str, *, omit_outputs: bool = False) -> None:
    if not source.is_dir():
        raise JudgeBatchError(f"{label} directory is missing: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    for item in sorted(source.rglob("*"), key=lambda value: value.relative_to(source).as_posix().casefold()):
        if item.is_symlink():
            raise JudgeBatchError(f"{label} must not contain symlinks: {item}")
        relative = item.relative_to(source)
        if omit_outputs and relative.parts and relative.parts[0].casefold() in {"output", "outputs"}:
            continue
        target = destination / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    files = sorted(
        (
            path for path in root.rglob("*")
            if path.is_file() and path.name not in EXCLUDED_FINGERPRINT_FILES
        ),
        key=lambda value: value.relative_to(root).as_posix().casefold(),
    )
    for path in files:
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        file_hash = _sha256(path)
        record = {"path": relative, "size": size, "sha256": file_hash}
        records.append(record)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
    return {
        "algorithm": "sha256",
        "value": digest.hexdigest(),
        "fileCount": len(records),
    }, records


def _validate_metadata(metadata: dict[str, Any], task_id: str, adapter: str) -> list[str]:
    declared_id = metadata.get("absolute_id", metadata.get("taskId", metadata.get("id")))
    if declared_id is not None and _task_id(declared_id, "metadata task id") != task_id:
        raise JudgeBatchError("metadata task id does not match judge batch task_id")
    declared_adapter = metadata.get("adapter")
    if declared_adapter is not None and declared_adapter != adapter:
        raise JudgeBatchError("metadata.adapter does not match judge batch adapter")
    task = metadata.get("task")
    if not isinstance(task, str) or not task.strip():
        raise JudgeBatchError("metadata.task must be a non-empty string")
    rubrics = metadata.get("rubrics")
    if not isinstance(rubrics, list) or not rubrics or not all(
        isinstance(item, str) and item.strip() for item in rubrics
    ):
        raise JudgeBatchError("metadata.rubrics must be a non-empty string list")
    return rubrics


def _validate_result_value(
    result: dict[str, Any], metadata: dict[str, Any], manifest: dict[str, Any]
) -> None:
    task_id = _task_id(manifest.get("taskId"), "case_manifest.taskId")
    adapter = manifest.get("adapter")
    if adapter not in ADAPTERS:
        raise JudgeBatchError("case_manifest.adapter is unsupported")
    if result.get("version") != 1:
        raise JudgeBatchError("judge_result.version must be 1")
    if _task_id(result.get("taskId"), "judge_result.taskId") != task_id:
        raise JudgeBatchError("judge_result.taskId does not match case_manifest")
    if result.get("judgeType") != EXPECTED_JUDGE_TYPES[adapter]:
        raise JudgeBatchError(f"judge_result.judgeType is invalid for adapter {adapter}")
    if result.get("inputFingerprint") != manifest.get("inputFingerprint"):
        raise JudgeBatchError("judge_result.inputFingerprint does not match case_manifest")
    status = result.get("status")
    if status == "error":
        if not isinstance(result.get("error"), str) or not result["error"].strip():
            raise JudgeBatchError("error Judge result requires a non-empty error")
        return
    if status != "success":
        raise JudgeBatchError("judge_result.status must be success or error")
    rubrics = _validate_metadata(metadata, task_id, adapter)
    rows = result.get("rubrics")
    if not isinstance(rows, list) or len(rows) != len(rubrics):
        raise JudgeBatchError("judge_result.rubrics length does not match metadata")
    passed_count = 0
    for index, (row, rubric) in enumerate(zip(rows, rubrics)):
        if not isinstance(row, dict):
            raise JudgeBatchError(f"judge_result.rubrics[{index}] must be an object")
        if row.get("index") != index or row.get("rubric") != rubric:
            raise JudgeBatchError(f"judge_result.rubrics[{index}] does not match metadata order")
        if not isinstance(row.get("passed"), bool):
            raise JudgeBatchError(f"judge_result.rubrics[{index}].passed must be boolean")
        confidence = row.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise JudgeBatchError(f"judge_result.rubrics[{index}].confidence must be in [0, 1]")
        if not isinstance(row.get("evidence"), str) or not row["evidence"].strip():
            raise JudgeBatchError(f"judge_result.rubrics[{index}].evidence must be non-empty")
        passed_count += int(row["passed"])
    total = len(rubrics)
    expected_summary = {"total": total, "passed": passed_count, "failed": total - passed_count}
    if result.get("summary") != expected_summary:
        raise JudgeBatchError("judge_result.summary arithmetic is invalid")
    score = result.get("score")
    expected_score = passed_count / total
    if isinstance(score, bool) or not isinstance(score, (int, float)) or abs(score - expected_score) > 1e-12:
        raise JudgeBatchError("judge_result.score is invalid")
    if result.get("passed") is not (passed_count == total):
        raise JudgeBatchError("judge_result.passed is invalid")
    if not isinstance(result.get("feedback"), str):
        raise JudgeBatchError("judge_result.feedback must be a string")


def _load_prepared(prepared_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata = _read_object(prepared_dir / "metadata.json", "prepared metadata")
    manifest = _read_object(prepared_dir / "case_manifest.json", "case manifest")
    current, _files = _fingerprint(prepared_dir)
    if manifest.get("inputFingerprint") != current:
        raise JudgeBatchError("prepared evidence fingerprint no longer matches case_manifest")
    _validate_metadata(metadata, _task_id(manifest.get("taskId"), "case_manifest.taskId"), manifest.get("adapter"))
    return metadata, manifest


def _validate_entry_shape(entry: dict[str, Any], judge_root: Path) -> dict[str, Any]:
    task_id = _task_id(entry.get("task_id"), "tasks[].task_id")
    adapter = entry.get("adapter")
    if adapter not in ADAPTERS:
        raise JudgeBatchError(f"task {task_id} adapter is unsupported: {adapter!r}")
    runner_status = entry.get("runner_status")
    if runner_status not in RUNNER_STATUSES:
        raise JudgeBatchError(f"task {task_id} runner_status is unsupported: {runner_status!r}")
    execution_outcome = entry.get("execution_outcome")
    if execution_outcome is not None and (
        not isinstance(execution_outcome, str) or not execution_outcome.strip()
    ):
        raise JudgeBatchError(f"task {task_id} execution_outcome must be a non-empty string or null")
    if "evidence_ready" in entry:
        evidence_ready = entry["evidence_ready"]
        if not isinstance(evidence_ready, bool):
            raise JudgeBatchError(f"task {task_id} evidence_ready must be boolean")
    else:
        # Schema-v1 batches written before evidence readiness was separated from
        # Runner completion remain valid and keep their original eligibility.
        evidence_ready = runner_status == "completed"
    required_keys = {"metadata", "data", "outputs", "runner_dir", "trace", "judge_dir"}
    missing = sorted(required_keys - set(entry))
    if missing:
        raise JudgeBatchError(f"task {task_id} is missing explicit path keys: {', '.join(missing)}")
    judge_dir = _absolute_path(entry["judge_dir"], f"task {task_id} judge_dir")
    assert judge_dir is not None
    try:
        judge_dir.relative_to(judge_root)
    except ValueError as exc:
        raise JudgeBatchError(f"task {task_id} judge_dir must stay below judge_root") from exc
    if judge_dir == judge_root:
        raise JudgeBatchError(f"task {task_id} judge_dir must be below, not equal to, judge_root")
    return {
        "task_id": task_id,
        "adapter": adapter,
        "runner_status": runner_status,
        "execution_outcome": execution_outcome.strip() if execution_outcome is not None else None,
        "evidence_ready": evidence_ready,
        "metadata": _absolute_path(entry["metadata"], f"task {task_id} metadata", nullable=True),
        "data": _absolute_path(entry["data"], f"task {task_id} data", nullable=True),
        "outputs": _absolute_path(entry["outputs"], f"task {task_id} outputs", nullable=True),
        "runner_dir": _absolute_path(entry["runner_dir"], f"task {task_id} runner_dir", nullable=True),
        "trace": _absolute_path(entry["trace"], f"task {task_id} trace", nullable=True),
        "judge_dir": judge_dir,
    }


def _prepare_entry(entry: dict[str, Any], judge_root: Path, *, force: bool) -> dict[str, Any]:
    values = _validate_entry_shape(entry, judge_root)
    task_id = values["task_id"]
    record: dict[str, Any] = {
        "taskId": task_id,
        "adapter": values["adapter"],
        "runnerStatus": values["runner_status"],
        "executionOutcome": values["execution_outcome"],
        "evidenceReady": values["evidence_ready"],
        "metadata": str(values["metadata"]) if values["metadata"] else None,
        "trace": str(values["trace"]) if values["trace"] else None,
        "status": "runner-failure",
        "action": "not-judged",
        "preparedDir": str(values["judge_dir"]),
        "result": str(values["judge_dir"] / "judge_result.json"),
    }
    if not values["evidence_ready"]:
        record["error"] = (
            "Judge evidence is not ready "
            f"(runner_status={values['runner_status']!r}, "
            f"execution_outcome={values['execution_outcome']!r})"
        )
        return record
    if values["metadata"] is None or values["outputs"] is None:
        raise JudgeBatchError(f"task {task_id} evidence-ready task requires metadata and outputs")

    metadata = _read_object(values["metadata"], f"task {task_id} metadata")
    _validate_metadata(metadata, task_id, values["adapter"])
    destination: Path = values["judge_dir"]
    temporary = destination.with_name("." + destination.name + ".prepare")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    try:
        _copy_file(values["metadata"], temporary / "metadata.json", "metadata")
        _copy_tree(values["outputs"], temporary / "output", "Runner outputs")
        if values["data"] is not None:
            _copy_tree(values["data"], temporary / "data", "source data")
        if values["runner_dir"] is not None:
            _copy_tree(values["runner_dir"], temporary / "runner", "Runner evidence", omit_outputs=True)
        if values["trace"] is not None:
            if not values["trace"].is_file():
                raise JudgeBatchError(f"task {task_id} trace is missing: {values['trace']}")
            runner_target = temporary / "runner" / values["trace"].name
            if not runner_target.exists():
                _copy_file(values["trace"], runner_target, "Runner trace")
        fingerprint, files = _fingerprint(temporary)
        manifest = {
            "version": 1,
            "adapter": values["adapter"],
            "taskId": task_id,
            "runnerStatus": values["runner_status"],
            "executionOutcome": values["execution_outcome"],
            "evidenceReady": values["evidence_ready"],
            "sourcePaths": {
                "metadata": str(values["metadata"]),
                "data": str(values["data"]) if values["data"] else None,
                "outputs": str(values["outputs"]),
                "runnerDir": str(values["runner_dir"]) if values["runner_dir"] else None,
                "trace": str(values["trace"]) if values["trace"] else None,
            },
            "inputFingerprint": fingerprint,
            "files": files,
        }
        _write_json(temporary / "case_manifest.json", manifest)

        if destination.is_dir() and not force:
            try:
                existing_manifest = _read_object(destination / "case_manifest.json", "existing case manifest")
                same = existing_manifest.get("inputFingerprint") == fingerprint
            except JudgeBatchError:
                same = False
            if same:
                shutil.rmtree(temporary)
                result_path = destination / "judge_result.json"
                if result_path.is_file():
                    try:
                        old_metadata, old_manifest = _load_prepared(destination)
                        _validate_result_value(_read_object(result_path, "Judge result"), old_metadata, old_manifest)
                        record.update({"status": "resumed", "action": "resumed"})
                        return record
                    except JudgeBatchError:
                        result_path.unlink()
                record.update({"status": "ready", "action": "judge"})
                return record

        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary.replace(destination)
        record.update({"status": "ready", "action": "judge"})
        return record
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def prepare(args: argparse.Namespace) -> int:
    batch_path = Path(args.batch).expanduser().resolve()
    batch = _read_object(batch_path, "Judge batch")
    if batch.get("schema_version") != 1 or batch.get("producer") != "run-xiaoyi":
        raise JudgeBatchError("judge batch must have schema_version 1 and producer run-xiaoyi")
    if batch.get("runner_finished") is not True:
        raise JudgeBatchError("judge batch runner_finished must be true before Prepare")
    judge_root = _absolute_path(batch.get("judge_root"), "judge_root")
    assert judge_root is not None
    entries = batch.get("tasks")
    if not isinstance(entries, list) or not entries or not all(isinstance(item, dict) for item in entries):
        raise JudgeBatchError("judge batch tasks must be a non-empty object array")
    task_ids = [_task_id(item.get("task_id"), "tasks[].task_id") for item in entries]
    if len(set(task_ids)) != len(task_ids):
        raise JudgeBatchError("judge batch task_id values must be globally unique")
    judge_root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for entry in entries:
        try:
            records.append(_prepare_entry(entry, judge_root, force=args.force))
        except (OSError, JudgeBatchError) as exc:
            task_id = _task_id(entry.get("task_id"), "tasks[].task_id")
            records.append({
                "taskId": task_id,
                "adapter": entry.get("adapter"),
                "runnerStatus": entry.get("runner_status"),
                "executionOutcome": entry.get("execution_outcome"),
                "evidenceReady": entry.get("evidence_ready", entry.get("runner_status") == "completed"),
                "metadata": entry.get("metadata") if isinstance(entry.get("metadata"), str) else None,
                "trace": entry.get("trace") if isinstance(entry.get("trace"), str) else None,
                "status": "input-error",
                "action": "not-judged",
                "error": str(exc),
            })
    queue = {
        "version": 1,
        "producer": "judge-xiaoyi-results",
        "runId": batch.get("run_id"),
        "sourceBatch": str(batch_path),
        "judgeRoot": str(judge_root),
        "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "taskIds": task_ids,
        "tasks": records,
    }
    queue_path = judge_root / "judge_queue.json"
    _write_json(queue_path, queue)
    print(json.dumps({
        "status": "prepared",
        "judgeRoot": str(judge_root),
        "queue": str(queue_path),
        "ready": [row["taskId"] for row in records if row["status"] == "ready"],
        "resumed": [row["taskId"] for row in records if row["status"] == "resumed"],
        "notJudgeable": [row["taskId"] for row in records if row["status"] in {"runner-failure", "input-error"}],
    }, ensure_ascii=False, indent=2))
    return 0


def validate_result(args: argparse.Namespace) -> int:
    prepared_dir = Path(args.prepared_dir).expanduser().resolve()
    result_path = Path(args.result).expanduser().resolve() if args.result else prepared_dir / "judge_result.json"
    metadata, manifest = _load_prepared(prepared_dir)
    _validate_result_value(_read_object(result_path, "Judge result"), metadata, manifest)
    print(json.dumps({"validation": "complete", "result": str(result_path)}, ensure_ascii=False))
    return 0


def summarize(args: argparse.Namespace) -> int:
    judge_root = Path(args.judge_root).expanduser().resolve()
    queue = _read_object(judge_root / "judge_queue.json", "Judge queue")
    if queue.get("version") != 1 or queue.get("producer") != "judge-xiaoyi-results":
        raise JudgeBatchError("judge_queue.json has an unsupported schema")
    rows: list[dict[str, Any]] = []
    invalid = False
    for item in queue.get("tasks", []):
        if not isinstance(item, dict):
            raise JudgeBatchError("judge_queue.tasks must contain objects")
        row = {
            "taskId": item.get("taskId"),
            "adapter": item.get("adapter"),
            "runnerStatus": item.get("runnerStatus"),
            "executionOutcome": item.get("executionOutcome"),
            "evidenceReady": item.get("evidenceReady", item.get("runnerStatus") == "completed"),
            "action": item.get("action"),
            "judgeStatus": "not-judged",
            "score": None,
            "passedRubrics": 0,
            "totalRubrics": 0,
            "passed": None,
            "preparedDir": item.get("preparedDir"),
            "result": item.get("result"),
        }
        if item.get("status") in {"runner-failure", "input-error"}:
            row["error"] = item.get("error")
            rows.append(row)
            continue
        try:
            prepared_dir = Path(item["preparedDir"])
            metadata, manifest = _load_prepared(prepared_dir)
            result = _read_object(Path(item["result"]), "Judge result")
            _validate_result_value(result, metadata, manifest)
            row["judgeStatus"] = result["status"]
            if result["status"] == "success":
                row.update({
                    "score": result["score"],
                    "passedRubrics": result["summary"]["passed"],
                    "totalRubrics": result["summary"]["total"],
                    "passed": result["passed"],
                })
            else:
                row["error"] = result["error"]
        except (OSError, JudgeBatchError) as exc:
            invalid = True
            row["judgeStatus"] = "error"
            row["error"] = str(exc)
        rows.append(row)
    summary_path = judge_root / "batch_summary.json"
    _write_json(summary_path, {
        "version": 1,
        "producer": "judge-xiaoyi-results",
        "runId": queue.get("runId"),
        "sourceBatch": queue.get("sourceBatch"),
        "writtenAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "taskIds": queue.get("taskIds"),
        "tasks": rows,
    })
    print(json.dumps({
        "status": "error" if invalid else "complete",
        "batchSummary": str(summary_path),
        "tasks": rows,
    }, ensure_ascii=False, indent=2))
    return 1 if invalid else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--batch", required=True)
    prepare_parser.add_argument("--force", action="store_true")
    prepare_parser.set_defaults(handler=prepare)
    validate_parser = subparsers.add_parser("validate-result")
    validate_parser.add_argument("--prepared-dir", required=True)
    validate_parser.add_argument("--result")
    validate_parser.set_defaults(handler=validate_result)
    summarize_parser = subparsers.add_parser("summarize")
    summarize_parser.add_argument("--judge-root", required=True)
    summarize_parser.set_defaults(handler=summarize)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        return args.handler(args)
    except (OSError, JudgeBatchError) as exc:
        print(f"[XiaoYi Judge batch] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
