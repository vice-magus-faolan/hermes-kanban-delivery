#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Docker success/failure teardown and ignore-layer negative control.

Run on the Docker host, not inside a socket-mounted test container. All fixture
files and the single wrapper mutation live in a uniquely owned disposable Git
root. No runtime or query implementation is changed by this test.
"""
from pathlib import Path
import os
import json
import shutil
import signal
import stat
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def run_wrapper(root, *args, expected):
    result = subprocess.run(["bash", "scripts/verify-docker.sh", *args], cwd=root,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    print(result.stdout, flush=True)
    if result.returncode != expected:
        raise RuntimeError(f"wrapper exit {result.returncode}, expected {expected}")
    if "resource cleanup=0; staging cleanup=0" not in result.stdout:
        raise RuntimeError("wrapper did not report successful cleanup")
    return result.stdout


def check_signal(root, scratch, signum):
    """Signal only Bash while its real container is blocked, not its process group."""
    with tempfile.TemporaryFile(mode="w+t") as output:
        process = subprocess.Popen(
            ["bash", "scripts/verify-docker.sh", "python", "-c", "import time; time.sleep(300)"],
            cwd=root, env={**os.environ, "TMPDIR": str(scratch)},
            text=True, stdout=output, stderr=subprocess.STDOUT,
        )
        container = None
        staging = None
        staging_mode = 0
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and process.poll() is None:
                found = subprocess.check_output(
                    ["docker", "ps", "-q", "--filter", f"name=^/kanban-delivery-[0-9]+-{process.pid}$"],
                    text=True,
                ).splitlines()
                if found:
                    if len(found) != 1:
                        raise RuntimeError("ambiguous owned signal-test container")
                    container = found[0]
                    details = json.loads(subprocess.check_output(["docker", "inspect", container], text=True))[0]
                    source = next(Path(mount["Source"]) for mount in details["Mounts"]
                                  if mount["Destination"] == "/source")
                    staging = source.parent
                    if staging.parent != scratch or source.name != "source":
                        raise RuntimeError("signal-test staging ownership mismatch")
                    staging_mode = stat.S_IMODE(staging.stat().st_mode)
                    break
                time.sleep(0.1)
            if staging is None:
                raise RuntimeError("blocking real container did not start")
            started = time.monotonic()
            process.send_signal(signum)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(f"wrapper did not retire promptly on {signum.name}") from error
            elapsed = time.monotonic() - started
        finally:
            # A failing predecessor must not leave its blocked container behind.
            if process.poll() is None:
                if container is not None:
                    subprocess.run(["docker", "rm", "-f", container], check=True)
                else:
                    process.terminate()
                process.wait(timeout=15)
            output.seek(0)
            captured = output.read()
            print(captured, flush=True)
        if process.returncode != 128 + signum:
            raise RuntimeError(f"signal exit {process.returncode}, expected {128 + signum}")
        if "resource cleanup=0; staging cleanup=0" not in captured or staging.exists():
            raise RuntimeError("signal cleanup failed")
        if staging_mode != 0o700:
            raise RuntimeError(f"private staging root mode is {staging_mode:o}, expected 700")
        print(f"{signum.name}: wrapper-only signal exit={process.returncode}; elapsed={elapsed:.2f}s; "
              "staging root=0700; cleanup verified.", flush=True)


def main():
    with tempfile.TemporaryDirectory(prefix="delivery-inputs-", dir=os.environ["TMPDIR"]) as directory:
        root = Path(directory)
        # The ancestors are deliberately traversable; only the staging root is private.
        root.chmod(0o755)
        scratch = root / "scratch"
        scratch.mkdir(mode=0o755)
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
        check_signal(root, scratch, signal.SIGTERM)
        check_signal(root, scratch, signal.SIGINT)
        command = ("python", "-m", "pytest", "-p", "no:cacheprovider", "-q", "tests/test_inputs.py")
        run_wrapper(root, *command, expected=0)
        run_wrapper(root, "false", expected=1)
        wrapper = root / "scripts" / "verify-docker.sh"
        original = wrapper.read_text()
        filtering = ('    if git check-ignore --no-index -q -- "$input"; then continue; else ignored=$?; fi\n'
                     '    if [[ $ignored -ne 1 ]]; then exit "$ignored"; fi\n')
        if original.count(filtering) != 1:
            raise RuntimeError("ignore-filter mutation target must occur exactly once")
        wrapper.write_text(original.replace(filtering, ""))
        negative = run_wrapper(root, *command, expected=1)
        if "test_selected_mount_excludes_ignored_private_inputs" not in negative:
            raise RuntimeError("ignore-filter negative control did not report the expected failing test")
    print("Real Docker input boundary: signals, private staging, positive, forced failure, "
          "ignore-filter mutation rejected; cleanup verified.")


if __name__ == "__main__":
    main()
