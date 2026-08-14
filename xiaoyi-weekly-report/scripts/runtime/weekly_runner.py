#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按人员执行周报 metadata：预制数据、逐任务执行、拉取、最终清理。"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .case_manager import (
    is_case_completed,
    mark_case_completed,
    mark_case_failed,
    mark_case_interrupted,
)
from .hdc_client import (
    HdcCommandLogger,
    HdcError,
    RemoteLog,
    changed_logs,
    hdc_path,
    list_remote_logs,
    remote_shell,
    run_hdc,
    set_hdc_logger,
    shell_quote,
    snapshot,
    target_args,
)
from .log_monitor import (
    TaskTimeoutError,
    has_stop_reason_stop,
    read_remote_stop_candidates,
    today_id,
)
from .task_executor import (
    extract_stop_content,
    force_stop,
    pull_log,
    save_prompt_text,
    start_prompt,
)


RUNNER_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = RUNNER_ROOT.parent
DEFAULT_CONFIG = WORKSPACE_ROOT / "assets" / "weekly_config.json"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

DEFAULTS: dict[str, Any] = {
    "metadata_root": "external:task",
    "deliverables_root": "external:deliverables_final",
    "scripts_root": "../scripts/runtime",
    "output_root": "external:xiaoyi_logs",
    "month": "2026-07",
    "calendar_start": "2026-07-01",
    "calendar_end": "2026-07-31",
    "xiaoyi_timeout": 1800,
    "helper_timeout": 300,
    "poll_seconds": 3,
    "task_interval": 3,
    "person_interval": 5,
    "clear_before_person": True,
    "require_worklog": False,
    "prompt_suffix": "生成的worklog和周报放到桌面上",
    "remote_output_roots": {
        "Download": "/storage/media/100/local/files/Docs/Download",
        "Desktop": "/storage/media/100/local/files/Docs/Desktop",
        "Documents": "/storage/media/100/local/files/Docs/Documents",
        "Workspace": "/storage/Users/currentUser/.xiaoyi/workspace",
        "WorkspaceLegacy": "/storage/User/currentUser/.xiaoyi/workspace",
    },
}

_VIRTUAL_OUTPUT_PATH_MAPPINGS: tuple[tuple[str, str], ...] = (
    ("/storage/User/currentUser/Desktop", "/storage/media/100/local/files/Docs/Desktop"),
    ("/storage/Users/currentUser/Desktop", "/storage/media/100/local/files/Docs/Desktop"),
    ("/data/service/el2/100/hmdfs/account/files/Docs/Desktop", "/storage/media/100/local/files/Docs/Desktop"),
    ("/storage/User/currentUser/Download", "/storage/media/100/local/files/Docs/Download"),
    ("/storage/Users/currentUser/Download", "/storage/media/100/local/files/Docs/Download"),
    ("/data/service/el2/100/hmdfs/account/files/Docs/Download", "/storage/media/100/local/files/Docs/Download"),
    ("/storage/User/currentUser/Documents", "/storage/media/100/local/files/Docs/Documents"),
    ("/storage/Users/currentUser/Documents", "/storage/media/100/local/files/Docs/Documents"),
    ("/data/service/el2/100/hmdfs/account/files/Docs/Documents", "/storage/media/100/local/files/Docs/Documents"),
)
_LOGGED_FILE_EXTENSIONS = "md|markdown|html?|docx?|pdf|xlsx?|csv|jsonl?|txt|log"


@dataclass(frozen=True)
class WeeklyTask:
    person: str
    task_id: str
    metadata_path: Path
    metadata: dict[str, Any]


def _task_case_id(task_id: str) -> str:
    """Return the local case name used under xiaoyi_logs/."""
    if not task_id.isdigit():
        raise ValueError(f"任务 ID 必须是纯数字: {task_id}")
    return f"task{task_id}"


@dataclass(frozen=True)
class RemoteFile:
    path: str
    size: int
    mtime: int
    root_label: str
    root_path: str


def _build_execution_prompt(task_text: str, suffix: str | None) -> str:
    task_text = task_text.rstrip()
    suffix = (suffix or "").strip()
    if not suffix or task_text.endswith(suffix):
        return task_text
    return f"{task_text}\n{suffix}"


def _configured_remote_roots(remote_output_roots: dict[str, str]) -> list[tuple[str, str]]:
    roots = [(str(path).rstrip("/"), str(label)) for label, path in remote_output_roots.items()]
    return sorted(roots, key=lambda item: len(item[0]), reverse=True)


