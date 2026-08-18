from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("render_batch_report.py")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class BatchReportTests(unittest.TestCase):
    def test_render_three_adapters_directly_from_judge_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            halo = root / "halo"
            tasks = []
            for task_id, adapter, passed in (
                ("FileOrganization_0_001", "file-organization", False),
                ("112", "workspacebench", True),
                ("21", "weekly-report", False),
            ):
                prepared = root / "judge" / task_id
                write_json(prepared / "metadata.json", {
                    "absolute_id": task_id, "task": f"task {task_id}", "rubrics": ["r"]
                })
                result = prepared / "judge_result.json"
                write_json(result, {
                    "taskId": task_id, "status": "success", "passed": passed,
                    "score": 1.0 if passed else 0.0,
                })
                trace = root / "logs" / task_id / "trace.jsonl"
                trace.parent.mkdir(parents=True)
                trace.write_text("{}\n", encoding="utf-8")
                artifact = halo / (f"task{task_id}_halo" if task_id.isdigit() else f"{task_id}_halo")
                write_json(artifact / "halo_report.json", {
                    "schema_version": 9,
                    "report_summary": {
                        "task_id": task_id, "task": f"task {task_id}", "trace_ids": ["trace"]
                    },
                    "diagnosis": {
                        "execution_classification": "SUCCEEDED_CLEANLY",
                        "primary_failure_mode": "未发现执行错误",
                        "error_findings": [],
                    },
                    "proposed_changes": [],
                })
                tasks.append({
                    "taskId": task_id,
                    "adapter": adapter,
                    "runnerStatus": "completed",
                    "executionOutcome": "complete" if adapter == "file-organization" else "completed",
                    "evidenceReady": True,
                    "metadata": str((prepared / "metadata.json").resolve()),
                    "trace": str(trace.resolve()),
                    "preparedDir": str(prepared.resolve()),
                    "result": str(result.resolve()),
                })
            queue = root / "judge" / "judge_queue.json"
            write_json(queue, {
                "version": 1,
                "producer": "judge-xiaoyi-results",
                "tasks": tasks,
            })
            environment = os.environ.copy()
            environment["PYTHONIOENCODING"] = "utf-8"
            completed = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), "--queue", str(queue),
                 "--output-root", str(halo)],
                text=True, encoding="utf-8", capture_output=True, env=environment,
                check=False, timeout=30,
            )
            self.assertEqual(0, completed.returncode, completed.stderr or completed.stdout)
            output = json.loads(completed.stdout)
            html = Path(output["html_report"])
            self.assertTrue(html.is_file())
            document = html.read_text(encoding="utf-8")
            self.assertIn('"task_id":"21"', document)
            self.assertIn('"adapter":"file-organization"', document)
            self.assertIn('"execution_outcome":"complete"', document)
            self.assertIn('"evidence_ready":true', document)
            self.assertNotIn('"handoff":', document)


if __name__ == "__main__":
    unittest.main()
