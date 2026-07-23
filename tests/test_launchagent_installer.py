# -*- coding: utf-8 -*-
"""Tests for selecting a safe subset of the macOS LaunchAgents."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_launchagents.sh"


def _stub_command(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _run_installer(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _stub_command(fake_bin, "launchctl", "exit 0")
    _stub_command(fake_bin, "plutil", "exit 0")
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "PYTHON": sys.executable,
        }
    )
    return subprocess.run(
        ["bash", str(INSTALLER), *args],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_only_uvicorn_renders_and_loads_no_other_agent(tmp_path: Path) -> None:
    result = _run_installer(tmp_path, "--only", "uvicorn")

    assert result.returncode == 0, result.stderr
    target = tmp_path / "home" / "Library" / "LaunchAgents"
    assert (target / "com.dailystock.uvicorn.plist").is_file()
    assert not (target / "com.dailystock.moomoo-sync.plist").exists()
    assert not (target / "com.dailystock.breakout-live.plist").exists()
    assert "[loaded] com.dailystock.uvicorn" in result.stdout
    assert "[skip] com.dailystock.moomoo-sync" in result.stdout


def test_only_rejects_unknown_agent_without_writing(tmp_path: Path) -> None:
    result = _run_installer(tmp_path, "--only", "unknown")

    assert result.returncode == 2
    assert "unknown agent for --only" in result.stderr
    assert not (tmp_path / "home" / "Library" / "LaunchAgents").exists()


def test_only_and_skip_are_mutually_exclusive(tmp_path: Path) -> None:
    result = _run_installer(
        tmp_path,
        "--only",
        "uvicorn",
        "--skip",
        "breakout",
    )

    assert result.returncode == 2
    assert "cannot be used together" in result.stderr
