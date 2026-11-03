# SPDX-License-Identifier: GPL-3.0-or-later
"""Executed inside the real wrapper; ignored supported inputs must stay out."""
from pathlib import Path
import os


def test_selected_mount_excludes_ignored_private_inputs():
    root = Path(__file__).resolve().parents[1]
    assert (root / "delivery" / "service.py").is_file()
    assert (root / "tests" / "test_native.py").is_file()
    assert os.geteuid() != 0
    assert (root / "delivery" / "service.py").read_text()
    assert not (root / "tests" / ".privacy-canary.py").exists()
    assert not (root / ".env").exists()
