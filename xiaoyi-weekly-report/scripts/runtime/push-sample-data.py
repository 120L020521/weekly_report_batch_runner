#!/usr/bin/env python3
"""Push sample data (Desktop/Documents/Downloads) to OpenHarmony device via hdc.

Usage:
    python push-sample-data.py "D:\\Downloads\\...\\何沐"
"""

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

MAPPINGS = [
    ("Desktop",   "/storage/media/100/local/files/Docs/Desktop"),
    ("Documents", "/storage/media/100/local/files/Docs/Documents"),
    ("Downloads", "/storage/media/100/local/files/Docs/Download"),
]


def run(cmd, check=True):
    print(f"  > {' '.join(cmd)}")
    return subprocess.run(cmd, check=check)


def main():
    # Fix Windows console encoding for CJK output
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Push sample data to OpenHarmony device via hdc")
    parser.add_argument("base_path", help=r'Local base path, e.g. "D:\Downloads\...\何沐"')
    args = parser.parse_args()

    base = args.base_path
    if not os.path.isdir(base):
        print(f"[ERROR] Path not found: {base}", file=sys.stderr)
        sys.exit(1)

    # Pre-check: hdc available
    hdc = shutil.which("hdc")
    if not hdc:
        print("[ERROR] hdc not found in PATH.", file=sys.stderr)
        sys.exit(1)

    # Pre-check: device connected
    r = run([hdc, "list", "targets"], check=False)
    if r.returncode != 0:
        print("[ERROR] No hdc device connected.", file=sys.stderr)
        sys.exit(1)

    print("=" * 44)
    print(" Push Sample Data to OpenHarmony Device")
    print("=" * 44)
    print(f" Source : {base}")
    print("=" * 44)
    print()

    with tempfile.TemporaryDirectory(prefix="hdc-push-") as tmpdir:
        for sub, remote in MAPPINGS:
            local_dir = os.path.join(base, sub)
            if not os.path.isdir(local_dir):
                print(f"[SKIP] {sub} - directory not found.\n")
                continue
            if not os.listdir(local_dir):
                print(f"[SKIP] {sub} - empty directory.\n")
                continue

            tar_path = os.path.join(tmpdir, f"{sub}.tar")

            # 1. Pack (Python tarfile handles Unicode paths natively)
            print(f"[1/4] PACK   {sub} ...")
            with tarfile.open(tar_path, "w") as tf:
                for item in os.listdir(local_dir):
                    tf.add(os.path.join(local_dir, item), arcname=item)

            # 2. Mkdir on device
            print(f"[2/4] MKDIR  {remote} ...")
            run([hdc, "shell", "mkdir", "-p", remote], check=False)

            # 3. Send tar to device
            print(f"[3/4] SEND   {sub}.tar -> /data/local/tmp/{sub}.tar ...")
            r = run([hdc, "file", "send", tar_path, f"/data/local/tmp/{sub}.tar"], check=False)
            if r.returncode != 0:
                print(f"[ERROR] hdc file send failed for {sub}\n")
                continue

            # 4. Extract on device
            print(f"[4/4] UNPACK to {remote} ...")
            run([hdc, "shell", "tar", "-xf", f"/data/local/tmp/{sub}.tar", "-C", remote], check=False)

            # Cleanup remote tar
            run([hdc, "shell", "rm", "-f", f"/data/local/tmp/{sub}.tar"], check=False)

            print(f"[OK]   {sub} -> {remote}\n")

    print("=" * 44)
    print(" All done!")
    print("=" * 44)


if __name__ == "__main__":
    main()