def map_logged_path_to_remote(
    logged_path: str, remote_output_roots: dict[str, str]
) -> dict[str, str] | None:
    """Map Xiaoyi's user-facing file path to the HDC-visible output path."""
    original = logged_path.strip().strip("`\"'")
    if original.startswith("file://"):
        original = original[7:]
    original = original.rstrip(".,;:!?，。；：！？)]}》")
    mapped = original
    for virtual_root, physical_root in sorted(
        _VIRTUAL_OUTPUT_PATH_MAPPINGS, key=lambda item: len(item[0]), reverse=True
    ):
        if original == virtual_root or original.startswith(virtual_root + "/"):
            mapped = physical_root + original[len(virtual_root):]
            break
    for root_path, root_label in _configured_remote_roots(remote_output_roots):
        if mapped == root_path or mapped.startswith(root_path + "/"):
            return {
                "logged_path": logged_path,
                "remote_path": mapped,
                "root_label": root_label,
                "root_path": root_path,
            }
    return None


def _walk_json_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                nested = json.loads(stripped)
            except (json.JSONDecodeError, TypeError):
                return
            if nested != value:
                yield from _walk_json_strings(nested)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_json_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_json_strings(item)


_OUTPUT_PATH_KEYS = {
    "filepath", "outputpath", "savepath", "savedpath", "destinationpath", "targetpath"
}
_WRITE_TOOL_HINTS = ("write", "create", "save", "export", "document", "docx", "worklog")


def _walk_output_path_values(value: Any, *, allow_plain_path: bool = False) -> Iterable[str]:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                yield from _walk_output_path_values(
                    json.loads(stripped), allow_plain_path=allow_plain_path
                )
            except json.JSONDecodeError:
                return
    elif isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if isinstance(item, str) and (
                normalized in _OUTPUT_PATH_KEYS or (allow_plain_path and normalized == "path")
            ):
                yield item
            yield from _walk_output_path_values(item, allow_plain_path=allow_plain_path)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_output_path_values(item, allow_plain_path=allow_plain_path)


def _bash_output_fragments(command: str) -> Iterable[str]:
    marker = re.compile(
        r"(?:>>?|--output(?:=|\s+)|--out(?:=|\s+)|-o\s+|"
        r"save(?:_as)?\s*\(|write(?:_text|_bytes)?\s*\()\s*[\"']?",
        flags=re.IGNORECASE,
    )
    for match in marker.finditer(command):
        yield command[match.end():]


