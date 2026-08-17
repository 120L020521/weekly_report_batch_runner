#!/usr/bin/env python3
"""Read XiaoYi's refreshed history list and resolve the latest dialogPageId."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from .hdc_client import remote_shell


VASSISTANT_BUNDLE = "com.huawei.hmos.vassistant"
VASSISTANT_ABILITY = "PCAgentTaskAbility"
HISTORY_FILE = "/data/app/el2/100/base/com.huawei.hmos.vassistant/files/history_list.json"
SHELL_TIMEOUT = 30


def parse_history_json(text: str) -> list[dict[str, Any]]:
    """Parse history JSON, tolerating the device's occasional extra trailing `]`."""
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        pass

    start = text.find("[")
    if start < 0:
        return []
    position = start
    while True:
        end = text.find("]", position)
        if end < 0:
            return []
        try:
            parsed = json.loads(text[start:end + 1])
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            position = end + 1


def fetch_history_list(
    *,
    target: str | None,
    wait_seconds: float = 5,
    max_retries: int = 3,
    retry_delay: float = 2,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """Refresh and read XiaoYi's history list."""
    command = (
        f"aa start -b {VASSISTANT_BUNDLE} "
        f"-a {VASSISTANT_ABILITY} "
        "--ps launch_type pc_agent_task_list_history"
    )
    output = remote_shell(
        command, target=target, timeout=SHELL_TIMEOUT, verbose=verbose
    )
    if output.strip():
        print(output.strip())
    if wait_seconds > 0:
        time.sleep(wait_seconds)

    command = f"cat {HISTORY_FILE}"
    for attempt in range(1, max_retries + 1):
        output = remote_shell(
            command, target=target, timeout=SHELL_TIMEOUT, verbose=verbose
        )
        history = parse_history_json(output.strip()) if output.strip() else []
        if history:
            return history
        if attempt < max_retries:
            print(
                f"[dialog-history] history_list.json 尚未就绪，重试 "
                f"({attempt}/{max_retries})...",
                file=sys.stderr,
            )
            time.sleep(retry_delay)
    return []


def get_latest_dialog_page_id(
    *, target: str | None, verbose: bool = False
) -> str:
    """Return the latest dialogPageId, or an empty string when unavailable."""
    try:
        history = fetch_history_list(target=target, verbose=verbose)
    except Exception as exc:
        print(f"[dialog-history] 获取 dialogPageId 失败: {exc}", file=sys.stderr)
        return ""
    if not history:
        return ""
    page_id = history[0].get("dialogPageId")
    return page_id if isinstance(page_id, str) else ""
