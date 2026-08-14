#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通过 BatchToolExecuteAbility 直接删除设备上的日历、备忘录和工作文件。

不走小艺 Agent 自然语言,避免二次确认和不稳定问题。
链路：
  1. 日历: searchCalendarEvent 拿 entityId -> deleteCalendarEvent 逐条删
  2. 备忘录: searchNote 拿 entityId -> deleteNote 批量删
  3. 文件: hdc shell rm 直接删 Docs 下的 Desktop/Documents/Download 内容

前置条件：
  - 设备已装 mirror 产物,BatchToolExecuteAbility exported: true
  - hdc 已加入 PATH

用法：
  # 清空全部(日历+备忘录+文件)
  python scripts/clear_person_data.py

  # 只清空日历
  python scripts/clear_person_data.py --only calendar

  # 只清空备忘录
  python scripts/clear_person_data.py --only memos

  # 只清空文件
  python scripts/clear_person_data.py --only files

  # 指定设备
  python scripts/clear_person_data.py --device <id>

  # dry-run 只打印不执行
  python scripts/clear_person_data.py --dry-run

  # 指定日历删除时间范围(默认本月)
  python scripts/clear_person_data.py --cal-start "2026-07-01" --cal-end "2026-07-31"
"""

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


SCRIPTS_DIR = Path(__file__).resolve().parent
BUNDLE_NAME = "com.huawei.hmos.vassistant"

REMOTE_RESULT_FILE = (
    f"/data/app/el2/100/base/{BUNDLE_NAME}/haps/voice_pc/files/batch_tool_result.json"
)

# 文件列表的设备根目录
DOCS_ROOT = "/storage/media/100/local/files/Docs"
DEFAULT_DOCS_SUBDIRS = ["Desktop", "Documents", "Download"]


def run_cmd(cmd: List[str], timeout: int = 60, check: bool = True,
            capture: bool = True) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(
        cmd, capture_output=capture, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    if capture and result.stdout:
        print(result.stdout, end="")
    if capture and result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"子命令失败 (exit={result.returncode}): {' '.join(str(c) for c in cmd)}"
        )
    return result


def run_hdc(args: List[str], device: Optional[str] = None,
            timeout: int = 30, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["hdc"]
    if device:
        cmd.extend(["-t", device])
    cmd.extend(args)
    return run_cmd(cmd, timeout=timeout, check=check)


def parse_iso_to_ms(s: str) -> int:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def write_batch_tools(items: List[Dict[str, Any]]) -> Path:
    tmp = Path(tempfile.gettempdir()) / "clear_search_tasks.json"
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return tmp


def call_batch_execute(batch_input: Path, device: Optional[str],
                       dry_run: bool, timeout: int) -> Optional[Dict[str, Any]]:
    script = SCRIPTS_DIR / "batch_execute_tools.py"
    if not script.exists():
        raise FileNotFoundError(f"未找到 {script}")

    cmd: List[str] = [
        sys.executable, "-B", str(script),
        "--input", str(batch_input),
        "--input-format", "batch_tools",
        "--mode", "intent",
        "--timeout", str(timeout),
        "--keep-app-running",
    ]
    if device:
        cmd.extend(["--device", device])
    if dry_run:
        cmd.append("--dry-run")
        run_cmd(cmd, timeout=60)
        return None

    run_cmd(cmd, timeout=timeout + 60, check=False)

    local_result = Path(tempfile.gettempdir()) / "batch_tool_result.json"
    run_hdc(["file", "recv", REMOTE_RESULT_FILE, str(local_result)],
            device=device, timeout=30, check=False)
    if not local_result.is_file():
        print(f"[WARN] 未拉回结果文件 {REMOTE_RESULT_FILE}")
        return None
    try:
        return json.loads(local_result.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] 解析结果文件失败: {e}")
        return None


def _extract_entity_ids(result: Optional[Dict[str, Any]]) -> List[str]:
    """从 batch_tool_result.json 中提取所有 entityId。"""
    return [eid for eid, _ in _extract_entity_items(result)]


def _extract_entity_items(result: Optional[Dict[str, Any]]) -> List[tuple]:
    """从 batch_tool_result.json 中提取 (entityId, isAllDay) 元组列表。"""
    items_out: List[tuple] = []
    if not result:
        return items_out
    details = result.get("details", [])
    for detail in details:
        intent_result_str = detail.get("intentResult")
        if not intent_result_str:
            continue
        try:
            intent_result = json.loads(intent_result_str)
        except Exception:
            continue
        inner = intent_result.get("result") if isinstance(intent_result, dict) else None
        if not isinstance(inner, dict):
            continue
        content = inner.get("content") or {}
        content_data = content.get("contentData") or []
        for entry in content_data:
            payload = (entry or {}).get("payload") or {}
            execute_result = payload.get("executeResult") or {}
            result_obj = execute_result.get("result") or {}
            items = result_obj.get("items") or []
            for item in items:
                if isinstance(item, dict):
                    eid = item.get("entityId")
                    if eid:
                        items_out.append((str(eid), item.get("isAllDay", 0)))
    return items_out


def search_calendar_ids(device: Optional[str], dry_run: bool, timeout: int,
                        cal_start: str, cal_end: str) -> List[str]:
    """搜索日历事件,返回可删除的 entityId 列表(排除系统节假日 isAllDay=1)。"""
    print("\n========== 搜索日历事件 ==========")
    start_ms = parse_iso_to_ms(cal_start + "T00:00:00+08:00")
    end_ms = parse_iso_to_ms(cal_end + "T23:59:59+08:00")
    print(f"[INFO] 时间范围: {cal_start} ~ {cal_end}")

    items = [{
        "mode": "intent",
        "bundleName": "com.huawei.hmos.calendardata",
        "intentName": "SearchCalendarEvent",
        "executeMode": "background",
        "args": {"timeInterval": [start_ms, end_ms]},
    }]
    batch_input = write_batch_tools(items)
    result = call_batch_execute(batch_input, device, dry_run, timeout)
    all_items = _extract_entity_items(result)
    # 只删非全天事件(isAllDay=0),保留系统节假日(isAllDay=1)
    ids = [eid for eid, is_all_day in all_items if is_all_day == 0]
    skipped = len(all_items) - len(ids)
    print(f"[INFO] 搜索到 {len(all_items)} 条日历事件,保留 {skipped} 条系统节假日,将删除 {len(ids)} 条")
    return ids


def search_memo_ids(device: Optional[str], dry_run: bool, timeout: int) -> List[str]:
    """搜索备忘录,返回 entityId 列表。"""
    print("\n========== 搜索备忘录 ==========")
    query = " "
    items = [{"tool": "searchNote", "args": {"query": query}}]
    batch_input = write_batch_tools(items)
    result = call_batch_execute(batch_input, device, dry_run, timeout)
    ids = _extract_entity_ids(result)
    print(f"[INFO] 搜索到 {len(ids)} 条备忘录")
    return ids


def delete_calendars(device: Optional[str], dry_run: bool, timeout: int,
                     cal_start: str, cal_end: str) -> int:
    """搜索并删除日历事件,返回删除条数。"""
    ids = search_calendar_ids(device, dry_run, timeout, cal_start, cal_end)
    if not ids:
        print("[INFO] 无日历事件可删除")
        return 0

    print(f"\n========== 删除 {len(ids)} 条日历事件 ==========")
    items = [{"tool": "deleteCalendarEvent", "args": {"entityId": eid}} for eid in ids]
    batch_input = write_batch_tools(items)
    result = call_batch_execute(batch_input, device, dry_run, timeout)
    if result:
        details = result.get("details", [])
        ok = sum(1 for d in details if d.get("success"))
        fail = len(details) - ok
        print(f"[INFO] 日历删除完成: 成功 {ok}, 失败 {fail}")
        return ok
    return 0


def delete_memos(device: Optional[str], dry_run: bool, timeout: int) -> int:
    """搜索并删除备忘录,返回删除条数。"""
    ids = search_memo_ids(device, dry_run, timeout)
    if not ids:
        print("[INFO] 无备忘录可删除")
        return 0

    print(f"\n========== 删除 {len(ids)} 条备忘录 ==========")
    # deleteNote 接受 items: [{entityId}] 数组,一条调用可删多个
    items = [{"tool": "deleteNote", "args": {"items": [{"entityId": eid} for eid in ids]}}]
    batch_input = write_batch_tools(items)
    result = call_batch_execute(batch_input, device, dry_run, timeout)
    if result:
        details = result.get("details", [])
        ok = sum(1 for d in details if d.get("success"))
        fail = len(details) - ok
        print(f"[INFO] 备忘录删除完成: 成功 {ok}, 失败 {fail}")
        return ok
    return 0


def delete_files(device: Optional[str], dry_run: bool,
                 subdirs: List[str]) -> int:
    """用 hdc shell rm 删除 Docs 下各子目录的内容。"""
    print("\n========== 删除工作文件 ==========")
    deleted = 0
    for sub in subdirs:
        remote = f"{DOCS_ROOT}/{sub}"
        if dry_run:
            print(f"[DRY-RUN] hdc shell rm -rf {remote}/*")
            continue
        # 先列一下有多少文件
        ls_result = run_hdc(["shell", f"ls -R {remote}"], device=device,
                            timeout=30, check=False)
        file_count = sum(1 for line in ls_result.stdout.splitlines()
                         if line.strip() and not line.strip().endswith(":"))
        if file_count == 0:
            print(f"[INFO] {sub}: 无文件,跳过")
            continue
        print(f"[INFO] {sub}: 发现 {file_count} 个条目,执行删除...")
        run_hdc(["shell", f"rm -rf {remote}/*"], device=device,
                timeout=60, check=False)
        # 验证
        ls_after = run_hdc(["shell", f"ls -R {remote}"], device=device,
                           timeout=30, check=False)
        remaining = sum(1 for line in ls_after.stdout.splitlines()
                        if line.strip() and not line.strip().endswith(":"))
        if remaining == 0:
            print(f"[OK] {sub}: 已清空 (删除 {file_count} 个条目)")
            deleted += file_count
        else:
            print(f"[WARN] {sub}: 删除后仍有 {remaining} 个条目")
            deleted += (file_count - remaining)
    return deleted


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
    if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore

    parser = argparse.ArgumentParser(
        description="通过 BatchToolExecuteAbility 直接删除日历、备忘录和工作文件(不走小艺 Agent)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--device", default=None, help="hdc 目标设备 ID")
    parser.add_argument("--dry-run", action="store_true", help="只预览不实际删除")
    parser.add_argument("--timeout", type=int, default=120,
                        help="batch_execute_tools 超时秒数 (默认 120)")
    parser.add_argument("--only", choices=["calendar", "memos", "files"],
                        default=None, help="只删除某一项")
    parser.add_argument("--skip", action="append",
                        choices=["calendar", "memos", "files"], default=[],
                        help="跳过某一项,可重复")
    parser.add_argument("--cal-start", default=None,
                        help="日历删除起始日期 YYYY-MM-DD (默认本月1号)")
    parser.add_argument("--cal-end", default=None,
                        help="日历删除结束日期 YYYY-MM-DD (默认今天)")
    parser.add_argument("--docs-subdirs", nargs="+",
                        default=DEFAULT_DOCS_SUBDIRS,
                        help=f"要清空的 Docs 子目录 (默认 {DEFAULT_DOCS_SUBDIRS})")
    args = parser.parse_args()

    # 默认日历时间范围:本月1号到今天
    if args.cal_start is None:
        today = datetime.now()
        args.cal_start = f"{today.year}-{today.month:02d}-01"
    if args.cal_end is None:
        today = datetime.now()
        args.cal_end = f"{today.year}-{today.month:02d}-{today.day:02d}"

    only = args.only
    skip = set(args.skip)
    def enabled(name: str) -> bool:
        if only is not None:
            return only == name
        return name not in skip

    do_calendar = enabled("calendar")
    do_memos = enabled("memos")
    do_files = enabled("files")

    if not (do_calendar or do_memos or do_files):
        print("[ERROR] 没有可执行的步骤", file=sys.stderr)
        return 1

    print(f"[INFO] 步骤: calendar={do_calendar} memos={do_memos} files={do_files}")
    print(f"[INFO] device={args.device or '(auto)'} dry-run={args.dry_run}")
    print(f"[INFO] 日历范围: {args.cal_start} ~ {args.cal_end}")

    if not args.dry_run:
        hdc_check = run_cmd(["hdc", "list", "targets"], timeout=10, check=False)
        if hdc_check.returncode != 0 or not hdc_check.stdout.strip():
            print("[ERROR] 未检测到 hdc 设备", file=sys.stderr)
            return 1

    try:
        total_deleted = 0
        if do_calendar:
            total_deleted += delete_calendars(
                args.device, args.dry_run, args.timeout,
                args.cal_start, args.cal_end
            )
        if do_memos:
            total_deleted += delete_memos(
                args.device, args.dry_run, args.timeout
            )
        if do_files:
            total_deleted += delete_files(
                args.device, args.dry_run, args.docs_subdirs
            )

        print(f"\n========== 清空完成 ==========")
        print(f"[INFO] 共删除 {total_deleted} 条数据")
        return 0

    except RuntimeError as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"\n[ERROR] 执行异常: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
