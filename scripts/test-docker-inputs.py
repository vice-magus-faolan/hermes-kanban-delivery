#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Docker success/failure teardown and ignore-layer negative control.

Run on the Docker host, not inside a socket-mounted test container. All fixture
files and the single wrapper mutation live in a uniquely owned disposable Git
root. No runtime or query implementation is changed by this test.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run_wrapper(root, *args, expected):
    result = subprocess.run(["bash", "scripts/verify-docker.sh", *args], cwd=root,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    print(result.stdout, flush=True)
    assert result.returncode == expected, result.returncode
    assert "resource cleanup=0; staging cleanup=0" in result.stdout
    return result.stdout


def main():
    with tempfile.TemporaryDirectory(prefix="delivery-inputs-", dir=os.environ["TMPDIR"]) as directory:
        root = Path(directory)
        raw = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "--cached", "--others",
                                       "--exclude-standard", "--deduplicate", "-z"])
        for value in raw.split(b"\0"):
            if not value:
                continue
            relative = Path(os.fsdecode(value))
            source = ROOT / relative
            if source.is_symlink() or not source.is_file():
                raise RuntimeError("nonregular fixture input")
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        ignore = root / ".gitignore"
        with ignore.open("a") as stream:
            stream.write("\n/tests/.privacy-canary.py\n")
        (root / "tests" / ".privacy-canary.py").write_text("PRIVATE_CANARY = True\n")
        (root / ".env").write_text("PRIVATE_CANARY=True\n")
        subprocess.run(["git", "-C", str(root), "add", "-f", "tests/.privacy-canary.py"], check=True)
        command = ("python", "-m", "pytest", "-p", "no:cacheprovider", "-q", "tests/test_inputs.py")
        run_wrapper(root, *command, expected=0)
        run_wrapper(root, "false", expected=1)
        wrapper = root / "scripts" / "verify-docker.sh"
        original = wrapper.read_text()
        filtering = ('    if git check-ignore --no-index -q -- "$input"; then continue; else ignored=$?; fi\n'
                     '    if [[ $ignored -ne 1 ]]; then exit "$ignored"; fi\n')
        assert original.count(filtering) == 1
        wrapper.write_text(original.replace(filtering, ""))
        negative = run_wrapper(root, *command, expected=1)
        assert "test_selected_mount_excludes_ignored_private_inputs" in negative
    print("Real Docker input boundary: positive, forced failure, ignore-filter mutation rejected; cleanup verified.")


if __name__ == "__main__":
    main()
