#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从鸿蒙 PC 设备拉取日历、备忘录,并从数据集复制文件,作为 judge 的源数据。

为什么需要这个脚本:
  judge 需要对照"小艺真正看到的数据",而不是推送的源数据。
  推送时部分字段会被丢弃(如备忘录的 timestamp)、合并或规范化,
  设备里实际存在的数据才是小艺生成周报时的输入。
  本脚本把设备里的真实数据拉回来,供 judge 比对。

链路:
  1. 备忘录:生成 searchNote 任务 JSON -> batch_execute_tools.py 调
     BatchToolExecuteAbility -> SearchNote intent -> 结果写回
     batch_tool_result.json -> 本脚本解析
  2. 日历:生成 searchCalendarEvent 任务 JSON -> 同上走 intent 模式
     (若 SearchCalendarEvent intent 不可用,降级用 BatchToolExecuteAbility
     内置的 verifyCreatedEvents 拉时间范围内事件)
  3. 文件:从 deliverables_final/<人名>/ 复制到 task/<人名>/data/
     镜像 Desktop/Documents/Downloads 三个目录,保持相对路径结构不变。
     不同人物目录结构可能不同 (星芒周报-<人名>/项目交付/达人合作 等),
     用 rglob 递归镜像自动适配,不硬编码子目录名。

前置条件:
  - 备忘录+日历:设备已装 mirror 产物,BatchToolExecuteAbility exported: true,hdc 已加入 PATH
  - 文件:无需连设备,从本地 deliverables_final 数据集复制

用法:
  # 拉取所有,指定人名 -> task/何沐/data/
  python scripts/fetch_device_data.py --person "何沐"

  # 只拉备忘录
  python scripts/fetch_device_data.py --only memos --person "何沐"

  # 只复制文件 (不需要连设备)
  python scripts/fetch_device_data.py --only files --person "何沐"

  # 指定时间范围拉日历(默认本月)
  python scripts/fetch_device_data.py --person "何沐" --cal-start "2026-07-01" --cal-end "2026-07-31"

  # 指定设备
  python scripts/fetch_device_data.py --person "何沐" --device <id>

  # dry-run 看会调什么
  python scripts/fetch_device_data.py --person "何沐" --dry-run
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


SCRIPTS_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = SCRIPTS_DIR.parent
DEFAULT_JUDGE_ROOT = WORKSPACE_DIR / "task"
DEFAULT_DATA_ROOT = WORKSPACE_DIR / "deliverables_final"
BUNDLE_NAME = "com.huawei.hmos.vassistant"

# 设备上 BatchToolExecuteAbility 写回结果的路径
REMOTE_RESULT_FILE = (
    f"/data/app/el2/100/base/{BUNDLE_NAME}/haps/voice_pc/files/batch_tool_result.json"
)

# 三个顶层目录 (数据集侧 Desktop/Documents/Downloads,设备侧 Download 对应 Downloads)
TOP_DIRS = ["Desktop", "Documents", "Downloads"]


def run_cmd(cmd: List[str], timeout: int = 60, check: bool = True,
            capture: bool = True) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
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


def write_batch_tools(items: List[Dict[str, Any]]) -> Path:
    """生成 batch_tools.json 格式的临时文件,喂给 batch_execute_tools.py。"""
    tmp = Path(tempfile.gettempdir()) / "fetch_search_tasks.json"
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return tmp


def call_batch_execute(batch_input: Path, device: Optional[str],
                       dry_run: bool, timeout: int) -> Optional[Dict[str, Any]]:
    """调用 batch_execute_tools.py 执行 search 任务,返回结果 JSON。"""
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

    # batch_execute_tools.py 在 Ability 执行"失败"时返回非 0 退出码,
    # 但对 fetch 场景来说,intent 返回 no data / retCode=-100 都是有效结果,
    # 不应当作错误,继续解析 batch_tool_result.json。
    run_cmd(cmd, timeout=timeout + 60, check=False)

    # 从设备拉回 batch_tool_result.json
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


