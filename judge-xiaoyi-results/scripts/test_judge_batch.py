from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "judge_batch.py"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class UnifiedJudgeBatchTests(unittest.TestCase):
    def run_cli(self, *args: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, args)],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_three_adapters_share_prepare_result_and_summary_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            judge_root = root / "judge"
            specs = [
                ("FileOrganization_0_001", "file-organization"),
                ("112", "workspacebench"),
                ("21", "weekly-report"),
            ]
            tasks = []
            for task_id, adapter in specs:
                source = root / "source" / task_id
                metadata = {
                    "absolute_id": task_id,
                    "task": f"task {task_id}",
                    "rubrics": ["artifact is correct"],
                }
                if adapter != "file-organization":
                    metadata["adapter"] = adapter
                metadata_path = source / "metadata.json"
                write_json(metadata_path, metadata)
                outputs = source / "outputs"
                outputs.mkdir(parents=True)
                if adapter == "file-organization":
                    for name in ("Desktop", "Download", "Documents"):
                        (outputs / name).mkdir()
                (outputs / "result.txt").write_text("ok", encoding="utf-8")
                data = source / "data"
                data.mkdir()
                (data / "source.txt").write_text("source", encoding="utf-8")
                runner = source / "runner"
                runner.mkdir()
                trace = runner / f"task{task_id}.jsonl"
                trace.write_text('{"trace_id":"t","span_id":"s"}\n', encoding="utf-8")
                tasks.append({
                    "task_id": task_id,
                    "adapter": adapter,
                    "runner_status": "completed",
                    "metadata": str(metadata_path.resolve()),
                    "data": str(data.resolve()),
                    "outputs": str(outputs.resolve()),
                    "runner_dir": str(runner.resolve()),
                    "trace": str(trace.resolve()),
                    "judge_dir": str((judge_root / f"task-{task_id}").resolve()),
                })
            batch = {
                "schema_version": 1,
                "producer": "run-xiaoyi",
                "runner_finished": True,
                "run_id": "test-run",
                "judge_root": str(judge_root.resolve()),
                "tasks": tasks,
            }
            batch_path = root / "judge_batch.json"
            write_json(batch_path, batch)
            prepared = self.run_cli("prepare", "--batch", batch_path)
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            queue = json.loads((judge_root / "judge_queue.json").read_text(encoding="utf-8"))
            self.assertEqual([row["status"] for row in queue["tasks"]], ["ready"] * 3)
            self.assertEqual(str(judge_root.resolve()), queue["judgeRoot"])
            self.assertTrue(all(row["metadata"] and row["trace"] for row in queue["tasks"]))

            judge_types = {
                "file-organization": "deterministic-file-organization",
                "workspacebench": "codex-subagent",
                "weekly-report": "codex-subagent",
            }
            for row in queue["tasks"]:
                prepared_dir = Path(row["preparedDir"])
                manifest = json.loads((prepared_dir / "case_manifest.json").read_text(encoding="utf-8"))
                metadata = json.loads((prepared_dir / "metadata.json").read_text(encoding="utf-8"))
                self.assertTrue((prepared_dir / "output").is_dir())
                self.assertTrue((prepared_dir / "data").is_dir())
                self.assertTrue((prepared_dir / "runner").is_dir())
                result = {
                    "version": 1,
                    "taskId": row["taskId"],
                    "status": "success",
                    "judgeType": judge_types[row["adapter"]],
                    "inputFingerprint": manifest["inputFingerprint"],
                    "rubrics": [{
                        "index": 0,
                        "rubric": metadata["rubrics"][0],
                        "passed": True,
                        "confidence": 1.0,
                        "evidence": "verified",
                    }],
                    "summary": {"total": 1, "passed": 1, "failed": 0},
                    "passed": True,
                    "score": 1.0,
                    "feedback": "1/1 rubrics passed.",
                }
                write_json(prepared_dir / "judge_result.json", result)
                validated = self.run_cli("validate-result", "--prepared-dir", prepared_dir)
                self.assertEqual(validated.returncode, 0, validated.stderr)

            summarized = self.run_cli("summarize", "--judge-root", judge_root)
            self.assertEqual(summarized.returncode, 0, summarized.stderr)
            summary = json.loads((judge_root / "batch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual([row["adapter"] for row in summary["tasks"]], [item[1] for item in specs])
            self.assertTrue(all(row["passed"] for row in summary["tasks"]))

    def test_prepare_requires_finished_runner_barrier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch_path = root / "judge_batch.json"
            write_json(batch_path, {
                "schema_version": 1,
                "producer": "run-xiaoyi",
                "runner_finished": False,
                "judge_root": str((root / "judge").resolve()),
                "tasks": [{}],
            })
            result = self.run_cli("prepare", "--batch", batch_path)
            self.assertEqual(result.returncode, 2)
            self.assertIn("runner_finished", result.stderr)

    def test_task_centric_layout_keeps_batch_indexes_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch_root = root / "_xiaoyi_batches" / "run_20260818"
            task_root = root / "task112"
            source = root / "source" / "112"
            metadata_path = source / "metadata.json"
            write_json(metadata_path, {
                "absolute_id": "112",
                "adapter": "workspacebench",
                "task": "prepare one artifact",
                "rubrics": ["artifact is correct"],
            })
            outputs = source / "outputs"
            outputs.mkdir(parents=True)
            (outputs / "result.txt").write_text("ok", encoding="utf-8")
            trace = source / "task112.jsonl"
            trace.write_text('{"trace_id":"t","span_id":"s"}\n', encoding="utf-8")
            batch_path = batch_root / "judge_batch.json"
            write_json(batch_path, {
                "schema_version": 1,
                "producer": "run-xiaoyi",
                "runner_finished": True,
                "run_id": "20260818",
                "artifact_root": str(root.resolve()),
                "judge_root": str(batch_root.resolve()),
                "tasks": [{
                    "task_id": "112",
                    "adapter": "workspacebench",
                    "runner_status": "completed",
                    "execution_outcome": "completed",
                    "evidence_ready": True,
                    "metadata": str(metadata_path.resolve()),
                    "data": None,
                    "outputs": str(outputs.resolve()),
                    "runner_dir": str(source.resolve()),
                    "trace": str(trace.resolve()),
                    "task_root": str(task_root.resolve()),
                    "judge_dir": str((task_root / "xiaoyi_judge").resolve()),
                    "halo_dir": str((task_root / "xiaoyi_halo").resolve()),
                }],
            })

            prepared = self.run_cli("prepare", "--batch", batch_path)
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            queue_path = batch_root / "judge_queue.json"
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            row = queue["tasks"][0]
            self.assertEqual(queue["artifactRoot"], str(root.resolve()))
            self.assertEqual(row["taskRoot"], str(task_root.resolve()))
            self.assertEqual(row["preparedDir"], str((task_root / "xiaoyi_judge").resolve()))
            self.assertEqual(row["haloDir"], str((task_root / "xiaoyi_halo").resolve()))
            self.assertTrue((task_root / "xiaoyi_judge" / "case_manifest.json").is_file())
            self.assertFalse((batch_root / "task112").exists())

    def test_file_organization_judges_ready_snapshot_despite_failed_runner_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            judge_root = root / "judge"
            case_id = "FileOrganization_0_002"
            source = root / "source" / case_id
            metadata_path = source / "metadata.json"
            write_json(metadata_path, {
                "absolute_id": case_id,
                "task": "organize files",
                "rubrics": ["expected directory structure"],
            })
            outputs = source / "outputs"
            for name in ("Desktop", "Download", "Documents"):
                (outputs / name).mkdir(parents=True)
            trace = source / f"{case_id}.jsonl"
            trace.write_text('{"trace_id":"t","span_id":"s"}\n', encoding="utf-8")
            batch_path = root / "judge_batch.json"
            write_json(batch_path, {
                "schema_version": 1,
                "producer": "run-xiaoyi",
                "runner_finished": True,
                "run_id": "test-file-run",
                "judge_root": str(judge_root.resolve()),
                "tasks": [
                    {
                        "task_id": case_id,
                        "adapter": "file-organization",
                        "runner_status": "failed",
                        "execution_outcome": "incomplete-after-3-continues",
                        "evidence_ready": True,
                        "metadata": str(metadata_path.resolve()),
                        "data": None,
                        "outputs": str(outputs.resolve()),
                        "runner_dir": str(source.resolve()),
                        "trace": str(trace.resolve()),
                        "judge_dir": str((judge_root / case_id).resolve()),
                    },
                    {
                        "task_id": "FileOrganization_0_003",
                        "adapter": "file-organization",
                        "runner_status": "failed",
                        "execution_outcome": "execution-error",
                        "evidence_ready": False,
                        "metadata": None,
                        "data": None,
                        "outputs": None,
                        "runner_dir": None,
                        "trace": None,
                        "judge_dir": str((judge_root / "FileOrganization_0_003").resolve()),
                    },
                ],
            })

            prepared = self.run_cli("prepare", "--batch", batch_path)
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            queue = json.loads((judge_root / "judge_queue.json").read_text(encoding="utf-8"))
            ready, unavailable = queue["tasks"]
            self.assertEqual(ready["status"], "ready")
            self.assertEqual(ready["runnerStatus"], "failed")
            self.assertEqual(ready["executionOutcome"], "incomplete-after-3-continues")
            self.assertIs(ready["evidenceReady"], True)
            self.assertEqual(ready["trace"], str(trace.resolve()))
            manifest = json.loads(
                (Path(ready["preparedDir"]) / "case_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["runnerStatus"], "failed")
            self.assertEqual(manifest["executionOutcome"], "incomplete-after-3-continues")
            self.assertIs(manifest["evidenceReady"], True)

            self.assertEqual(unavailable["status"], "runner-failure")
            self.assertEqual(unavailable["executionOutcome"], "execution-error")
            self.assertIs(unavailable["evidenceReady"], False)
            self.assertIn("evidence is not ready", unavailable["error"])


if __name__ == "__main__":
    unittest.main()
