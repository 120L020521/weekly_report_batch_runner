import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_ROOT))

from runtime import weekly_runner as runner


class WeeklyRunnerArtifactTests(unittest.TestCase):
    def test_desktop_worklog_scan_supports_common_names(self):
        response = "\n".join(
            [
                "1|10|/desktop/worklog.jsonl",
                "2|11|/desktop/work_log.json",
                "3|12|/desktop/work-log.md",
                "4|13|/desktop/本周工作记录.jsonl",
                "5|14|/desktop/工作快捷区/worklog.jsonl",
                "6|15|/desktop/task21-worklog/events.jsonl",
                "__END__",
            ]
        )
        with patch.object(runner, "remote_shell", return_value=response) as remote:
            files = runner.list_remote_desktop_worklogs(
                {"Desktop": "/desktop"}, target=None, verbose=False
            )

        self.assertEqual(
            {
                "/desktop/worklog.jsonl",
                "/desktop/work_log.json",
                "/desktop/work-log.md",
                "/desktop/本周工作记录.jsonl",
                "/desktop/task21-worklog/events.jsonl",
            },
            {item.path for item in files},
        )
        command = remote.call_args.args[0]
        self.assertIn("*worklog*", command)
        self.assertIn("*work_log*", command)
        self.assertIn("*work-log*", command)
        self.assertIn("-maxdepth 1 -type d", command)
        self.assertIn('find "$worklog_dir" -type f', command)

    def test_task_pulls_desktop_worklog_delta_when_log_omits_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            metadata_path = root / "task" / "测试人员" / "21" / "metadata.json"
            metadata_path.parent.mkdir(parents=True)
            metadata = {
                "person": "测试人员",
                "absolute_id": "21",
                "task": "生成七月份第一周的工作周报",
                "rubrics": ["输出格式要求：Word 文档（.docx）"],
            }
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
            )
            task = runner.WeeklyTask("测试人员", "21", metadata_path, metadata)
            config = {
                "output_root": root / "xiaoyi_logs",
                "prompt_suffix": "生成的worklog和周报放到桌面上",
                "remote_output_roots": {"Desktop": "/desktop"},
                "xiaoyi_timeout": 10,
                "poll_seconds": 0.01,
                "require_worklog": True,
            }
            old_worklog = runner.RemoteFile(
                "/desktop/task21-worklog/events.jsonl", 10, 1, "Desktop", "/desktop"
            )
            new_worklog = runner.RemoteFile(
                "/desktop/task21-worklog/events.jsonl", 20, 2, "Desktop", "/desktop"
            )
            report = runner.RemoteFile(
                "/desktop/七月第一周周报.docx", 30, 2, "Desktop", "/desktop"
            )
            done_log = runner.RemoteLog("100", "task.jsonl", "/logs/task.jsonl", 1, 1)

            def fake_pull_log(*args, **kwargs):
                path = root / "xiaoyi_logs" / "task21" / "task21.jsonl"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")
                return path

            def fake_pull_remote_files(files, *, local_root, target, verbose=False):
                records = []
                for remote_file in files:
                    local_path = local_root / runner._safe_relative(remote_file)
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    local_path.write_text("test", encoding="utf-8")
                    records.append(
                        {
                            "remote_path": remote_file.path,
                            "local_path": str(local_path),
                            "size": remote_file.size,
                            "mtime": remote_file.mtime,
                            "status": "pulled",
                        }
                    )
                return records

            logged_path = {
                "logged_path": "/desktop/七月第一周周报.docx",
                "remote_path": report.path,
                "root_label": "Desktop",
                "root_path": "/desktop",
            }
            with (
                patch.object(
                    runner,
                    "list_remote_desktop_worklogs",
                    side_effect=[[old_worklog], [new_worklog]],
                ),
                patch.object(runner, "list_remote_logs", return_value=[]),
                patch.object(runner, "start_prompt"),
                patch.object(runner, "wait_for_new_stop", return_value=done_log),
                patch.object(runner.time, "sleep"),
                patch.object(runner, "pull_log", side_effect=fake_pull_log),
                patch.object(runner, "extract_stop_content"),
                patch.object(runner, "extract_logged_output_paths", return_value=[logged_path]),
                patch.object(runner, "resolve_logged_remote_files", return_value=[report]),
                patch.object(runner, "pull_remote_files", side_effect=fake_pull_remote_files),
                patch.object(runner, "force_stop"),
            ):
                succeeded = runner.run_weekly_task(
                    task, config, target=None, verbose=False, dry_run=False, rerun=False
                )

            self.assertTrue(succeeded)
            task_dir = root / "xiaoyi_logs" / "task21"
            self.assertTrue(
                (
                    task_dir
                    / "outputs"
                    / "Desktop"
                    / "task21-worklog"
                    / "events.jsonl"
                ).is_file()
            )
            self.assertTrue(
                (task_dir / "outputs" / "Desktop" / "七月第一周周报.docx").is_file()
            )
            manifest = json.loads(
                (task_dir / "artifacts_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual("desktop-worklog-delta", manifest["worklogs"][0]["selection_source"])


if __name__ == "__main__":
    unittest.main()