def parse_iso_to_ms(s: str) -> int:
    """ISO 日期字符串转毫秒 timestamp。"""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def fetch_memos(device: Optional[str], dry_run: bool, timeout: int) -> List[Dict[str, Any]]:
    """通过 SearchNote intent 拉取备忘录列表。

    SearchNote 接受 query 参数(关键词)。空 args 会被 notepad 当作"无查询"返回 no data。
    insight_intent_execute_map.json 里默认 intentParam 是 {"query": "备忘"},只搜含"备忘"的笔记。
    这里依次尝试多种 query 策略,选第一个返回数据的。
    """
    print("\n========== 拉取备忘录 ==========")
    # query 策略:单空格实测稳定命中(空串必失败,已去掉)
    query_candidates = [" ", "*", "备忘", "a"]
    last_memos: List[Dict[str, Any]] = []
    for query in query_candidates:
        print(f"[INFO] 尝试 query={query!r}")
        items = [{"tool": "searchNote", "args": {"query": query}}]
        batch_input = write_batch_tools(items)

        result = call_batch_execute(batch_input, device, dry_run, timeout)
        if result is None:
            # dry-run 或拉取失败,继续尝试下一个
            continue

        memos: List[Dict[str, Any]] = []
        details = result.get("details", [])
        for detail in details:
            intent_result_str = detail.get("intentResult")
            if not intent_result_str:
                continue
            try:
                intent_result = json.loads(intent_result_str)
            except Exception:
                intent_result = {"raw": intent_result_str}
            memos.append({
                "index": detail.get("index"),
                "tool": detail.get("tool"),
                "success": detail.get("success", False),
                "error": detail.get("error"),
                "query": query,
                "result": intent_result,
            })

        # 判断是否有数据:success=true 且 result 不含 "no data"
        has_data = any(
            m.get("success") and "no data" not in str(m.get("result", ""))
            for m in memos
        )
        if has_data:
            print(f"[INFO] query={query!r} 命中,拉取到 {len(memos)} 条备忘录记录")
            return memos
        print(f"[INFO] query={query!r} 无数据,尝试下一个")
        last_memos = memos

    if dry_run:
        print(f"[INFO] dry-run 模式,未实际拉取")
        return []
    print(f"[WARN] 所有 query 策略均无数据,返回最后一次结果")
    return last_memos


