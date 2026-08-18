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
    def make_task(self, root: Path, task_id: str = "21") -> runner.WeeklyTask:
        metadata_path = root / "task" / "测试人员" / task_id / "metadata.json"
        metadata_path.parent.mkdir(parents=True)
        metadata = {
            "adapter": "weekly-report",
            "person": "测试人员",
            "absolute_id": task_id,
            "task": "生成七月份第一周的工作周报",
            "rubrics": ["输出格式要求：Word 文档（.docx）"],
        }
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
        )
        return runner.WeeklyTask("测试人员", task_id, metadata_path, metadata)

    def config(self, root: Path) -> dict:
        return {
            "output_root": root / "xiaoyi_logs",
            "prompt_suffix": "",
            "xiaoyi_timeout": 10,
            "poll_seconds": 0.01,
            "require_worklog": True,
            "auto_continue": True,
            "max_continue_rounds": 3,
            "history_initial_wait_seconds": 11,
            "history_max_retries": 7,
            "history_retry_delay_seconds": 6,
        }

    def workspace_files(self, dialog_id: str = "dialog-21"):
        root = f"{runner._XIAOYI_WORKSPACE_ROOT}/{dialog_id}"
        report = runner.RemoteFile(
            f"{root}/七月第一周周报.docx", 30, 2, "XiaoYiWorkspace", root
        )
        worklog = runner.RemoteFile(
            f"{root}/{runner._WEEKLY_WORKLOG_RELATIVE_ROOT}/events.jsonl",
            20,
            2,
            "XiaoYiWorkspace",
            root,
        )
        return report, worklog

    def fake_pull_log(self, root: Path, task_id: str = "21"):
        def pull(*args, **kwargs):
            path = root / "xiaoyi_logs" / f"task{task_id}" / f"task{task_id}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"event":"session_ended"}\n', encoding="utf-8")
            return path

        return pull

    @staticmethod
    def fake_pull_remote_files(files, *, local_root, target, verbose=False):
        records = []
        for remote_file in files:
            local_path = local_root / runner._safe_relative(remote_file)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text("test", encoding="utf-8")
            records.append({
                "remote_path": remote_file.path,
                "local_path": str(local_path),
                "size": remote_file.size,
                "mtime": remote_file.mtime,
                "status": "pulled",
            })
        return records

    def test_default_prompt_uses_original_task_text(self):
        task_text = "生成七月份第一周的工作周报"
        self.assertEqual(task_text, runner._build_execution_prompt(task_text, ""))
        self.assertEqual(task_text, runner._build_execution_prompt(task_text, None))

    def test_dialog_workspace_listing_uses_only_fixed_paths(self):
        dialog_root = f"{runner._XIAOYI_WORKSPACE_ROOT}/dialog-21"
        worklog_root = f"{dialog_root}/{runner._WEEKLY_WORKLOG_RELATIVE_ROOT}"
        response = (
            f"2|30|{dialog_root}/七月第一周周报.docx\n"
            "__WORKLOG__\n"
            f"3|20|{worklog_root}/events.jsonl\n"
            "__END__\n"
        )
        with patch.object(runner, "remote_shell", return_value=response) as remote:
            reports, worklogs = runner.list_dialog_workspace_artifacts(
                "dialog-21", target=None, verbose=False
            )

        self.assertEqual([f"{dialog_root}/七月第一周周报.docx"], [item.path for item in reports])
        self.assertEqual([f"{worklog_root}/events.jsonl"], [item.path for item in worklogs])
        command = remote.call_args.args[0]
        self.assertIn(f"find '{dialog_root}' -mindepth 1 -maxdepth 1 -type f", command)
        self.assertIn(f"find '{worklog_root}' -type f", command)
        self.assertNotIn("Desktop", command)
        self.assertNotIn("Documents", command)

    def test_dialog_workspace_rejects_unsafe_id(self):
        with self.assertRaises(ValueError):
            runner.list_dialog_workspace_artifacts("../other", target=None, verbose=False)

    def test_task_pulls_workspace_report_worklog_and_original_trace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task = self.make_task(root)
            report, worklog = self.workspace_files()
            done_log = runner.RemoteLog("100", "task.jsonl", "/logs/task.jsonl", 1, 1)
            with (
                patch.object(runner, "list_remote_logs", return_value=[]),
                patch.object(runner, "start_prompt"),
                patch.object(runner, "wait_for_new_stop", return_value=done_log),
                patch.object(runner.time, "sleep"),
                patch.object(runner, "pull_log", side_effect=self.fake_pull_log(root)),
                patch.object(
                    runner, "extract_stop_content",
                    return_value="周报与worklog均已生成并保存。",
                ),
                patch.object(runner, "get_latest_dialog_page_id", return_value="dialog-21") as history,
                patch.object(
                    runner, "list_dialog_workspace_artifacts",
                    return_value=([report], [worklog]),
                ),
                patch.object(
                    runner, "pull_remote_files", side_effect=self.fake_pull_remote_files
                ),
                patch.object(runner, "force_stop"),
            ):
                succeeded = runner.run_weekly_task(
                    task, self.config(root), target=None, verbose=False,
                    dry_run=False, rerun=False
                )

            self.assertTrue(succeeded)
            history.assert_called_once()
            task_dir = root / "xiaoyi_logs" / "task21"
            self.assertTrue((task_dir / "task21.jsonl").is_file())
            self.assertTrue(
                (task_dir / "outputs" / "XiaoYiWorkspace" / "七月第一周周报.docx").is_file()
            )
            self.assertTrue(
                (
                    task_dir / "outputs" / "XiaoYiWorkspace" / "memory"
                    / "weekly-report-skill" / "worklog" / "events.jsonl"
                ).is_file()
            )
            manifest = json.loads(
                (task_dir / "artifacts_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual("dialog-21", manifest["dialog_id"])
            self.assertEqual("dialog-workspace-root", manifest["outputs"][0]["selection_source"])
            self.assertEqual("dialog-weekly-worklog", manifest["worklogs"][0]["selection_source"])

    def test_task_continues_same_dialog_after_confirmation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task = self.make_task(root)
            report, worklog = self.workspace_files()
            first_log = runner.RemoteLog("100", "first.jsonl", "/logs/first.jsonl", 1, 1)
            final_log = runner.RemoteLog("100", "final.jsonl", "/logs/final.jsonl", 1, 1)
            with (
                patch.object(runner, "list_remote_logs", side_effect=[[], []]),
                patch.object(runner, "start_prompt") as start,
                patch.object(
                    runner, "wait_for_new_stop", side_effect=[first_log, final_log]
                ),
                patch.object(runner.time, "sleep"),
                patch.object(runner, "pull_log", side_effect=self.fake_pull_log(root)),
                patch.object(
                    runner, "extract_stop_content",
                    side_effect=[
                        "我已读取资料，是否继续生成周报？",
                        "周报与worklog均已生成并保存。",
                    ],
                ),
                patch.object(runner, "get_latest_dialog_page_id", return_value="dialog-21") as history,
                patch.object(
                    runner, "list_dialog_workspace_artifacts",
                    return_value=([report], [worklog]),
                ),
                patch.object(
                    runner, "pull_remote_files", side_effect=self.fake_pull_remote_files
                ),
                patch.object(runner, "force_stop") as stopped,
            ):
                succeeded = runner.run_weekly_task(
                    task, self.config(root), target=None, verbose=False,
                    dry_run=False, rerun=False
                )

            self.assertTrue(succeeded)
            self.assertEqual(2, start.call_count)
            self.assertIsNone(start.call_args_list[0].kwargs["history_session_id"])
            self.assertEqual("dialog-21", start.call_args_list[1].kwargs["history_session_id"])
            history.assert_called_once_with(
                target=None, wait_seconds=11.0, max_retries=7,
                retry_delay=6.0, verbose=False
            )
            stopped.assert_called_once()
            marker = json.loads(
                (root / "xiaoyi_logs" / "task21" / "completed.json").read_text(encoding="utf-8")
            )
            self.assertEqual("dialog-21", marker["result"]["dialog_id"])
            self.assertEqual(2, marker["result"]["pushes"])

    def test_missing_dialog_id_fails_workspace_collection_but_keeps_trace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task = self.make_task(root, "22")
            done_log = runner.RemoteLog("100", "task.jsonl", "/logs/task.jsonl", 1, 1)
            with (
                patch.object(runner, "list_remote_logs", return_value=[]),
                patch.object(runner, "start_prompt"),
                patch.object(runner, "wait_for_new_stop", return_value=done_log),
                patch.object(runner.time, "sleep"),
                patch.object(runner, "pull_log", side_effect=self.fake_pull_log(root, "22")),
                patch.object(runner, "extract_stop_content", return_value="周报已经生成完成。"),
                patch.object(runner, "get_latest_dialog_page_id", return_value=""),
                patch.object(runner, "list_dialog_workspace_artifacts") as listing,
                patch.object(runner, "force_stop"),
            ):
                succeeded = runner.run_weekly_task(
                    task, self.config(root), target=None, verbose=False,
                    dry_run=False, rerun=False
                )

            self.assertFalse(succeeded)
            listing.assert_not_called()
            task_dir = root / "xiaoyi_logs" / "task22"
            self.assertTrue((task_dir / "task22.jsonl").is_file())
            self.assertTrue((task_dir / "failed.json").is_file())

    def test_stop_content_classification(self):
        self.assertEqual(
            "needs-confirmation",
            runner.classify_stop_content("我已读取资料，请确认是否继续生成周报？"),
        )
        self.assertEqual(
            "partial-or-failed", runner.classify_stop_content("周报生成失败，请提供文件")
        )
        self.assertEqual(
            "complete",
            runner.classify_stop_content("周报和worklog已经生成，是否还需要我继续处理？"),
        )
        self.assertEqual("missing-content", runner.classify_stop_content(""))

    def test_history_parser_tolerates_extra_closing_bracket(self):
        parsed = dialog_history.parse_history_json('[{"dialogPageId":"dialog-21"}]]')
        self.assertEqual("dialog-21", parsed[0]["dialogPageId"])

    def test_history_fetch_waits_and_retries_until_ready(self):
        with (
            patch.object(
                dialog_history,
                "remote_shell",
                side_effect=[
                    "history ability started", "", "[]", '[{"dialogPageId":"dialog-21"}]'
                ],
            ) as remote,
            patch.object(dialog_history.time, "sleep") as sleep,
        ):
            history = dialog_history.fetch_history_list(
                target=None, wait_seconds=8, max_retries=3,
                retry_delay=5, verbose=False
            )
        self.assertEqual("dialog-21", history[0]["dialogPageId"])
        self.assertEqual(4, remote.call_count)
        self.assertEqual([8, 5, 5], [call.args[0] for call in sleep.call_args_list])

    def test_handoff_declares_no_copied_person_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task = self.make_task(root)
            output_root = root / "xiaoyi_logs"
            task_dir = output_root / "task21"
            (task_dir / "outputs").mkdir(parents=True)
            (task_dir / "task21.jsonl").write_text("{}\n", encoding="utf-8")
            (task_dir / "completed.json").write_text("{}", encoding="utf-8")
            config = {
                "metadata_root": root / "task",
                "deliverables_root": root / "deliverables_final",
                "output_root": output_root,
            }
            handoff_path = runner.write_weekly_runner_handoff(
                [task], config, run_date="20260817", runner_finished=True
            )
            payload = json.loads(handoff_path.read_text(encoding="utf-8"))
            entry = payload["tasks"][0]
            self.assertEqual(
                {
                    "metadata": str(task.metadata_path.resolve()),
                    "data": None,
                    "outputs": str((task_dir / "outputs").resolve()),
                    "runnerTaskDir": str(task_dir.resolve()),
                },
                entry["judgeInputs"],
            )
            self.assertTrue(payload["runnerFinished"])
            self.assertFalse((task.metadata_path.parent.parent / "data").exists())

    def test_handoff_barrier_stays_closed_when_outputs_are_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task = self.make_task(root)
            output_root = root / "xiaoyi_logs"
            task_dir = output_root / "task21"
            task_dir.mkdir(parents=True)
            (task_dir / "task21.jsonl").write_text("{}\n", encoding="utf-8")
            (task_dir / "completed.json").write_text("{}", encoding="utf-8")
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

    def test_task_centric_handoff_uses_per_task_runner_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task = self.make_task(root)
            batch_root = root / "_xiaoyi_batches" / "run_20260818"
            task_artifacts_root = root
            runner_root = root / "task21" / "xiaoyi_file_runs"
            task_dir = runner_root / "task21"
            (task_dir / "outputs").mkdir(parents=True)
            (task_dir / "task21.jsonl").write_text("{}\n", encoding="utf-8")
            (task_dir / "completed.json").write_text("{}", encoding="utf-8")
            config = {
                "metadata_root": root / "task",
                "deliverables_root": root / "deliverables_final",
                "output_root": batch_root,
                "task_artifacts_root": task_artifacts_root,
            }

            handoff_path = runner.write_weekly_runner_handoff(
                [task], config, run_date="20260818", runner_finished=True
            )
            payload = json.loads(handoff_path.read_text(encoding="utf-8"))
            entry = payload["tasks"][0]
            self.assertEqual(handoff_path, batch_root / "weekly_runner_batch.json")
            self.assertEqual(entry["judgeInputs"]["runnerTaskDir"], str(task_dir.resolve()))
            self.assertEqual(entry["judgeInputs"]["outputs"], str((task_dir / "outputs").resolve()))
            self.assertEqual(payload["roots"]["taskArtifacts"], str(root.resolve()))
            self.assertTrue(payload["runnerFinished"])


if __name__ == "__main__":
    unittest.main()
