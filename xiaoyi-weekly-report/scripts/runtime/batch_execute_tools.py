#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用批量工具执行脚本：支持方案 B（insight intent）和方案 C（直接 CalendarKit）。

链路：
  本脚本 -> 转换样例数据为 JSON 数组 -> hdc file send 到设备应用沙箱 ->
  hdc shell aa start BatchToolExecuteAbility(voice_pc) -> 读取文件 ->
  根据 mode 执行 insight intent 或直接 CalendarKit -> 写回结果

前置条件：
  - 设备已安装 mirror 产物（voice_pc + systemagent + shared_so_hsp）
  - voice_pc 中的 BatchToolExecuteAbility 已编译进 hap 且 exported: true
  - hdc 已加入 PATH

用法：
  # 方案 C：直接 CalendarKit 创建日历（默认，从 cal-shared-*.json 解析）
  python scripts/batch_execute_tools.py

  # 方案 B：通过 insight intent 创建日历
  python scripts/batch_execute_tools.py --mode intent --tool createCalendarEvent

  # 从简化备忘录结构批量创建备忘录（memo_samples/ 目录）
  python scripts/batch_execute_tools.py --input "D:/.../memo_samples" --input-format simple_memo

  # 直接使用已生成的 batch_memos.json / batch_tools.json
  python scripts/batch_execute_tools.py --input "D:/.../batch_memos.json" --input-format batch_tools

  # 指定目录与设备
  python scripts/batch_execute_tools.py --input "D:/data/排期计划" --device <device-id>