def extract_memo_items(memos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从 fetch_memos 的原始结果中提取扁平的备忘录数组。

    原始结构(每条 memos[i]):
      {index, tool, success, error, query, result: {retCode, result: {session, content: {contentData: [{payload: {executeResult: {result: {items: [...]}}}}]}}}}

    扁平化后每条:
      {title, content, createdDate, modifiedDate, entityId, entityName}
    """
    items_out: List[Dict[str, Any]] = []
    for m in memos:
        if not m.get("success"):
            continue
        result = m.get("result") or {}
        # 层层下钻到 items
        inner = result.get("result") if isinstance(result, dict) else None
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
                    items_out.append({
                        "title": item.get("title", ""),
                        "content": item.get("content", ""),
                        "createdDate": item.get("createdDate"),
                        "modifiedDate": item.get("modifiedDate"),
                        "entityId": item.get("entityId", ""),
                        "entityName": item.get("entityName", ""),
                    })
    return items_out


def fetch_calendar(device: Optional[str], dry_run: bool, timeout: int,
                   cal_start: str, cal_end: str) -> List[Dict[str, Any]]:
    """通过 SearchCalendarEvent intent 拉取日历事件。

    searchCalendarEvent 在 BatchToolExecuteAbility 的 getIntentToolConfig 里没注册,
    所以必须显式指定 bundleName/intentName(BatchToolExecuteAbility.ets:120-122 允许)。

    参数 schema (insight_intent_tools.json:284):
      - timeInterval: [start_ms, end_ms] 数组(毫秒,2 个元素)
      - title: 可选,日程标题关键词
      - eventLocation: 可选,位置关键词
    """
    print("\n========== 拉取日历 ==========")
    start_ms = parse_iso_to_ms(cal_start + "T00:00:00+08:00")
    end_ms = parse_iso_to_ms(cal_end + "T23:59:59+08:00")
    print(f"[INFO] 时间范围: {cal_start} ~ {cal_end} ({start_ms} ~ {end_ms} ms)")

    # 显式指定 bundleName/intentName/mode,绕过 getIntentToolConfig 未注册的问题
    # resolveItemMode (BatchToolExecuteAbility.ets:510) 优先看 item.mode,再 item.tool,
    # 都没有就用 defaultMode(--mode intent 传入)。这里显式 mode 更稳妥。
    items = [{
        "mode": "intent",
        "bundleName": "com.huawei.hmos.calendardata",
        "intentName": "SearchCalendarEvent",
        "executeMode": "background",
        "args": {
            "timeInterval": [start_ms, end_ms],
        }
    }]
    batch_input = write_batch_tools(items)

    result = call_batch_execute(batch_input, device, dry_run, timeout)
    if result is None:
        return []

    events: List[Dict[str, Any]] = []
    details = result.get("details", [])
    for detail in details:
        intent_result_str = detail.get("intentResult")
        if not intent_result_str:
            continue
        try:
            intent_result = json.loads(intent_result_str)
        except Exception:
            intent_result = {"raw": intent_result_str}
        events.append({
            "index": detail.get("index"),
            "tool": detail.get("tool"),
            "success": detail.get("success", False),
            "error": detail.get("error"),
            "result": intent_result,
        })
    print(f"[INFO] 拉取到 {len(events)} 条日历记录")
    return events


def _ms_to_str(ms: Optional[int], tz: str = "Asia/Shanghai") -> Optional[str]:
    """毫秒时间戳转可读日期时间字符串 (YYYY-MM-DD HH:MM)。"""
    if ms is None:
        return None
    try:
        dt = datetime.fromtimestamp(ms / 1000, tz=_get_tz(tz))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None


def _get_tz(tz_name: str):
    """获取时区对象,失败时默认 Asia/Shanghai。"""
    from datetime import timezone
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_name)
    except Exception:
        # UTC 偏移兜底
        if tz_name == "UTC":
            return timezone.utc
        return ZoneInfo("Asia/Shanghai")


def extract_calendar_items(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从 fetch_calendar 的原始结果中提取扁平的日历事件数组。

    扁平化后每条:
      {title, dtStart, dtEnd, startDate, endDate, eventLocation, description, ...}

    过滤掉系统节假日(建党节、小暑、大暑等):
      特征是 isAllDay=1(用户推送的工作/私人日程都是 isAllDay=0)
    """
    items_out: List[Dict[str, Any]] = []
    skipped_holidays = 0
    for e in events:
        if not e.get("success"):
            continue
        result = e.get("result") or {}
        inner = result.get("result") if isinstance(result, dict) else None
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
                if not isinstance(item, dict):
                    continue
                if item.get("isAllDay") == 1:
                    skipped_holidays += 1
                    continue
                # 统一用北京时间显示,设备上的 timeZone 字段不可靠
                # (很多事件标 UTC 但实际是按北京时间推送的)
                tz = "Asia/Shanghai"
                items_out.append({
                    "title": item.get("title", ""),
                    "dtStart": item.get("dtStart"),
                    "dtEnd": item.get("dtEnd"),
                    "startDate": _ms_to_str(item.get("dtStart"), tz),
                    "endDate": _ms_to_str(item.get("dtEnd"), tz),
                    "eventLocation": item.get("eventLocation", ""),
                    "description": item.get("description", ""),
                    "isAllDay": item.get("isAllDay"),
                    "eventType": item.get("eventType"),
                    "timeZone": item.get("timeZone", ""),
                    "entityId": item.get("entityId", ""),
                    "entityName": item.get("entityName", ""),
                })
    if skipped_holidays > 0:
        print(f"[INFO] 过滤掉 {skipped_holidays} 条系统节假日 (isAllDay=1)")
    # 按 dtStart 升序排列(从早到晚)
    items_out.sort(key=lambda x: x.get("dtStart") or 0)
    return items_out


def copy_files_from_dataset(person: str, output_dir: Path,
                            dry_run: bool,
                            src_root: Path = DEFAULT_DATA_ROOT,
                            subdirs: Optional[List[str]] = None
                            ) -> Dict[str, List[str]]:
    """从 deliverables_final/<人名>/ 复制文件到 output_dir,返回各目录文件列表。

    镜像复制 Desktop/Documents/Downloads 三个目录下的所有文件,
    保持相对路径结构不变 (rglob 递归,不硬编码子目录名)。
    不同人物的 Documents 子目录名可能不同:
      何沐等 → 星芒周报-<人名>/2026-07/{备忘,排期计划,文件输出,邮件}
      林小雨 → 项目交付/2026-07/{inbox,memo,排期计划,文件输出}
      赵凯   → 达人合作/2026-07/{备忘,排期计划,文件输出,邮件}
    用 rglob 镜像可自动适配这些差异。
    """
    # Documents 下只复制"文件输出",跳过排期计划、邮件、备忘/memo
    DOCS_EXCLUDE_DIRS = {"排期计划", "邮件", "备忘", "memo", "inbox"}

    print("\n========== 从数据集复制文件 ==========")
    dirs = subdirs if subdirs is not None else TOP_DIRS
    src_person = src_root / person
    if not src_person.is_dir():
        print(f"[ERROR] 源人物目录不存在: {src_person}", file=sys.stderr)
        return {}

    result: Dict[str, List[str]] = {}
    for sub in dirs:
        src_dir = src_person / sub
        dst_dir = output_dir / sub
        if not src_dir.is_dir():
            print(f"[SKIP] {sub} - 源目录不存在")
            result[sub] = []
            continue

        files: List[str] = []
        for item in src_dir.rglob("*"):
            if item.is_dir():
                continue
            rel = item.relative_to(src_dir)
            # Documents 下跳过排期计划/邮件/备忘/memo 目录中的文件
            if sub == "Documents":
                parts = set(rel.parts)
                if parts & DOCS_EXCLUDE_DIRS:
                    continue
            dst_file = dst_dir / rel
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            if dry_run:
                print(f"  [DRY-RUN] {rel}")
            else:
                shutil.copy2(item, dst_file)
            files.append(str(rel).replace("\\", "/"))
        result[sub] = files
        print(f"[INFO] {sub}: {len(files)} 个文件")
    return result


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
    if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore

    parser = argparse.ArgumentParser(
        description="从鸿蒙 PC 设备拉取日历、备忘录和文件列表,作为 judge 源数据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--output", default=None,
                        help=f"judge 根目录 (默认 {DEFAULT_JUDGE_ROOT})")
    parser.add_argument("--person", default=None,
                        help="人名,数据写到 <output>/<人名>/data/ 下")
    parser.add_argument("--device", default=None, help="hdc 目标设备 ID")
    parser.add_argument("--dry-run", action="store_true",
                        help="只预览不实际拉取")
    parser.add_argument("--timeout", type=int, default=120,
                        help="batch_execute_tools 超时秒数 (默认 120)")

    parser.add_argument("--only", choices=["memos", "calendar", "files"],
                        default=None, help="只拉取某一项")
    parser.add_argument("--skip", action="append",
                        choices=["memos", "calendar", "files"], default=[],
                        help="跳过某一项,可重复")

    parser.add_argument("--cal-start", default=None,
                        help="日历查询起始日期 YYYY-MM-DD (默认本月1号)")
    parser.add_argument("--cal-end", default=None,
                        help="日历查询结束日期 YYYY-MM-DD (默认今天)")
    parser.add_argument("--docs-subdirs", nargs="+",
                        default=TOP_DIRS,
                        help=f"要复制的顶层目录 (默认 {TOP_DIRS})")
    parser.add_argument("--src-root", default=None,
                        help=f"数据集根目录 (默认 {DEFAULT_DATA_ROOT})")
    args = parser.parse_args()

    # 默认日历时间范围:本月1号到今天
    if args.cal_start is None:
        today = datetime.now()
        args.cal_start = f"{today.year}-{today.month:02d}-01"
    if args.cal_end is None:
        today = datetime.now()
        args.cal_end = f"{today.year}-{today.month:02d}-{today.day:02d}"

    # 解析 --only 与 --skip
    only = args.only
    skip = set(args.skip)
    def enabled(name: str) -> bool:
        if only is not None:
            return only == name
        return name not in skip

    do_memos = enabled("memos")
    do_calendar = enabled("calendar")
    do_files = enabled("files")

    if not (do_memos or do_calendar or do_files):
        print("[ERROR] 没有可执行的步骤", file=sys.stderr)
        return 1

    # 输出目录: <output>/<人名>/data/
    judge_root = Path(args.output) if args.output else DEFAULT_JUDGE_ROOT
    if args.person:
        output_dir = judge_root / args.person / "data"
    else:
        output_dir = judge_root / "data"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] 输出目录: {output_dir}")
    print(f"[INFO] 步骤: memos={do_memos} calendar={do_calendar} files={do_files}")
    print(f"[INFO] device={args.device or '(auto)'} dry-run={args.dry_run}")
    print(f"[INFO] 日历范围: {args.cal_start} ~ {args.cal_end}")

    # 不连设备也要校验 hdc 存在(除了纯 dry-run 或只拉文件)
    needs_device = (do_memos or do_calendar) and not args.dry_run
    if needs_device:
        hdc_check = run_cmd(["hdc", "list", "targets"], timeout=10, check=False)
        if hdc_check.returncode != 0 or not hdc_check.stdout.strip():
            print("[ERROR] 未检测到 hdc 设备", file=sys.stderr)
            return 1

    try:
        result: Dict[str, Any] = {
            "fetched_at": datetime.now().isoformat(),
            "device": args.device or "(auto)",
            "calendar_range": {"start": args.cal_start, "end": args.cal_end}
            if do_calendar else None,
        }

        memos_raw: List[Dict[str, Any]] = []
        memos_flat: List[Dict[str, Any]] = []
        calendar_raw: List[Dict[str, Any]] = []
        calendar_flat: List[Dict[str, Any]] = []
        files_data: Dict[str, List[str]] = {}

        if do_memos:
            memos_raw = fetch_memos(args.device, args.dry_run, args.timeout)
            memos_flat = extract_memo_items(memos_raw)

        if do_calendar:
            calendar_raw = fetch_calendar(
                args.device, args.dry_run, args.timeout,
                args.cal_start, args.cal_end
            )
            calendar_flat = extract_calendar_items(calendar_raw)

        if do_files:
            src_root = Path(args.src_root) if args.src_root else DEFAULT_DATA_ROOT
            files_data = copy_files_from_dataset(
                args.person or "", output_dir, args.dry_run,
                src_root, args.docs_subdirs
            )

        print(f"\n========== 拉取完成 ==========")
        print(f"[INFO] 备忘录: {len(memos_flat)} 条 -> device_memos.json")
        print(f"[INFO] 日历: {len(calendar_flat)} 条 -> device_calendar.json")
        print(f"[INFO] 文件: {sum(len(v) for v in files_data.values())} 个条目 -> device_files.json")

        # 分别写出独立的 JSON,文件名有区分度,方便 judge 单独引用
        if do_memos:
            (output_dir / "device_memos.json").write_text(
                json.dumps(memos_flat, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            # 原始返回,调试用
            if memos_raw:
                (output_dir / "device_memos_raw.json").write_text(
                    json.dumps(memos_raw, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
        if do_calendar:
            (output_dir / "device_calendar.json").write_text(
                json.dumps(calendar_flat, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            # 原始返回,调试用
            if calendar_raw:
                (output_dir / "device_calendar_raw.json").write_text(
                    json.dumps(calendar_raw, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
        if do_files and files_data:
            (output_dir / "device_files.json").write_text(
                json.dumps(files_data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

        return 0

    except RuntimeError as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"\n[ERROR] 执行异常: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
