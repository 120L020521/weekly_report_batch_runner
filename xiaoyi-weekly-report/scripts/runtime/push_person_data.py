#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
推送单个人物的周报数据到鸿蒙 PC 设备,可选清空旧数据。

链路:
  人物目录 (deliverables_final/<person>) 下包含 Desktop/Documents/Downloads
  以及 Documents/星芒周报-<person>/<YYYY-MM>/{排期计划, 备忘, ...}

  本脚本依次调用 scripts/ 下四个已有脚本:
    1. push-sample-data.py     推文件 (Desktop/Documents/Downloads) → 不需要 export:true
    2. batch_execute_tools.py  推日历 (排期计划/cal-shared-*.json) → 需要 export:true
    3. convert_memos_jsonl.py  转换备忘录 (memos.jsonl → memo_samples/) → 纯本地
    4. batch_execute_tools.py  推备忘录 (simple_memo 格式) → 需要 export:true

  可选:
    --clean-before  推送前先调用 clear_person_data.py 清空日历+备忘录
    --clean-only    只清空不推送 (等价于直接调 clear_person_data.py)

前置条件:
  - 设备已装 mirror 产物 (voice_pc + systemagent + shared_so_hsp)
  - BatchToolExecuteAbility / PCAgentTaskAbility 已 exported: true
  - hdc 已加入 PATH

用法:
  # 默认推何沐 (不带参数)
  python scripts/push_person_data.py

  # 指定人物目录
  python scripts/push_person_data.py "D:/codes/haloworks/deliverables_final/叶知予"

  # 推送前先清空 (切换人物时推荐)
  python scripts/push_person_data.py --clean-before

  # 推送后清空 (批跑下一个人前)
  python scripts/push_person_data.py --clean-after

  # 只清空不推送
  python scripts/push_person_data.py --clean-only

  # 指定月份 / dry-run / limit / device
  python scripts/push_person_data.py --month 2026-07 --dry-run --limit 3 --device <id>
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_PERSON = "何沐"
DEFAULT_DATA_ROOT = Path.cwd() / "deliverables_final"


def run_cmd(cmd: List[str], timeout: Optional[int] = None,
            check: bool = True) -> subprocess.CompletedProcess:
    """运行子命令,统一 UTF-8 输出。"""
    print(f"\n$ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"子命令失败 (exit={result.returncode}): {' '.join(str(c) for c in cmd)}"
        )
    return result


def find_weekly_dir(person_dir: Path, month: Optional[str]) -> Optional[Path]:
    """在 person_dir/Documents/ 下查找周报目录下的 <month> 子目录。

    周报目录名不统一:有"星芒周报-<人名>"、"项目交付"、"达人合作"等,
    所以扫描 Documents 下所有含 YYYY-MM 子目录的文件夹。

    若未指定 month,取所有候选中字典序最大的 (即最新的 YYYY-MM)。
    """
    documents = person_dir / "Documents"
    if not documents.is_dir():
        return None

    candidates: List[Path] = []
    for weekly in documents.iterdir():
        if not weekly.is_dir():
            continue
        if month:
            target = weekly / month
            if target.is_dir():
                candidates.append(target)
        else:
            for m in weekly.iterdir():
                if m.is_dir() and m.name.startswith("20"):
                    candidates.append(m)

    if not candidates:
        return None
    return sorted(candidates)[-1]


def resolve_person_dir(person_arg: Optional[str]) -> Path:
    """解析人物目录。

    - 若传入路径,直接用
    - 若传入人物名 (如 "何沐"),拼到 DEFAULT_DATA_ROOT/<name>
    - 若未传入,用 DEFAULT_PERSON
    """
    if person_arg:
        p = Path(person_arg)
        if p.is_dir():
            return p
        # 可能是裸人物名
        p = Path(DEFAULT_DATA_ROOT) / person_arg
        if p.is_dir():
            return p
        raise FileNotFoundError(f"无法定位人物目录: {person_arg}")
    return Path(DEFAULT_DATA_ROOT) / DEFAULT_PERSON


def call_clean(device: Optional[str], dry_run: bool, timeout: int,
               only: Optional[str] = None) -> None:
    """调用 clear_person_data.py 清空日历+备忘录。"""
    script = SCRIPTS_DIR / "clear_person_data.py"
    if not script.exists():
        raise FileNotFoundError(f"未找到 {script}")

    cmd: List[str] = [sys.executable, "-B", str(script)]
    if device:
        cmd.extend(["--device", device])
    if only:
        cmd.extend(["--only", only])
    if dry_run:
        cmd.append("--dry-run")
    else:
        cmd.extend(["--timeout", str(timeout)])

    run_cmd(cmd, timeout=timeout + 60 if not dry_run else 60)


