import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_ROOT))

from runtime import dialog_history
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
                patch.object(
                    runner,
                    "extract_stop_content",
                    return_value="周报与worklog均已生成并保存到桌面。",
                ),
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

    def test_task_continues_same_dialog_after_confirmation(self):
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
                "auto_continue": True,
                "max_continue_rounds": 3,
                "history_initial_wait_seconds": 11,
                "history_max_retries": 7,
                "history_retry_delay_seconds": 6,
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
            first_log = runner.RemoteLog("100", "first.jsonl", "/logs/first.jsonl", 1, 1)
            final_log = runner.RemoteLog("100", "final.jsonl", "/logs/final.jsonl", 1, 1)

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
                patch.object(runner, "list_remote_logs", side_effect=[[], []]),
                patch.object(runner, "start_prompt") as start,
                patch.object(
                    runner,
                    "wait_for_new_stop",
                    side_effect=[first_log, final_log],
                ),
                patch.object(runner.time, "sleep"),
                patch.object(runner, "pull_log", side_effect=fake_pull_log),
                patch.object(
                    runner,
                    "extract_stop_content",
                    side_effect=[
                        "我已读取资料，是否继续生成周报？",
                        "周报与worklog均已生成并保存到桌面。",
                    ],
                ),
                patch.object(
                    runner,
                    "extract_logged_output_paths",
                    side_effect=[[], [logged_path]],
                ),
                patch.object(
                    runner,
                    "get_latest_dialog_page_id",
                    return_value="dialog-21",
                ) as get_dialog,
                patch.object(runner, "resolve_logged_remote_files", return_value=[report]),
                patch.object(runner, "pull_remote_files", side_effect=fake_pull_remote_files),
                patch.object(runner, "force_stop") as stopped,
            ):
                succeeded = runner.run_weekly_task(
                    task, config, target=None, verbose=False, dry_run=False, rerun=False
                )

            self.assertTrue(succeeded)
            self.assertEqual(2, start.call_count)
            self.assertIsNone(start.call_args_list[0].kwargs["history_session_id"])
            self.assertEqual(
                "dialog-21", start.call_args_list[1].kwargs["history_session_id"]
            )
            get_dialog.assert_called_once_with(
                target=None,
                wait_seconds=11.0,
                max_retries=7,
                retry_delay=6.0,
                verbose=False,
            )
            stopped.assert_called_once()
            task_dir = root / "xiaoyi_logs" / "task21"
            self.assertTrue((task_dir / "task21.continue1.txt").is_file())
            meta = json.loads((task_dir / "task21.meta.json").read_text(encoding="utf-8"))
            self.assertEqual("dialog-21", meta["dialog_page_id"])
            self.assertEqual("complete", meta["dialog_verdict"])
            marker = json.loads((task_dir / "completed.json").read_text(encoding="utf-8"))
            self.assertEqual(2, marker["result"]["pushes"])
            self.assertEqual(1, len(marker["result"]["continue_queries"]))

    def test_missing_history_is_warning_when_report_and_worklog_are_pulled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            metadata_path = root / "task" / "测试人员" / "22" / "metadata.json"
            metadata_path.parent.mkdir(parents=True)
            metadata = {
                "person": "测试人员",
                "absolute_id": "22",
                "task": "生成七月份第一周的工作周报",
                "rubrics": ["输出格式要求：HTML 文档（.html）"],
            }
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
            )
            task = runner.WeeklyTask("测试人员", "22", metadata_path, metadata)
            config = {
                "output_root": root / "xiaoyi_logs",
                "prompt_suffix": "生成的worklog和周报放到桌面上",
                "remote_output_roots": {"Desktop": "/desktop"},
                "xiaoyi_timeout": 10,
                "poll_seconds": 0.01,
                "require_worklog": True,
                "auto_continue": True,
                "max_continue_rounds": 3,
            }
            old_worklog = runner.RemoteFile(
                "/desktop/task22-worklog/events.jsonl", 10, 1, "Desktop", "/desktop"
            )
            new_worklog = runner.RemoteFile(
                "/desktop/task22-worklog/events.jsonl", 20, 2, "Desktop", "/desktop"
            )
            report = runner.RemoteFile(
                "/desktop/七月第一周周报.docx", 30, 2, "Desktop", "/desktop"
            )
            done_log = runner.RemoteLog("100", "task.jsonl", "/logs/task.jsonl", 1, 1)

            def fake_pull_log(*args, **kwargs):
                path = root / "xiaoyi_logs" / "task22" / "task22.jsonl"
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
                patch.object(
                    runner,
                    "extract_stop_content",
                    return_value="我已读取资料，是否继续生成周报？",
                ),
                patch.object(runner, "extract_logged_output_paths", return_value=[logged_path]),
                patch.object(runner, "get_latest_dialog_page_id", return_value=""),
                patch.object(runner, "resolve_logged_remote_files", return_value=[report]),
                patch.object(runner, "pull_remote_files", side_effect=fake_pull_remote_files),
                patch.object(runner, "force_stop"),
            ):
                succeeded = runner.run_weekly_task(
                    task, config, target=None, verbose=False, dry_run=False, rerun=False
                )

            self.assertTrue(succeeded)
            task_dir = root / "xiaoyi_logs" / "task22"
            self.assertTrue((task_dir / "completed.json").is_file())
            self.assertFalse((task_dir / "failed.json").exists())
            marker = json.loads((task_dir / "completed.json").read_text(encoding="utf-8"))
            self.assertEqual("needs-confirmation", marker["result"]["dialog_verdict"])
            self.assertIn("history_list.json", marker["result"]["warnings"][0])
            self.assertNotIn("required_formats", marker["result"])
            self.assertEqual(["docx", "jsonl"], marker["result"]["present_formats"])
            meta = json.loads((task_dir / "task22.meta.json").read_text(encoding="utf-8"))
            self.assertEqual(marker["result"]["warnings"], meta["runner_warnings"])

    def test_stop_content_classification(self):
        self.assertEqual(
            "needs-confirmation",
            runner.classify_stop_content("资料已整理，请确认是否继续生成周报。"),
        )
        self.assertEqual(
            "partial-or-failed",
            runner.classify_stop_content("周报生成失败，需要稍后重试。"),
        )
        self.assertEqual(
            "needs-confirmation",
            runner.classify_stop_content("需要您授权访问备忘录后才能继续。"),
        )
        self.assertEqual(
            "complete",
            runner.classify_stop_content("周报已生成。还有其他需要帮忙的吗？"),
        )
        self.assertEqual(
            "complete",
            runner.classify_stop_content(
                "周报和worklog已经生成并保存到桌面，是否还需要我继续处理？"
            ),
        )
        self.assertEqual(
            "needs-confirmation",
            runner.classify_stop_content("周报已生成，是否继续生成worklog？"),
        )
        self.assertEqual("missing-content", runner.classify_stop_content(""))

    def test_history_parser_tolerates_extra_closing_bracket(self):
        parsed = dialog_history.parse_history_json(
            '[{"dialogPageId":"dialog-21"}]]'
        )
        self.assertEqual("dialog-21", parsed[0]["dialogPageId"])

    def test_history_fetch_waits_longer_and_retries_until_ready(self):
        with (
            patch.object(
                dialog_history,
                "remote_shell",
                side_effect=[
                    "history ability started",
                    "",
                    "[]",
                    '[{"dialogPageId":"dialog-21"}]',
                ],
            ) as remote,
            patch.object(dialog_history.time, "sleep") as sleep,
        ):
            history = dialog_history.fetch_history_list(
                target=None,
                wait_seconds=8,
                max_retries=3,
                retry_delay=5,
                verbose=False,
            )

        self.assertEqual("dialog-21", history[0]["dialogPageId"])
        self.assertEqual(4, remote.call_count)
        self.assertEqual([8, 5, 5], [call.args[0] for call in sleep.call_args_list])

    def test_handoff_declares_exact_judge_input_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            metadata_path = root / "task" / "测试人员" / "21" / "metadata.json"
            metadata_path.parent.mkdir(parents=True)
            metadata = {
                "adapter": "weekly-report",
                "person": "测试人员",
                "absolute_id": "21",
                "task": "生成七月份第一周的工作周报",
                "rubrics": ["时间范围为七月份第一周"],
            }
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
            )
            data_dir = metadata_path.parent.parent / "data"
            data_dir.mkdir()
            output_root = root / "xiaoyi_logs"
            task_dir = output_root / "task21"
            (task_dir / "outputs").mkdir(parents=True)
            (task_dir / "task21.jsonl").write_text("{}\n", encoding="utf-8")
            (task_dir / "completed.json").write_text("{}", encoding="utf-8")
            task = runner.WeeklyTask("测试人员", "21", metadata_path, metadata)
            config = {
                "metadata_root": root / "task",
                "deliverables_root": root / "deliverables_final",
                "output_root": output_root,
            }

            handoff_path = runner.write_weekly_runner_handoff(
                [task], config, run_date="20260817", runner_finished=True
            )
            entry = json.loads(handoff_path.read_text(encoding="utf-8"))["tasks"][0]

            self.assertEqual(
                {
                    "metadata": str(metadata_path.resolve()),
                    "data": str(data_dir.resolve()),
                    "outputs": str((task_dir / "outputs").resolve()),
                    "runnerTaskDir": str(task_dir.resolve()),
                },
                entry["judgeInputs"],
            )
            self.assertTrue(
                json.loads(handoff_path.read_text(encoding="utf-8"))["runnerFinished"]
            )

    def test_handoff_barrier_stays_closed_when_fetched_data_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            metadata_path = root / "task" / "测试人员" / "21" / "metadata.json"
            metadata_path.parent.mkdir(parents=True)
            metadata_path.write_text("{}", encoding="utf-8")
            output_root = root / "xiaoyi_logs"
            task_dir = output_root / "task21"
            (task_dir / "outputs").mkdir(parents=True)
            (task_dir / "task21.jsonl").write_text("{}\n", encoding="utf-8")
            (task_dir / "completed.json").write_text("{}", encoding="utf-8")
            task = runner.WeeklyTask("测试人员", "21", metadata_path, {})
            config = {
                "metadata_root": root / "task",
                "deliverables_root": root / "deliverables_final",
                "output_root": output_root,
            }

            handoff_path = runner.write_weekly_runner_handoff(
                [task], config, run_date="20260817", runner_finished=True
            )

            self.assertFalse(
                json.loads(handoff_path.read_text(encoding="utf-8"))["runnerFinished"]
            )


if __name__ == "__main__":
    unittest.main()
