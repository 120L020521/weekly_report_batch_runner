from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from judge_file_organization import JudgeInputError, judge_file_organization


class JudgeFileOrganizationTests(unittest.TestCase):
    def _fixture(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        outputs = root / "outputs"
        for name in ("Desktop", "Download", "Documents"):
            (outputs / name).mkdir(parents=True)
        target = outputs / "Desktop" / "move_file" / "ceshi.txt"
        target.parent.mkdir()
        target.write_bytes(b"fixture")
        digest = hashlib.md5(b"fixture").hexdigest()
        metadata = {
            "absolute_id": "FileOrganization_0_002",
            "task": "move fixture",
            "rubrics": [
                "Desktop 的直接子项是否恰好为 1 个，且完整名称集合为 move_file？",
                "Desktop\\move_file 是否存在且类型为目录，其直接子项是否恰好为 1 个，且完整名称集合为 ceshi.txt？",
                f"以下文件是否存在且类型为文件，且 MD5 分别正确：Desktop\\move_file\\ceshi.txt（{digest}）？",
            ],
        }
        metadata_path = root / "metadata.json"
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        return temp, metadata_path, outputs

    def test_all_rubrics_pass_for_clean_tree(self) -> None:
        temp, metadata, outputs = self._fixture()
        self.addCleanup(temp.cleanup)
        result = judge_file_organization(metadata, outputs)
        self.assertTrue(result["passed"])
        self.assertEqual(result["score"], 1.0)
        self.assertEqual(result["summary"], {"total": 3, "passed": 3, "failed": 0})

    def test_extra_nested_directory_fails_exact_child_rubric(self) -> None:
        temp, metadata, outputs = self._fixture()
        self.addCleanup(temp.cleanup)
        nested = outputs / "Desktop" / "move_file" / "move_file"
        nested.mkdir()
        (nested / "ceshi.txt").write_bytes(b"fixture")
        result = judge_file_organization(metadata, outputs)
        self.assertFalse(result["passed"])
        self.assertEqual(result["summary"], {"total": 3, "passed": 2, "failed": 1})
        self.assertFalse(result["rubrics"][1]["passed"])

    def test_md5_rubric_accepts_optional_uniform_character(self) -> None:
        temp, metadata, outputs = self._fixture()
        self.addCleanup(temp.cleanup)
        value = json.loads(metadata.read_text(encoding="utf-8"))
        value["rubrics"][2] = value["rubrics"][2].replace(
            "MD5 分别正确", "MD5 均分别正确"
        )
        metadata.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        result = judge_file_organization(metadata, outputs)
        self.assertTrue(result["rubrics"][2]["passed"])
        self.assertTrue(result["passed"])

    def test_missing_output_root_is_error(self) -> None:
        temp, metadata, outputs = self._fixture()
        self.addCleanup(temp.cleanup)
        (outputs / "Documents").rmdir()
        with self.assertRaisesRegex(JudgeInputError, "missing roots: Documents"):
            judge_file_organization(metadata, outputs)

    def test_unsupported_rubric_is_failed_and_other_rubrics_continue(self) -> None:
        unsupported = "任务看起来是否完成？"
        for position in (0, 1, 3):
            with self.subTest(position=position):
                temp, metadata, outputs = self._fixture()
                try:
                    value = json.loads(metadata.read_text(encoding="utf-8"))
                    value["rubrics"].insert(position, unsupported)
                    metadata.write_text(
                        json.dumps(value, ensure_ascii=False), encoding="utf-8"
                    )
                    result = judge_file_organization(metadata, outputs)
                    self.assertEqual(result["status"], "success")
                    self.assertEqual(
                        result["summary"], {"total": 4, "passed": 3, "failed": 1}
                    )
                    self.assertFalse(result["passed"])
                    self.assertEqual(result["score"], 0.75)
                    self.assertFalse(result["rubrics"][position]["passed"])
                    self.assertIn(
                        "unsupported or invalid rubric",
                        result["rubrics"][position]["evidence"],
                    )
                    self.assertTrue(
                        all(
                            item["passed"]
                            for index, item in enumerate(result["rubrics"])
                            if index != position
                        )
                    )
                finally:
                    temp.cleanup()

    def test_manifest_can_block_incomplete_snapshot(self) -> None:
        temp, metadata, outputs = self._fixture()
        self.addCleanup(temp.cleanup)
        (outputs / "outputs_manifest.json").write_text(
            json.dumps({"snapshot_complete": False}), encoding="utf-8"
        )
        with self.assertRaisesRegex(JudgeInputError, "reports failure"):
            judge_file_organization(metadata, outputs)

    def test_cli_writes_standard_halo_context(self) -> None:
        temp, metadata, outputs = self._fixture()
        self.addCleanup(temp.cleanup)
        result_path = Path(temp.name) / "judge" / "FileOrganization_0_002" / "judge_result.json"
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(Path(__file__).with_name("judge_file_organization.py")),
                "--metadata",
                str(metadata),
                "--outputs",
                str(outputs),
                "--result",
                str(result_path),
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            env=environment,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        prepared_dir = result_path.parent
        frozen_metadata = json.loads(
            (prepared_dir / "metadata.json").read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (prepared_dir / "case_manifest.json").read_text(encoding="utf-8")
        )
        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual("FileOrganization_0_002", frozen_metadata["absolute_id"])
        self.assertEqual("file-organization", manifest["adapter"])
        self.assertEqual(result["inputFingerprint"], manifest["inputFingerprint"])

    def test_prepared_cli_copies_manifest_fingerprint_without_running_prepare(self) -> None:
        temp, metadata, outputs = self._fixture()
        self.addCleanup(temp.cleanup)
        prepared_dir = Path(temp.name) / "prepared"
        prepared_dir.mkdir()
        result_path = prepared_dir / "judge_result.json"
        manifest_path = prepared_dir / "case_manifest.json"
        fingerprint = {"algorithm": "sha256", "value": "prepared", "fileCount": 4}
        manifest_value = {
            "version": 1,
            "adapter": "file-organization",
            "taskId": "FileOrganization_0_002",
            "inputFingerprint": fingerprint,
        }
        manifest_path.write_text(json.dumps(manifest_value), encoding="utf-8")
        before = manifest_path.read_bytes()
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(Path(__file__).with_name("judge_file_organization.py")),
                "--metadata", str(metadata),
                "--outputs", str(outputs),
                "--case-manifest", str(manifest_path),
                "--result", str(result_path),
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            env=environment,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(before, manifest_path.read_bytes())
        self.assertFalse((prepared_dir / "metadata.json").exists())
        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(fingerprint, result["inputFingerprint"])


if __name__ == "__main__":
    unittest.main()
