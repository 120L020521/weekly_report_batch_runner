#!/usr/bin/env python3
"""Normalize weekly-report metadata to the two-rubric Judge contract."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


FORMAT_LABELS = {
    ".md": "Markdown（.md）",
    ".html": "HTML（.html）",
    ".docx": "Word（.docx）",
}
FORMAT_ORDER = tuple(FORMAT_LABELS)
CONSISTENCY_RUBRIC = (
    "检查生成的周报或日报内容与“周报生成-原始story.xlsx”中对应人员、对应日期范围的记录是否一致；"
    "仅忽略“周一、周二”等星期信息的差异，日期、人员信息和工作事实等其他差异不得忽略。"
)


def _read_metadata(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"metadata must be a JSON object: {path}")
    return value


def _format_extensions(rubrics: Any, path: Path) -> list[str]:
    if not isinstance(rubrics, list):
        raise ValueError(f"metadata.rubrics must be a list: {path}")
    found: set[str] = set()
    for rubric in rubrics:
        if not isinstance(rubric, str):
            continue
        lowered = rubric.casefold()
        for extension in FORMAT_ORDER:
            if re.search(rf"(?<![a-z0-9]){re.escape(extension)}(?![a-z0-9])", lowered):
                found.add(extension)
    ordered = [extension for extension in FORMAT_ORDER if extension in found]
    if not ordered:
        raise ValueError(f"cannot infer required report format from metadata.rubrics: {path}")
    return ordered


def normalized_rubrics(metadata: dict[str, Any], path: Path) -> list[str]:
    extensions = _format_extensions(metadata.get("rubrics"), path)
    labels = [FORMAT_LABELS[extension] for extension in extensions]
    if len(labels) == 1:
        format_rubric = f"生成结果中是否存在{labels[0]}格式的报告文件？"
    else:
        format_rubric = f"生成结果中是否同时存在{'和'.join(labels)}格式的报告文件？"
    return [format_rubric, CONSISTENCY_RUBRIC]


def normalize_tree(metadata_root: Path, *, check: bool) -> tuple[int, list[Path]]:
    changed: list[Path] = []
    matched = 0
    for path in sorted(metadata_root.glob("*/*/metadata.json")):
        metadata = _read_metadata(path)
        if metadata.get("adapter") != "weekly-report":
            continue
        matched += 1
        expected = normalized_rubrics(metadata, path)
        if metadata.get("rubrics") == expected:
            continue
        changed.append(path)
        if not check:
            metadata["rubrics"] = expected
            path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    return matched, changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata_root", type=Path, help="task metadata root")
    parser.add_argument("--check", action="store_true", help="report drift without writing")
    args = parser.parse_args()
    root = args.metadata_root.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"metadata root is missing: {root}")
    matched, changed = normalize_tree(root, check=args.check)
    print(json.dumps({
        "metadataRoot": str(root),
        "weeklyTasks": matched,
        "changed": len(changed),
        "check": args.check,
    }, ensure_ascii=False))
    return 1 if args.check and changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