"""

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import re


DEFAULT_INPUT_DIR = str(Path.cwd() / "input")
BUNDLE_NAME = "com.huawei.hmos.vassistant"
ABILITY_NAME = "BatchToolExecuteAbility"
REMOTE_INPUT_FILE = "/data/app/el2/100/base/com.huawei.hmos.vassistant/haps/voice_pc/files/batch_tools.json"
REMOTE_RESULT_FILE = "/data/app/el2/100/base/com.huawei.hmos.vassistant/haps/voice_pc/files/batch_tool_result.json"
APP_DATA_DIR = "/data/app/el2/100/base/com.huawei.hmos.vassistant"
# UID/GID 不再硬编码，改为从设备上 APP_DATA_DIR 的属主自动获取


@dataclass
class CalendarRecord:
    source_file: str
    title: str
    start_time: str
    end_time: str
    location: str
    description: str
    participants: List[str] = field(default_factory=list)


def _extract_tool_call_args(events: List[dict]) -> Optional[dict]:
    for event in events:
        if event.get("event") == "tool_call":
            return event.get("payload", {}).get("args")
    return None


def parse_calendar_sample_file(file_path: Path) -> Optional[CalendarRecord]:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            events = json.load(f)
        if not isinstance(events, list):
            print(f"[WARN] {file_path.name} 不是 JSON 数组，跳过", file=sys.stderr)
            return None

        args = _extract_tool_call_args(events) or {}
        title = args.get("title") or ""
        if not title:
            print(f"[WARN] {file_path.name} 未解析到 title，跳过", file=sys.stderr)
            return None

        return CalendarRecord(
            source_file=file_path.name,
            title=str(title),
            start_time=str(args.get("startTime") or ""),
            end_time=str(args.get("endTime") or ""),
            location=str(args.get("location") or ""),
            description=str(args.get("description") or ""),
            participants=list(args.get("participants") or []),
        )
    except Exception as e:
        print(f"[WARN] 解析 {file_path.name} 失败: {e}，跳过", file=sys.stderr)
        return None


def load_calendar_records(input_dir: str) -> List[CalendarRecord]:
    p = Path(input_dir)
    if not p.exists():
        raise FileNotFoundError(f"输入目录不存在: {input_dir}")

    files = sorted(p.glob("cal-shared-*.json"))
    if not files:
        raise FileNotFoundError(f"在 {input_dir} 下未找到 cal-shared-*.json 文件")

    records: List[CalendarRecord] = []
    for f in files:
        rec = parse_calendar_sample_file(f)
        if rec:
            records.append(rec)
    return records


def convert_to_intent_item(rec: CalendarRecord, tool: str) -> Dict[str, Any]:
    """转换为 intent 模式 item。"""
    return {
        "tool": tool,
        "args": {
            "title": rec.title,
            "startTime": rec.start_time,
            "endTime": rec.end_time,
            "location": rec.location,
            "description": rec.description,
            "participants": rec.participants,
        }
    }


def convert_to_direct_item(rec: CalendarRecord) -> Dict[str, Any]:
    """转换为 direct 模式 item（兼容旧格式）。"""
    return {
        "title": rec.title,
        "startTime": rec.start_time,
        "endTime": rec.end_time,
        "location": rec.location,
        "description": rec.description,
        "participants": rec.participants,
    }


def load_json_array(file_path: Path) -> List[Dict[str, Any]]:
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return data
    raise ValueError(f"{file_path} 内容不是对象或数组")


def detect_input_format(input_path: Path) -> str:
    """根据输入路径自动判断格式。"""
    if input_path.is_file():
        data = load_json_array(input_path)
        if not data:
            raise ValueError(f"{input_path} 为空数组")
        first = data[0]
        if isinstance(first, dict):
            if "tool" in first and "args" in first:
                return "batch_tools"
            if "title" in first and "content" in first:
                return "simple_memo"
            if "event" in first:
                return "tool_call"
        raise ValueError(f"无法识别 {input_path} 的数据格式")

    if input_path.is_dir():
        if list(input_path.glob("cal-shared-*.json")):
            return "tool_call"
        if list(input_path.glob("memo_*.json")) or (input_path / "memos_simple.json").exists():
            return "simple_memo"
        if (input_path / "batch_memos.json").exists() or (input_path / "batch_tools.json").exists():
            return "batch_tools"
        raise FileNotFoundError(
            f"在 {input_path} 下未找到可识别的输入文件（cal-shared-*.json / memo_*.json / memos_simple.json / batch_memos.json / batch_tools.json）"
        )

    raise FileNotFoundError(f"输入路径不存在: {input_path}")


def load_simple_memo_items(input_path: Path) -> List[Dict[str, Any]]:
    """加载简化备忘录结构：支持单文件数组、单条对象，或目录下 memo_*.json / memos_simple.json。"""
    memos: List[Dict[str, Any]] = []

    if input_path.is_file():
        memos.extend(load_json_array(input_path))
    else:
        simple_file = input_path / "memos_simple.json"
        if simple_file.exists():
            memos.extend(load_json_array(simple_file))
        else:
            for f in sorted(input_path.glob("memo_*.json")):
                memos.extend(load_json_array(f))

    # 过滤掉不合法的条目
    valid: List[Dict[str, Any]] = []
    for m in memos:
        if not isinstance(m, dict):
            continue
        if not m.get("title"):
            print(f"[WARN] 跳过无 title 的备忘录: {m}", file=sys.stderr)
            continue
        valid.append(m)
    return valid


def load_batch_tools_items(input_path: Path) -> List[Dict[str, Any]]:
    """加载已经是 BatchToolExecuteAbility 输入格式的 JSON 数组。"""
    if input_path.is_file():
        return load_json_array(input_path)

    candidates = ["batch_memos.json", "batch_tools.json"]
    for name in candidates:
        f = input_path / name
        if f.exists():
            return load_json_array(f)
    raise FileNotFoundError(f"在 {input_path} 下未找到 batch_memos.json 或 batch_tools.json")


def load_items(
    input_path: Path,
    fmt: str,
    mode: str,
    tool: str,
) -> List[Dict[str, Any]]:
    """根据格式加载并转换为 BatchToolExecuteAbility 可识别的 item 数组。"""
    if fmt == "auto":
        fmt = detect_input_format(input_path)
        print(f"[INFO] 自动识别输入格式: {fmt}")

    if fmt == "tool_call":
        if input_path.is_file():
            raise ValueError("tool_call 格式需要输入目录（包含 cal-shared-*.json）")
        records = load_calendar_records(str(input_path))
        effective_tool = tool if tool else "createCalendarEvent"
        if mode == "direct":
            return [convert_to_direct_item(rec) for rec in records]
        return [convert_to_intent_item(rec, effective_tool) for rec in records]

    if fmt == "simple_memo":
        memos = load_simple_memo_items(input_path)
        effective_tool = tool if tool else "createNote"
        items: List[Dict[str, Any]] = []
        for m in memos:
            args: Dict[str, Any] = {"title": m["title"], "content": m.get("content", "")}
            # 如果简化结构里还有其他字段，按需透传
            for key in ["person", "date", "weekday", "recordTime", "timestamp"]:
                if key in m:
                    args[key] = m[key]
            items.append({"tool": effective_tool, "args": args})
        return items

    if fmt == "batch_tools":
        return load_batch_tools_items(input_path)

    raise ValueError(f"不支持的输入格式: {fmt}")


def run_hdc(cmd: List[str], timeout: int = 60, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"hdc command failed: {' '.join(cmd)}\nstdout: {result.stdout}\nstderr: {result.stderr}")
    return result


def check_hdc(device: Optional[str] = None) -> bool:
    cmd = ["hdc"]
    if device:
        cmd.extend(["-t", device])
    cmd.extend(["list", "targets"])
    try:
        result = run_hdc(cmd, timeout=10, check=False)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            print("[ERROR] 没有检测到 hdc 目标设备", file=sys.stderr)
            return False
        print(f"[INFO] hdc 设备: {lines}")
        return True
    except FileNotFoundError:
        print("[ERROR] 未找到 hdc 命令", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[ERROR] hdc 检查异常: {e}", file=sys.stderr)
        return False


def detect_app_uid_gid(base_cmd: List[str]) -> Tuple[str, str]:
    """从设备上应用数据目录的属主自动获取 UID/GID。"""
    result = run_hdc(base_cmd + ["shell", f"ls -ld {APP_DATA_DIR}"], timeout=10)
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # 格式：drwx------ 8 20020164 20020164 3440 ...
        m = re.match(r"^\S+\s+\d+\s+(\d+)\s+(\d+)", line)
        if m:
            uid, gid = m.group(1), m.group(2)
            print(f"[INFO] 自动获取应用 UID/GID: {uid}:{gid}")
            return uid, gid
    raise RuntimeError(f"无法从 {APP_DATA_DIR} 的 ls 输出中解析 UID/GID")


def push_input_file(base_cmd: List[str], local_file: Path) -> None:
    """将输入文件 push 到应用沙箱，并修正 owner/permission。"""
    uid, gid = detect_app_uid_gid(base_cmd)
    # 获取 root
    run_hdc(base_cmd + ["target", "mount"], timeout=10, check=False)
    remote_dir = os.path.dirname(REMOTE_INPUT_FILE)
    run_hdc(base_cmd + ["shell", f"mkdir -p {remote_dir}"], timeout=10)
    run_hdc(base_cmd + ["file", "send", str(local_file), REMOTE_INPUT_FILE], timeout=30)
    # 目录和文件都需修正属主，否则应用通过沙箱路径写入结果文件时会 Permission denied
    run_hdc(base_cmd + ["shell", f"chown {uid}:{gid} {remote_dir}"], timeout=10)
    run_hdc(base_cmd + ["shell", f"chmod 770 {remote_dir}"], timeout=10)
    run_hdc(base_cmd + ["shell", f"chown {uid}:{gid} {REMOTE_INPUT_FILE}"], timeout=10)
    run_hdc(base_cmd + ["shell", f"chmod 660 {REMOTE_INPUT_FILE}"], timeout=10)


def read_remote_result(base_cmd: List[str]) -> dict:
    """通过 hdc shell cat 读取结果文件内容。"""
    result = run_hdc(base_cmd + ["shell", f"cat {REMOTE_RESULT_FILE}"], timeout=10, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    try:
        return json.loads(result.stdout)
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="通用批量工具执行：insight intent / 直接 CalendarKit")
    parser.add_argument("-i", "--input", default=DEFAULT_INPUT_DIR, help="输入目录或 JSON 文件")
    parser.add_argument("--device", default=None, help="hdc 目标设备 ID")
    parser.add_argument("--dry-run", action="store_true", help="仅预览转换后的数据，不执行 hdc")
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 条")
    parser.add_argument("--timeout", type=int, default=120, help="Ability 执行超时")
    parser.add_argument("--keep-app-running", action="store_true",
                        help="不在执行前 force-stop 应用（可能因沙箱文件清理导致失败）")
    parser.add_argument("--mode", choices=["direct", "intent"], default="direct",
                        help="执行模式：direct=直接 CalendarKit（方案 C，默认），intent=insight intent（方案 B）")
    parser.add_argument("--tool", default=None,
                        help="intent 模式下使用的工具 ID，如 createCalendarEvent / createNote。不指定时：tool_call 格式默认 createCalendarEvent，simple_memo 格式默认 createNote，batch_tools 格式以文件中为准")
    parser.add_argument("--input-format", choices=["auto", "tool_call", "simple_memo", "batch_tools"], default="auto",
                        help="输入数据格式：auto 自动识别")
    args = parser.parse_args()

    input_path = Path(args.input)

    # 1. 加载并转换数据
    try:
        items = load_items(input_path, args.input_format, args.mode, args.tool)
    except (FileNotFoundError, ValueError) as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    if not items:
        print("[ERROR] 没有解析到有效任务", file=sys.stderr)
        return 1

    if args.limit > 0:
        items = items[:args.limit]

    effective_tool = items[0].get("tool") if items else args.tool
    print(f"[INFO] 共加载 {len(items)} 条待执行任务，模式={args.mode}, tool={effective_tool}")

    if args.dry_run:
        print("\n[DRY-RUN] 转换后的数据预览：")
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0

    # 2. 检查 hdc
    if not check_hdc(args.device):
        return 1

    base_cmd = ["hdc"]
    if args.device:
        base_cmd.extend(["-t", args.device])

    # 3. 写入本地临时文件
    local_tmp = Path(os.environ.get("TEMP", "/tmp")) / "batch_tools.json"
    with open(local_tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"[INFO] 本地临时文件: {local_tmp}")

    try:
        # 4. force-stop 应用并 push 文件到沙箱
        if not args.keep_app_running:
            print("[INFO] force-stop 应用以确保沙箱文件可被读取...")
            run_hdc(base_cmd + ["shell", "aa", "force-stop", BUNDLE_NAME], timeout=10, check=False)
            time.sleep(2)

        print(f"[INFO] 推送输入文件到: {REMOTE_INPUT_FILE}")
        push_input_file(base_cmd, local_tmp)

        # 删除旧结果文件，避免读到历史数据
        run_hdc(base_cmd + ["shell", f"rm -f {REMOTE_RESULT_FILE}"], timeout=10, check=False)

        # 5. 启动 BatchToolExecuteAbility
        print("[INFO] 启动 BatchToolExecuteAbility 批量执行...")
        start_at = time.time()
        run_hdc(base_cmd + [
            "shell", "aa", "start",
            "-b", BUNDLE_NAME,
            "-a", ABILITY_NAME,
            "--ps", "toolDataFile", "batch_tools.json",
        ], timeout=args.timeout)

        # 6. 等待结果文件生成
        print("[INFO] 等待 Ability 执行完成...")
        result: dict = {}
        for _ in range(args.timeout):
            result = read_remote_result(base_cmd)
            if result.get("details"):
                break
            time.sleep(1)

        elapsed = time.time() - start_at
        success = bool(result.get("success"))
        total = int(result.get("total", 0))
        success_count = int(result.get("successCount", 0))
        fail_count = int(result.get("failCount", 0))
        verified_count = int(result.get("verifiedCount", 0))
        unverified_ids = result.get("unverifiedEventIds", [])
        details = result.get("details", [])

        print(f"\n========== 执行结果（耗时 {elapsed:.1f}s）==========")
        print(f"总数:   {total}")
        print(f"成功:   {success_count}")
        print(f"失败:   {fail_count}")
        print(f"已校验: {verified_count}")
        print(f"未校验事件ID: {unverified_ids if unverified_ids else '无'}")
        print(f"整体:   {'成功' if success and fail_count == 0 and len(unverified_ids) == 0 else '失败'}")

        for detail in details:
            status = "OK" if detail.get("success") else "FAIL"
            mode = detail.get("mode", "?")
            tool = detail.get("tool", "")
            event_id = detail.get("eventId")
            title = detail.get("title", "")
            error = detail.get("error", "")
            verified = "verified" if detail.get("verified") else "unverified"
            if mode == "direct":
                print(f"  [{status}/{verified}] idx={detail.get('index')} direct title={title} eventId={event_id} {error}")
            else:
                print(f"  [{status}] idx={detail.get('index')} intent tool={tool} title={title} {error}")

        return 0 if success and fail_count == 0 and len(unverified_ids) == 0 else 2

    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"[ERROR] 执行异常: {e}", file=sys.stderr)
        return 2
    finally:
        if local_tmp.exists():
            local_tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None
    sys.exit(main())
