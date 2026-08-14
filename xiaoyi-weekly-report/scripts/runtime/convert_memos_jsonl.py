#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把星芒周报导出的 memos.jsonl（包含 create_memo tool_call 事件）
转换成扁平、易用的备忘录数据结构。

输入（raw JSONL）示例：
  {"event": "tool_call", "payload": {"tool_name": "create_memo", "args": {"title": "...", "content": "...", ...}}}

输出：
  - memos_simple.json      简化后的备忘录数组
  - memo_<id>.json         单条备忘录（方便逐条查看/调试）
  - batch_memos.json       可直接喂给 BatchToolExecuteAbility 的 intent 任务数组

简化后单条结构：
  {
    "id": "9",
    "title": "准备下周面试安排",
    "content": "...",
    "person": "叶知予",
    "date": "07日",
    "weekday": "周日",
    "recordTime": "2026-07-07 16:15",
    "timestamp": "2026-07-07T09:00:00+08:00"
  }
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def extract_memo_from_event(event: dict) -> Optional[Dict[str, Any]]:
    """从一条 tool_call 事件里提取 create_memo 的参数。"""
    if event.get("event") != "tool_call":
        return None
    payload = event.get("payload") or {}
    if payload.get("tool_name") != "create_memo":
        return None
    args = payload.get("args") or {}

    content = args.get("content") or ""
    # 从 content 里尝试提取“记录时间：yyyy-mm-dd HH:MM”
    record_time = ""
    m = re.search(r"记录时间[：:]\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", content)
    if m:
        record_time = m.group(1)

    return {
        "id": str(args.get("memoId") or ""),
        "title": args.get("title") or "",
        "content": content,
        "person": args.get("person") or "",
        "date": args.get("date") or "",
        "weekday": args.get("weekday") or "",
        "recordTime": record_time,
        "timestamp": args.get("timestamp") or "",
    }


def load_raw_memos(input_path: Path) -> List[Dict[str, Any]]:
    """读取 JSONL，返回所有 create_memo 的简化记录。"""
    memos: List[Dict[str, Any]] = []
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[WARN] JSON 解析失败，跳过: {e}", file=sys.stderr)
                continue
            memo = extract_memo_from_event(event)
            if memo:
                memos.append(memo)
    return memos


def to_batch_item(memo: Dict[str, Any]) -> Dict[str, Any]:
    """生成 BatchToolExecuteAbility intent 模式可识别的单条任务。"""
    return {
        "tool": "createNote",
        "args": {
            "title": memo["title"],
            "content": memo["content"],
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="把 memos.jsonl 转换成易用的备忘录结构")
    parser.add_argument(
        "-i", "--input",
        required=True,
        help="原始 memos.jsonl 路径"
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="输出目录，默认与输入文件同目录下的 memo_samples/"
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"[ERROR] 输入文件不存在: {input_path}", file=sys.stderr)
        return 1

    output_dir = Path(args.output) if args.output else input_path.parent / "memo_samples"
    output_dir.mkdir(parents=True, exist_ok=True)

    memos = load_raw_memos(input_path)
    if not memos:
        print("[WARN] 未解析到任何 create_memo 记录", file=sys.stderr)
        return 0

    # 1. 合并的简化 JSON
    simple_file = output_dir / "memos_simple.json"
    with open(simple_file, "w", encoding="utf-8") as f:
        json.dump(memos, f, ensure_ascii=False, indent=2)

    # 2. 逐条独立文件
    for memo in memos:
        memo_id = memo["id"] or "unknown"
        single_file = output_dir / f"memo_{memo_id}.json"
        with open(single_file, "w", encoding="utf-8") as f:
            json.dump(memo, f, ensure_ascii=False, indent=2)

    # 3. 可直接批量执行的 intent 任务文件
    batch_items = [to_batch_item(m) for m in memos]
    batch_file = output_dir / "batch_memos.json"
    with open(batch_file, "w", encoding="utf-8") as f:
        json.dump(batch_items, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 共转换 {len(memos)} 条备忘录")
    print(f"[INFO] 简化数组: {simple_file}")
    print(f"[INFO] 单条文件: {output_dir / 'memo_*.json'}")
    print(f"[INFO] 批量任务: {batch_file}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None
    sys.exit(main())