def _paths_from_text(text: str, known_roots: Iterable[str]) -> Iterable[str]:
    stripped = text.strip()
    for root in known_roots:
        if stripped == root or stripped.startswith(root + "/"):
            yield stripped
        pattern = re.compile(
            rf"{re.escape(root)}[^\r\n\"'<>`]*?\.(?:{_LOGGED_FILE_EXTENSIONS})(?=$|[\s\"'<>`\]),，。；;:：])",
            flags=re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            yield match.group(0)


def extract_logged_output_paths(
    local_log: Path,
    *,
    start_byte: int,
    remote_output_roots: dict[str, str],
) -> list[dict[str, str]]:
    """Extract output locations only from bytes appended during this task."""
    raw = local_log.read_bytes()
    new_text = raw[max(0, min(start_byte, len(raw))):].decode("utf-8", errors="replace")
    configured_roots = [root for root, _ in _configured_remote_roots(remote_output_roots)]
    known_roots = configured_roots + [item for pair in _VIRTUAL_OUTPUT_PATH_MAPPINGS for item in pair]
    detected: dict[str, dict[str, str]] = {}
    for line in new_text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_name = str(event.get("event", "")).lower() if isinstance(event, dict) else ""
        payload = event.get("payload", {}) if isinstance(event, dict) else {}
        values: Iterable[str] = ()
        if event_name == "model_output":
            values = _walk_json_strings(payload.get("assistant", payload))
        elif event_name == "tool_call":
            tool_name = str(payload.get("tool_name", "")).lower()
            args = payload.get("args", {})
            if any(hint in tool_name for hint in _WRITE_TOOL_HINTS):
                values = _walk_json_strings(args)
            elif tool_name in {"bash", "shell", "exec"}:
                command = str(args.get("command", "")) if isinstance(args, dict) else ""
                values = _bash_output_fragments(command)
        elif event_name == "tool_result":
            tool_name = str(payload.get("tool_name", "")).lower()
            values = _walk_output_path_values(
                payload, allow_plain_path=any(hint in tool_name for hint in _WRITE_TOOL_HINTS)
            )
        for value in values:
            for candidate in _paths_from_text(value, known_roots):
                mapped = map_logged_path_to_remote(candidate, remote_output_roots)
                if mapped is not None:
                    detected.setdefault(mapped["remote_path"], mapped)
    return list(detected.values())


def _resolve_path(config_dir: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (config_dir / path).resolve()


def load_weekly_config(config_path: Path) -> dict[str, Any]:
    config = dict(DEFAULTS)
    if config_path.is_file():
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"配置必须是 JSON 对象: {config_path}")
        config.update(loaded)
    config_dir = config_path.resolve().parent
    for key in ("metadata_root", "deliverables_root", "scripts_root", "output_root"):
        config[key] = _resolve_path(config_dir, str(config[key]))
    return config


def discover_tasks(metadata_root: Path) -> list[WeeklyTask]:
    tasks: list[WeeklyTask] = []
    for metadata_path in metadata_root.glob("*/*/metadata.json"):
        person = metadata_path.parent.parent.name
        task_id = metadata_path.parent.name
        if not task_id.isdigit():
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("adapter") != "weekly-report":
            raise ValueError(f"adapter 不是 weekly-report: {metadata_path}")
        if metadata.get("person") != person:
            raise ValueError(f"person 与目录不一致: {metadata_path}")
        if str(metadata.get("absolute_id")) != task_id:
            raise ValueError(f"absolute_id 与目录不一致: {metadata_path}")
        task_text = metadata.get("task")
        if not isinstance(task_text, str) or not task_text.strip():
            raise ValueError(f"task 为空: {metadata_path}")
        tasks.append(WeeklyTask(person, task_id, metadata_path, metadata))
    return sorted(tasks, key=lambda item: (int(item.task_id), item.person))


def _group_by_person(tasks: Iterable[WeeklyTask]) -> list[tuple[str, list[WeeklyTask]]]:
    grouped: dict[str, list[WeeklyTask]] = {}
    for task in tasks:
        grouped.setdefault(task.person, []).append(task)
    return sorted(
        ((person, sorted(items, key=lambda item: int(item.task_id))) for person, items in grouped.items()),
        key=lambda pair: int(pair[1][0].task_id),
    )


def _stream_command(cmd: list[str], *, cwd: Path, log_path: Path | None = None) -> None:
    print("$", " ".join(cmd))
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    assert proc.stdout is not None
    log_handle = log_path.open("a", encoding="utf-8") if log_path else None
    try:
        for line in proc.stdout:
            print(line, end="")
            if log_handle:
                log_handle.write(line)
                log_handle.flush()
    finally:
        if log_handle:
            log_handle.close()
    returncode = proc.wait()
    if returncode != 0:
        raise RuntimeError(f"子流程失败(exit={returncode}): {' '.join(cmd)}")


def _helper_command(script: Path, *args: str) -> list[str]:
    return [sys.executable, "-B", str(script), *args]


def _call_clear(config: dict[str, Any], *, target: str | None, dry_run: bool,
                lifecycle_log: Path) -> None:
    cmd = _helper_command(
        config["scripts_root"] / "clear_person_data.py",
        "--cal-start", str(config["calendar_start"]),
        "--cal-end", str(config["calendar_end"]),
        "--timeout", str(config["helper_timeout"]),
    )
    if target:
        cmd.extend(["--device", target])
    if dry_run:
        cmd.append("--dry-run")
    _stream_command(cmd, cwd=WORKSPACE_ROOT, log_path=lifecycle_log)


def _call_push(person: str, config: dict[str, Any], *, target: str | None,
               dry_run: bool, lifecycle_log: Path) -> None:
    person_dir = config["deliverables_root"] / person
    cmd = _helper_command(
        config["scripts_root"] / "push_person_data.py",
        str(person_dir),
        "--month", str(config["month"]),
        "--timeout", str(config["helper_timeout"]),
    )
    if target:
        cmd.extend(["--device", target])
    if dry_run:
        cmd.append("--dry-run")
    _stream_command(cmd, cwd=WORKSPACE_ROOT, log_path=lifecycle_log)


def _call_fetch(person: str, config: dict[str, Any], *, target: str | None,
                dry_run: bool, lifecycle_log: Path) -> None:
    cmd = _helper_command(
        config["scripts_root"] / "fetch_device_data.py",
        "--person", person,
        "--output", str(config["metadata_root"]),
        "--src-root", str(config["deliverables_root"]),
        "--cal-start", str(config["calendar_start"]),
        "--cal-end", str(config["calendar_end"]),
        "--timeout", str(config["helper_timeout"]),
    )
    if target:
        cmd.extend(["--device", target])
    if dry_run:
        cmd.append("--dry-run")
    _stream_command(cmd, cwd=WORKSPACE_ROOT, log_path=lifecycle_log)


def _safe_relative(remote_file: RemoteFile) -> Path:
    prefix = remote_file.root_path + "/"
    relative = remote_file.path[len(prefix):] if remote_file.path.startswith(prefix) else PurePosixPath(remote_file.path).name
    parts = [part for part in PurePosixPath(relative).parts if part not in {"", ".", "..", "/"}]
    return Path(remote_file.root_label, *parts)


def pull_remote_files(files: Iterable[RemoteFile], *, local_root: Path,
                      target: str | None, verbose: bool = False) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for remote_file in files:
        local_path = local_root / _safe_relative(remote_file)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {
            "remote_path": remote_file.path,
            "local_path": str(local_path),
            "size": remote_file.size,
            "mtime": remote_file.mtime,
        }
        try:
            run_hdc(
                [*target_args(target), "file", "recv", remote_file.path, str(local_path)],
                timeout=300,
                verbose=verbose,
            )
            record["status"] = "pulled"
        except Exception as exc:
            record["status"] = "failed"
            record["error"] = str(exc)
        manifest.append(record)
    return manifest


def wait_for_new_stop(*, task_id: str, before: dict[str, tuple[int, int]],
                      target: str | None, timeout_seconds: int,
                      poll_seconds: float, verbose: bool = False) -> RemoteLog:
    deadline = time.monotonic() + timeout_seconds
    active_log: RemoteLog | None = None
    last_message = 0.0
    while time.monotonic() < deadline:
        logs = list_remote_logs(target=target, user_id=None, date_id=today_id(), verbose=False)
        candidates = changed_logs(before, logs)
        if candidates:
            active_log = candidates[0]
        for log in candidates:
            base_size = before.get(log.path, (0, 0))[0]
            text = read_remote_stop_candidates(
                log,
                target=target,
                lines=300,
                start_byte=base_size + 1,
                verbose=verbose and (time.monotonic() - last_message > 180),
            )
            if has_stop_reason_stop(text):
                return log
        if time.monotonic() - last_message > 300:
            print(f"[{task_id}] 等待 baseline 之后的新 stop_reason=stop ...")
            last_message = time.monotonic()
        time.sleep(poll_seconds)
    raise TaskTimeoutError(f"{task_id} 等待新 stop_reason=stop 超时", active_log=active_log)


def _required_formats(metadata: dict[str, Any]) -> set[str]:
    formats: set[str] = set()
    for rubric in metadata.get("rubrics", []):
        formats.update(re.findall(r"\(\.(md|html|docx)\)", str(rubric), flags=re.IGNORECASE))
    return {fmt.lower() for fmt in formats}


def _present_formats(outputs_dir: Path) -> set[str]:
    if not outputs_dir.is_dir():
        return set()
    return {path.suffix.lower().lstrip(".") for path in outputs_dir.rglob("*") if path.is_file()}


def _archive_previous_attempt(task_dir: Path) -> None:
    """保留旧尝试，确保本轮格式校验不被历史输出污染。"""
    if not task_dir.is_dir():
        return
    entries = [entry for entry in task_dir.iterdir() if entry.name != "attempts"]
    if not entries:
        return
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    archive_dir = task_dir / "attempts" / stamp
    archive_dir.mkdir(parents=True, exist_ok=False)
    for entry in entries:
        shutil.move(str(entry), str(archive_dir / entry.name))


def _write_artifact_manifest(task_dir: Path, *, task_id: str,
                             output_records: list[dict[str, Any]],
                             worklog_records: list[dict[str, Any]],
                             logged_paths: list[dict[str, Any]]) -> None:
    manifest = {
        "task_id": task_id,
        "case_id": task_dir.name,
        "pulled_at": datetime.now().isoformat(timespec="seconds"),
        "logged_paths": logged_paths,
        "outputs": output_records,
        "worklogs": worklog_records,
    }
    (task_dir / "artifacts_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def resolve_logged_remote_files(
    logged_paths: list[dict[str, Any]], *, target: str | None, verbose: bool
) -> list[RemoteFile]:
    """Resolve only log-declared files/directories; never scan configured output roots."""
    files: dict[str, RemoteFile] = {}
    for detected in logged_paths:
        remote_path = detected["remote_path"].rstrip("/")
        quoted = shell_quote(remote_path)
        command = (
            f"if [ -f {quoted} ]; then stat -c '%Y|%s|%n' {quoted}; "
            f"elif [ -d {quoted} ]; then find {quoted} -type f "
            "-exec stat -c '%Y|%s|%n' {} \\;; fi; echo __END__"
        )
        try:
            output = remote_shell(command, target=target, timeout=90, verbose=verbose)
        except HdcError as exc:
            detected["status"] = "resolve_failed"
            detected["error"] = str(exc)
            continue
        matched: list[str] = []
        payload = output.split("__END__", 1)[0]
        for line in payload.splitlines():
            parts = line.strip().split("|", 2)
            if len(parts) != 3:
                continue
            mtime_text, size_text, path = parts
            try:
                remote_file = RemoteFile(
                    path=path,
                    size=int(size_text),
                    mtime=int(mtime_text),
                    root_label=detected["root_label"],
                    root_path=detected["root_path"],
                )
            except ValueError:
                continue
            files[path] = remote_file
            matched.append(path)
        detected["matched_files"] = matched
        detected["status"] = "found" if matched else "not_found"
    return sorted(files.values(), key=lambda item: item.path)


def _collect_task_artifacts(
    task_dir: Path,
    *,
    task_id: str,
    target: str | None,
    verbose: bool,
    logged_paths: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """在 force-stop 前直接拉取本轮日志明确声明的产物。"""
    resolved_files = resolve_logged_remote_files(logged_paths, target=target, verbose=verbose)
    worklog_files = [
        item for item in resolved_files
        if "worklog" in item.path.lower() or "work_log" in item.path.lower()
    ]
    worklog_paths = {item.path for item in worklog_files}
    report_files = [item for item in resolved_files if item.path not in worklog_paths]
    output_records = pull_remote_files(
        report_files, local_root=task_dir / "outputs", target=target, verbose=verbose
    )
    worklog_records = pull_remote_files(
        worklog_files, local_root=task_dir / "outputs", target=target, verbose=verbose
    )
    for record in output_records + worklog_records:
        record["selection_source"] = "log"
    _write_artifact_manifest(
        task_dir,
        task_id=task_id,
        output_records=output_records,
        worklog_records=worklog_records,
        logged_paths=logged_paths,
    )
    return output_records, worklog_records


def run_weekly_task(task: WeeklyTask, config: dict[str, Any], *, target: str | None,
                    verbose: bool, dry_run: bool, rerun: bool) -> bool:
    output_root: Path = config["output_root"]
    case_id = _task_case_id(task.task_id)
    task_dir = output_root / case_id
    required_formats = _required_formats(task.metadata)
    execution_prompt = _build_execution_prompt(task.metadata["task"], config.get("prompt_suffix"))
    if not rerun and is_case_completed(case_id, str(output_root)):
        print(f"[{task.task_id}] 已完成，跳过")
        return True

    print(f"\n{'=' * 70}\n[{task.task_id}] {task.person}: {task.metadata['task']}\n{'=' * 70}")
    print(f"[{task.task_id}] 要求格式: {', '.join(sorted(required_formats)) or '(未指定)'}")
    if dry_run:
        print(f"[{task.task_id}] [DRY-RUN] execution prompt:\n{execution_prompt}")
        print(f"[{task.task_id}] [DRY-RUN] 将推送 metadata.task、监控日志并拉取增量周报/worklog")
        return True

    task_dir.mkdir(parents=True, exist_ok=True)
    _archive_previous_attempt(task_dir)
    shutil.copy2(task.metadata_path, task_dir / "metadata.json")
    save_prompt_text(execution_prompt, case_id=case_id, run_dir=str(output_root), tag="prompt")

    before_logs = snapshot(list_remote_logs(target=target, user_id=None, date_id=today_id(), verbose=verbose))

    done_log: RemoteLog | None = None
    local_log: Path | None = None
    failure: str | None = None
    interrupted = False
    output_records: list[dict[str, Any]] = []
    worklog_records: list[dict[str, Any]] = []
    logged_paths: list[dict[str, Any]] = []
    artifacts_collected = False
    try:
        start_prompt(execution_prompt, target=target, verbose=verbose)
        done_log = wait_for_new_stop(
            task_id=task.task_id,
            before=before_logs,
            target=target,
            timeout_seconds=int(config["xiaoyi_timeout"]),
            poll_seconds=float(config["poll_seconds"]),
            verbose=verbose,
        )
        time.sleep(1.5)
        local_log = pull_log(done_log, case_id=case_id, run_dir=str(output_root), target=target, verbose=verbose)
        extract_stop_content(local_log, case_id, output_root)
        logged_paths = extract_logged_output_paths(
            local_log,
            start_byte=before_logs.get(done_log.path, (0, 0))[0],
            remote_output_roots=config["remote_output_roots"],
        )
        output_records, worklog_records = _collect_task_artifacts(
            task_dir,
            task_id=task.task_id,
            target=target,
            verbose=verbose,
            logged_paths=logged_paths,
        )
        artifacts_collected = True
    except KeyboardInterrupt:
        interrupted = True
        failure = "手动中断"
    except Exception as exc:
        failure = str(exc)
        print(f"[{task.task_id}] 执行失败: {exc}", file=sys.stderr)
        if isinstance(exc, TaskTimeoutError) and exc.active_log is not None:
            done_log = exc.active_log
    finally:
        try:
            force_stop(target=target, verbose=verbose)
        except Exception as exc:
            failure = failure or f"force-stop 失败: {exc}"

    if local_log is None and done_log is None:
        try:
            current_logs = list_remote_logs(target=target, user_id=None, date_id=today_id(), verbose=verbose)
            candidates = changed_logs(before_logs, current_logs)
            done_log = candidates[0] if candidates else None
        except Exception:
            done_log = None
    if local_log is None and done_log is not None:
        try:
            local_log = pull_log(done_log, case_id=case_id, run_dir=str(output_root), target=target, verbose=verbose)
            extract_stop_content(local_log, case_id, output_root)
            logged_paths = extract_logged_output_paths(
                local_log,
                start_byte=before_logs.get(done_log.path, (0, 0))[0],
                remote_output_roots=config["remote_output_roots"],
            )
        except Exception as exc:
            failure = failure or f"日志拉取失败: {exc}"

    if not artifacts_collected:
        try:
            output_records, worklog_records = _collect_task_artifacts(
                task_dir,
                task_id=task.task_id,
                target=target,
                verbose=verbose,
                logged_paths=logged_paths,
            )
        except Exception as exc:
            failure = failure or f"产物拉取失败: {exc}"

    failed_pulls = [item for item in output_records + worklog_records if item.get("status") != "pulled"]
    if failed_pulls:
        failure = failure or f"{len(failed_pulls)} 个远端产物拉取失败"
    if not logged_paths:
        failure = failure or "本任务新增日志中未发现可映射的产物路径"
    elif not output_records and not worklog_records:
        failure = failure or "日志中的产物路径在设备上不存在或不含文件"
    present_formats = _present_formats(task_dir / "outputs")
    missing_formats = required_formats - present_formats
    if missing_formats:
        failure = failure or f"缺少要求的输出格式: {', '.join(sorted(missing_formats))}"
    if config.get("require_worklog") and not worklog_records:
        failure = failure or "未发现本任务新增/修改的 worklog"

    result = {
        "person": task.person,
        "required_formats": sorted(required_formats),
        "present_formats": sorted(present_formats),
        "outputs_pulled": sum(1 for item in output_records if item.get("status") == "pulled"),
        "worklogs_pulled": sum(1 for item in worklog_records if item.get("status") == "pulled"),
    }
    if interrupted:
        mark_case_interrupted(case_id, str(output_root))
        raise KeyboardInterrupt
    if failure:
        mark_case_failed(case_id, str(output_root), failure)
        print(f"[{task.task_id}] FAILED: {failure}", file=sys.stderr)
        return False
    mark_case_completed(case_id, str(output_root), result=result)
    print(f"[{task.task_id}] 完成: outputs={result['outputs_pulled']} worklogs={result['worklogs_pulled']}")
    return True


def _write_person_marker(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _task_handoff_entry(task: WeeklyTask, output_root: Path) -> dict[str, Any]:
    case_id = _task_case_id(task.task_id)
    task_dir = output_root / case_id
    marker_names = (
        ("interrupted.json", "interrupted"),
        ("failed.json", "failed"),
        ("completed.json", "complete"),
    )
    outcome = "not-run"
    marker_path: Path | None = None
    for marker_name, marker_outcome in marker_names:
        candidate = task_dir / marker_name
        if candidate.is_file():
            outcome = marker_outcome
            marker_path = candidate
            break
    trace_path = task_dir / f"{case_id}.jsonl"
    return {
        "taskId": task.task_id,
        "person": task.person,
        "executionOutcome": outcome,
        "metadata": str(task.metadata_path.resolve()),
        "trace": str(trace_path.resolve()) if trace_path.is_file() else None,
        "outputs": str((task_dir / "outputs").resolve()),
        "marker": str(marker_path.resolve()) if marker_path else None,
    }


def write_weekly_runner_handoff(
    tasks: list[WeeklyTask], config: dict[str, Any], *, run_date: str, runner_finished: bool
) -> Path:
    output_root: Path = config["output_root"]
    handoff_path = output_root / "weekly_runner_batch.json"
    entries = [_task_handoff_entry(task, output_root) for task in tasks]
    payload = {
        "version": 1,
        "adapter": "weekly-report",
        "runId": run_date,
        "runnerFinished": runner_finished,
        "writtenAt": datetime.now().isoformat(timespec="seconds"),
        "roots": {
            "metadata": str(Path(config["metadata_root"]).resolve()),
            "deliverables": str(Path(config["deliverables_root"]).resolve()),
            "logs": str(output_root.resolve()),
        },
        "taskIds": [task.task_id for task in tasks],
        "tasks": entries,
    }
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return handoff_path


def run_person(person: str, tasks: list[WeeklyTask], config: dict[str, Any], *,
               target: str | None, verbose: bool, dry_run: bool, rerun: bool,
               stop_on_error: bool, skip_push: bool, skip_fetch: bool,
               skip_clear: bool, skip_initial_clear: bool,
               clear_on_interrupt: bool, run_date: str) -> tuple[int, int, bool, bool]:
    output_root: Path = config["output_root"]
    pending = tasks if rerun else [
        task for task in tasks
        if not is_case_completed(_task_case_id(task.task_id), str(output_root))
    ]
    if not pending:
        print(f"[{person}] 任务均已完成，跳过该人员的数据推送与清理")
        return 0, 0, False, False

    lifecycle_log = output_root / f"{person}.{run_date}.lifecycle.log"
    person_result = output_root / f"{person}.{run_date}.person_result.json"
    print(f"\n{'#' * 76}\n人员: {person}，本轮任务 {len(pending)} 个\n{'#' * 76}")
    interrupted = False
    success_count = 0
    fail_count = 0
    lifecycle_error: str | None = None
    cleanup_succeeded = False
    try:
        if config.get("clear_before_person") and not skip_initial_clear:
            print(f"[{person}] 推送前清理设备，确保人员数据隔离")
            _call_clear(config, target=target, dry_run=dry_run, lifecycle_log=lifecycle_log)
        if not skip_push:
            _call_push(person, config, target=target, dry_run=dry_run, lifecycle_log=lifecycle_log)
        for index, task in enumerate(pending, 1):
            print(f"[{person}] 任务进度 {index}/{len(pending)}")
            ok = run_weekly_task(task, config, target=target, verbose=verbose, dry_run=dry_run, rerun=rerun)
            if ok:
                success_count += 1
            else:
                fail_count += 1
                if stop_on_error:
                    break
            if index < len(pending) and float(config["task_interval"]) > 0 and not dry_run:
                time.sleep(float(config["task_interval"]))
    except KeyboardInterrupt:
        interrupted = True
        lifecycle_error = "手动中断"
    except Exception as exc:
        lifecycle_error = str(exc)
        print(f"[{person}] 人员流程失败: {exc}", file=sys.stderr)

    if not interrupted:
        if not skip_fetch:
            try:
                _call_fetch(person, config, target=target, dry_run=dry_run, lifecycle_log=lifecycle_log)
            except Exception as exc:
                lifecycle_error = lifecycle_error or f"设备数据拉取失败: {exc}"
                print(f"[{person}] {lifecycle_error}", file=sys.stderr)
        if not skip_clear:
            try:
                _call_clear(config, target=target, dry_run=dry_run, lifecycle_log=lifecycle_log)
                cleanup_succeeded = True
            except Exception as exc:
                lifecycle_error = lifecycle_error or f"人员数据清理失败: {exc}"
                print(f"[{person}] {lifecycle_error}", file=sys.stderr)
    elif clear_on_interrupt and not skip_clear:
        try:
            _call_clear(config, target=target, dry_run=dry_run, lifecycle_log=lifecycle_log)
            cleanup_succeeded = True
        except Exception as exc:
            lifecycle_error = f"中断后清理失败: {exc}"

    if not dry_run:
        _write_person_marker(person_result, {
            "person": person,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "success": success_count,
            "failed": fail_count,
            "interrupted": interrupted,
            "lifecycle_error": lifecycle_error,
        })
    if interrupted:
        raise KeyboardInterrupt
    return (
        success_count,
        fail_count + (1 if lifecycle_error else 0),
        bool(lifecycle_error),
        cleanup_succeeded,
    )


def _preflight_hdc(target: str | None) -> None:
    hdc_path()
    output = run_hdc(["list", "targets"], timeout=15, verbose=False)
    targets = [
        line.strip() for line in output.splitlines()
        if line.strip() and "empty" not in line.lower()
    ]
    if not targets:
        raise RuntimeError("未检测到 HDC 设备")
    if target and not any(target in line for line in targets):
        raise RuntimeError(f"指定 HDC 设备不在线: {target}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="周报生成任务批跑器")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--person", action="append", help="仅执行指定人员，可重复")
    parser.add_argument("--task", action="append", help="仅执行指定 task ID，可重复")
    parser.add_argument("--device", default=None, help="HDC 目标设备 ID")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list", action="store_true", help="列出发现的人员与任务后退出")
    parser.add_argument("--rerun", action="store_true", help="忽略 completed.json 重新执行")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--skip-push", action="store_true")
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--skip-clear", action="store_true")
    parser.add_argument("--skip-initial-clear", action="store_true")
    parser.add_argument("--clear-on-interrupt", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--log-hdc", action="store_true")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    config = load_weekly_config(config_path)
    if args.device:
        config["hdc_target"] = args.device
    target = config.get("hdc_target")
    tasks = discover_tasks(config["metadata_root"])
    if not tasks:
        print(f"未发现 metadata: {config['metadata_root']}", file=sys.stderr)
        return 1

    all_ids = {task.task_id for task in tasks}
    if args.task:
        unknown = set(args.task) - all_ids
        if unknown:
            print(f"未知 task ID: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 1
        tasks = [task for task in tasks if task.task_id in set(args.task)]
    if args.person:
        tasks = [task for task in tasks if task.person in set(args.person)]
    grouped = _group_by_person(tasks)
    if not grouped:
        print("筛选后没有任务", file=sys.stderr)
        return 1

    if args.list:
        for person, person_tasks in grouped:
            print(f"{person}: " + ", ".join(f"{task.task_id}={task.metadata['task']}" for task in person_tasks))
        return 0

    output_root: Path = config["output_root"]
    output_root.mkdir(parents=True, exist_ok=True)
    if args.log_hdc and not args.dry_run:
        hdc_log = output_root / f"hdc_commands_{args.date}.log"
        set_hdc_logger(HdcCommandLogger(str(hdc_log)))
    if not args.dry_run:
        _preflight_hdc(target)

    total_success = 0
    total_failed = 0
    device_clean = False
    stopped_before_lifecycle = False
    try:
        for person_index, (person, person_tasks) in enumerate(grouped):
            has_pending = args.rerun or any(
                not is_case_completed(_task_case_id(task.task_id), str(output_root))
                for task in person_tasks
            )
            if not has_pending:
                print(f"[{person}] 任务均已完成，跳过该人员的数据推送与清理")
                continue
            if not args.dry_run and not stopped_before_lifecycle:
                print("[batch] 首次人员数据操作前停止小艺一次")
                force_stop(target=target, verbose=args.verbose)
                stopped_before_lifecycle = True

            skip_initial_clear = args.skip_initial_clear or device_clean
            if device_clean and not args.skip_initial_clear:
                print(f"[{person}] 上一人员结束后已清理，跳过重复的推送前清理")

            success, failed, lifecycle_failed, cleanup_succeeded = run_person(
                person,
                person_tasks,
                config,
                target=target,
                verbose=args.verbose,
                dry_run=args.dry_run,
                rerun=args.rerun,
                stop_on_error=args.stop_on_error,
                skip_push=args.skip_push,
                skip_fetch=args.skip_fetch,
                skip_clear=args.skip_clear,
                skip_initial_clear=skip_initial_clear,
                clear_on_interrupt=args.clear_on_interrupt,
                run_date=args.date,
            )
            total_success += success
            total_failed += failed
            device_clean = cleanup_succeeded
            if args.stop_on_error and (failed or lifecycle_failed):
                break
            if person_index < len(grouped) - 1 and float(config["person_interval"]) > 0 and not args.dry_run:
                time.sleep(float(config["person_interval"]))
    except KeyboardInterrupt:
        if not args.dry_run:
            handoff_path = write_weekly_runner_handoff(
                tasks, config, run_date=args.date, runner_finished=False
            )
            print(f"未完成 handoff: {handoff_path}", file=sys.stderr)
        print("\n批跑被手动中断；默认保留当前人员设备数据，便于排查。", file=sys.stderr)
        return 130
    finally:
        set_hdc_logger(None)

    if not args.dry_run:
        handoff_path = write_weekly_runner_handoff(
            tasks, config, run_date=args.date, runner_finished=True
        )
        print(f"Runner handoff: {handoff_path}")
    print(f"\n批跑结束: 成功 {total_success}，失败 {total_failed}")
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