def call_push_files(person_dir: Path, dry_run: bool) -> None:
    """调用 push-sample-data.py 推送 Desktop/Documents/Downloads。"""
    script = SCRIPTS_DIR / "push-sample-data.py"
    if not script.exists():
        raise FileNotFoundError(f"未找到 {script}")

    if dry_run:
        print("[DRY-RUN] 跳过文件推送 (push-sample-data.py 无 --dry-run,直接跳过)")
        return

    cmd: List[str] = [sys.executable, "-B", str(script), str(person_dir)]
    run_cmd(cmd, timeout=300)


def call_push_calendar(weekly_dir: Path, device: Optional[str],
                       dry_run: bool, limit: int, timeout: int) -> None:
    """调用 batch_execute_tools.py 推送日历 (cal-shared-*.json)。

    日历目录名不统一:有"排期计划"、"calendar"等,
    且赵凯的日历 json 被误放在"备忘"目录下(与 memos.jsonl 混在一起)。
    查找优先级:排期计划 > calendar > 备忘(仅含 cal-shared-*.json 时)。
    """
    calendar_dir = None
    for name in ("排期计划", "calendar"):
        candidate = weekly_dir / name
        if candidate.is_dir() and any(candidate.glob("cal-shared-*.json")):
            calendar_dir = candidate
            break

    if calendar_dir is None:
        # 回退:看"备忘"目录下是否有 cal-shared-*.json(赵凯的情况)
        memo_candidate = weekly_dir / "备忘"
        if memo_candidate.is_dir() and any(memo_candidate.glob("cal-shared-*.json")):
            calendar_dir = memo_candidate
            print(f"[INFO] 日历 json 在备忘目录下,使用: {calendar_dir}")

    if calendar_dir is None:
        print(f"[WARN] 未找到含 cal-shared-*.json 的日历目录,跳过日历推送")
        return

    script = SCRIPTS_DIR / "batch_execute_tools.py"
    if not script.exists():
        raise FileNotFoundError(f"未找到 {script}")

    cmd: List[str] = [
        sys.executable, "-B", str(script),
        "--input", str(calendar_dir),
        "--mode", "direct",
        "--timeout", str(timeout),
        "--keep-app-running",
    ]
    if device:
        cmd.extend(["--device", device])
    if limit > 0:
        cmd.extend(["--limit", str(limit)])
    if dry_run:
        cmd.append("--dry-run")
    run_cmd(cmd, timeout=timeout + 60)


def call_convert_memos(weekly_dir: Path, dry_run: bool) -> Optional[Path]:
    """调用 convert_memos_jsonl.py 转换 memos.jsonl → memo_samples/。

    返回 memo_samples 目录路径 (dry-run 时返回 None,因未实际生成)。
    """
    # 备忘录目录名不统一:有"备忘"、"memo"等
    memo_dir = None
    for name in ("备忘", "memo"):
        candidate = weekly_dir / name
        if candidate.is_dir():
            memo_dir = candidate
            break
    if memo_dir is None:
        print(f"[WARN] 未找到备忘录目录 ({'或'.join(['备忘', 'memo'])}),跳过备忘录推送")
        return None
    memos_jsonl = memo_dir / "memos.jsonl"
    if not memos_jsonl.is_file():
        print(f"[WARN] 未找到 {memos_jsonl},跳过备忘录推送")
        return None

    script = SCRIPTS_DIR / "convert_memos_jsonl.py"
    if not script.exists():
        raise FileNotFoundError(f"未找到 {script}")

    if dry_run:
        # convert_memos_jsonl.py 无 --dry-run,只打印不执行
        print(f"[DRY-RUN] 跳过转换: {script} --input {memos_jsonl}")
        return None

    cmd: List[str] = [sys.executable, "-B", str(script), "--input", str(memos_jsonl)]
    run_cmd(cmd, timeout=60)

    memo_samples = memo_dir / "memo_samples"
    if not memo_samples.is_dir():
        print(f"[WARN] 转换后未生成 {memo_samples}")
        return None
    return memo_samples


