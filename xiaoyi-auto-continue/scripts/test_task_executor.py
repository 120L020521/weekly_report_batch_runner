import json
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from scripts import task_executor


class PullOutputsTests(unittest.TestCase):
    def test_pull_outputs_excludes_hidden_paths_at_every_depth(self):
        roots = task_executor._ACCESSIBLE_OUTPUT_ROOTS

        def fake_remote_shell(command, **_kwargs):
            root = next(root for root in roots if root in command)
            if "-type d" in command:
                return "\n".join(
                    [
                        f"{root}/visible-dir",
                        f"{root}/visible-empty-dir",
                        f"{root}/.hidden-dir",
                        f"{root}/visible-dir/.nested-hidden-dir",
                    ]
                )
            if "-type f" in command:
                return "\n".join(
                    [
                        f"{root}/visible.txt",
                        f"{root}/visible.log",
                        f"{root}/.runtime.log",
                        f"{root}/visible-dir/kept.txt",
                        f"{root}/visible-dir/.nested.log",
                        f"{root}/.hidden-dir/leaked.txt",
                    ]
                )
            raise AssertionError(command)

        received_remote_paths = []

        def fake_run_hdc(args, **_kwargs):
            remote_path = args[-2]
            local_path = Path(args[-1])
            received_remote_paths.append(remote_path)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text("fixture", encoding="utf-8")
            return ""

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            task_executor, "remote_shell", side_effect=fake_remote_shell
        ), patch.object(task_executor, "run_hdc", side_effect=fake_run_hdc):
            task_executor.pull_outputs(
                "FileOrganization_0_001", temp_dir, target=None
            )

            outputs = Path(temp_dir) / "FileOrganization_0_001" / "outputs"
            for root_name in ("Desktop", "Download", "Documents"):
                local_root = outputs / root_name
                self.assertTrue((local_root / "visible.txt").is_file())
                self.assertTrue((local_root / "visible.log").is_file())
                self.assertTrue((local_root / "visible-dir" / "kept.txt").is_file())
                self.assertTrue((local_root / "visible-dir").is_dir())
                self.assertTrue((local_root / "visible-empty-dir").is_dir())
            self.assertFalse(
                any(
                    part.startswith(".")
                    for path in outputs.rglob("*")
                    for part in path.relative_to(outputs).parts
                )
            )

            self.assertEqual(9, len(received_remote_paths))
            self.assertTrue(
                all(
                    not any(
                        part.startswith(".")
                        for part in PurePosixPath(path).parts
                    )
                    for path in received_remote_paths
                )
            )

            manifest = json.loads(
                (outputs / "outputs_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(9, len(manifest["files"]))
            self.assertTrue(manifest["snapshot_complete"])
if __name__ == "__main__":
    unittest.main()
