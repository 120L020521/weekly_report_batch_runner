import tempfile
import unittest
from pathlib import Path


import sys


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_ROOT))

from runtime import fetch_device_data


class FetchDeviceDataTests(unittest.TestCase):
    def test_copy_files_keeps_mail_memo_inbox_and_calendar_sources(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            person = "测试人员"
            src = root / "deliverables_final" / person
            documents = src / "Documents" / "星芒周报-测试人员" / "2026-07"
            for relative in (
                "邮件/mail.msg",
                "备忘/memos.jsonl",
                "memo/memo.json",
                "inbox/inbox.msg",
                "排期计划/cal-shared-1.json",
                "文件输出/report.docx",
            ):
                path = documents / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x", encoding="utf-8")

            output = root / "task" / person / "data"
            copied = fetch_device_data.copy_files_from_dataset(
                person,
                output,
                dry_run=False,
                src_root=root / "deliverables_final",
                subdirs=["Documents"],
            )

            self.assertEqual(
                {
                    "星芒周报-测试人员/2026-07/邮件/mail.msg",
                    "星芒周报-测试人员/2026-07/备忘/memos.jsonl",
                    "星芒周报-测试人员/2026-07/memo/memo.json",
                    "星芒周报-测试人员/2026-07/inbox/inbox.msg",
                    "星芒周报-测试人员/2026-07/排期计划/cal-shared-1.json",
                    "星芒周报-测试人员/2026-07/文件输出/report.docx",
                },
                set(copied["Documents"]),
            )
            self.assertTrue(
                (
                    output
                    / "Documents"
                    / "星芒周报-测试人员"
                    / "2026-07"
                    / "邮件"
                    / "mail.msg"
                ).is_file()
            )


if __name__ == "__main__":
    unittest.main()