def call_push_memos(memo_samples_dir: Path, device: Optional[str],
                    dry_run: bool, limit: int, timeout: int) -> None:
    """调用 batch_execute_tools.py 批量写入备忘录 (simple_memo 格式)。"""
    script = SCRIPTS_DIR / "batch_execute_tools.py"
    if not script.exists():
        raise FileNotFoundError(f"未找到 {script}")

    cmd: List[str] = [
        sys.executable, "-B", str(script),
        "--input", str(memo_samples_dir),
        "--input-format", "simple_memo",
        "--mode", "intent",
        "--tool", "createNote",
        "--timeout", str(timeout),
        "--keep-app-running",
    ]
    if device:
        cmd.extend(["--device", device])
    if limit > 0:
        cmd.extend(["--limit", str(limit)])
    if dry_run:
        cmd.append("--dry-run")
    run_cmd(cmd, timeout=timeout + 60)


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
    if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore

    parser = argparse.ArgumentParser(
        description="推送单个人物的周报数据 (文件+日历+备忘录) 到鸿蒙 PC,可选清空",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("person", nargs="?", default=None,
                        help=f'人物目录或人物名 (默认 {DEFAULT_PERSON},'
                             f'或数据根目录 {DEFAULT_DATA_ROOT}/<name>)')
    parser.add_argument("--month", default=None,
                        help="周报月份子目录 (YYYY-MM),默认自动取最新")
    parser.add_argument("--device", default=None, help="hdc 目标设备 ID")
    parser.add_argument("--dry-run", action="store_true",
                        help="只预览不实际推送 (文件推送直接跳过)")
    parser.add_argument("--limit", type=int, default=0,
                        help="限制日历/备忘录各处理前 N 条 (调试用)")
    parser.add_argument("--timeout", type=int, default=300,
                        help="每个 batch 子任务的超时秒数 (默认 300)")

    # 清空选项
    parser.add_argument("--clean-before", action="store_true",
                        help="推送前先清空日历+备忘录 (切换人物时推荐)")
    parser.add_argument("--clean-only", action="store_true",
                        help="只清空不推送 (等价于直接调 clear_person_data.py)")
    parser.add_argument("--clean-only-target", choices=["calendar", "memos"],
                        default=None,
                        help="清空时只清某一项 (calendar/memos),默认两者都清")

    # 推送步骤控制
    parser.add_argument("--only", choices=["files", "calendar", "memos"],
                        default=None, help="只推送某一项")
    parser.add_argument("--skip", action="append",
                        choices=["files", "calendar", "memos"], default=[],
                        help="跳过某一项,可重复: --skip files --skip memos")
    args = parser.parse_args()

    # --clean-only 短路
    if args.clean_only:
        print("[INFO] --clean-only 模式,只清空不推送")
        try:
            call_clean(args.device, args.dry_run, args.timeout,
                       only=args.clean_only_target)
            print("\n========== 清空完成 ==========")
            return 0
        except RuntimeError as e:
            print(f"\n[ERROR] {e}", file=sys.stderr)
            return 2

    # 解析人物目录
    try:
        person_dir = resolve_person_dir(args.person)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    if not person_dir.is_dir():
        print(f"[ERROR] 人物目录不存在: {person_dir}", file=sys.stderr)
        return 1

    # 解析 --only 与 --skip
    only = args.only
    skip = set(args.skip)
    def enabled(name: str) -> bool:
        if only is not None:
            return only == name
        return name not in skip

    do_files = enabled("files")
    do_calendar = enabled("calendar")
    do_memos = enabled("memos")

    if not (do_files or do_calendar or do_memos):
        print("[ERROR] 没有可执行的步骤 (--only 与 --skip 组合导致全部被禁用)",
              file=sys.stderr)
        return 1

    weekly_dir = find_weekly_dir(person_dir, args.month)
    if weekly_dir is None:
        print(f"[ERROR] 在 {person_dir}/Documents 下未找到 星芒周报-* 目录",
              file=sys.stderr)
        return 1

    print(f"[INFO] 人物目录: {person_dir}")
    print(f"[INFO] 周报目录: {weekly_dir}")
    print(f"[INFO] 步骤: files={do_files} calendar={do_calendar} memos={do_memos}")
    print(f"[INFO] device={args.device or '(auto)'} dry-run={args.dry_run} "
          f"limit={args.limit or '(all)'}")
    print(f"[INFO] clean-before={args.clean_before}")

    try:
        # 0. 推送前清空
        if args.clean_before:
            print("\n========== 步骤 0: 清空旧数据 ==========")
            call_clean(args.device, args.dry_run, args.timeout,
                       only=args.clean_only_target)

        # 1. 推文件
        if do_files:
            print("\n========== 步骤 1: 推送文件 ==========")
            call_push_files(person_dir, args.dry_run)

        # 2. 推日历
        if do_calendar:
            print("\n========== 步骤 2: 推送日历 ==========")
            call_push_calendar(weekly_dir, args.device, args.dry_run,
                               args.limit, args.timeout)

        # 3. 推备忘录 (3a 转换 + 3b 写入)
        if do_memos:
            print("\n========== 步骤 3: 推送备忘录 ==========")
            print("\n----- 3a: 转换 memos.jsonl -----")
            memo_samples = call_convert_memos(weekly_dir, args.dry_run)
            if memo_samples is not None:
                print("\n----- 3b: 批量写入备忘录 -----")
                call_push_memos(memo_samples, args.device, args.dry_run,
                                args.limit, args.timeout)

        print("\n========== 完成 ==========")
        return 0

    except RuntimeError as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"\n[ERROR] 执行异常: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
