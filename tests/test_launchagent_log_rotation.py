from __future__ import annotations

import os
import plistlib
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "scripts" / "run_with_rotating_logs.py"
PLIST_DIR = ROOT / "scripts" / "launchagents"

PLIST_LOGS = {
    "com.dailystock.uvicorn.plist": (
        "__PROJECT_DIR__/logs/launchagent.uvicorn.out.log",
        "__PROJECT_DIR__/logs/launchagent.uvicorn.err.log",
    ),
    "com.dailystock.moomoo-sync.plist": (
        "__PROJECT_DIR__/logs/launchagent.moomoo-sync.out.log",
        "__PROJECT_DIR__/logs/launchagent.moomoo-sync.err.log",
    ),
    "com.dailystock.breakout-live.plist": (
        "__PROJECT_DIR__/logs/breakout-live.jsonl",
        "__PROJECT_DIR__/logs/launchagent.breakout-live.err.log",
    ),
}


def _wrapper_command(
    stdout_log: Path,
    stderr_log: Path,
    child_code: str,
    *,
    max_bytes: int = 1024,
    backup_count: int = 2,
) -> list[str]:
    return [
        sys.executable,
        str(WRAPPER),
        "--stdout-log",
        str(stdout_log),
        "--stderr-log",
        str(stderr_log),
        "--max-bytes",
        str(max_bytes),
        "--backup-count",
        str(backup_count),
        "--",
        sys.executable,
        "-c",
        child_code,
    ]


def test_wrapper_separates_streams_and_preserves_exit_code(tmp_path: Path) -> None:
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    child_code = (
        "import sys; "
        "print('only-stdout', flush=True); "
        "print('only-stderr', file=sys.stderr, flush=True); "
        "raise SystemExit(23)"
    )

    result = subprocess.run(
        _wrapper_command(stdout_log, stderr_log, child_code),
        check=False,
        timeout=10,
    )

    assert result.returncode == 23
    assert stdout_log.read_text(encoding="utf-8") == "only-stdout\n"
    assert stderr_log.read_text(encoding="utf-8") == "only-stderr\n"


def test_wrapper_rotates_each_stream_with_bounded_backups(tmp_path: Path) -> None:
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    child_code = (
        "import sys; "
        "[(print(f'out-{i:03d}-' + 'x' * 40, flush=True), "
        "print(f'err-{i:03d}-' + 'y' * 40, file=sys.stderr, flush=True)) "
        "for i in range(80)]"
    )

    result = subprocess.run(
        _wrapper_command(
            stdout_log,
            stderr_log,
            child_code,
            max_bytes=256,
            backup_count=2,
        ),
        check=False,
        timeout=10,
    )

    assert result.returncode == 0
    for base in (stdout_log, stderr_log):
        assert base.exists()
        assert Path(f"{base}.1").exists()
        assert Path(f"{base}.2").exists()
        assert not Path(f"{base}.3").exists()
        assert all(path.stat().st_size <= 256 for path in (base, Path(f"{base}.1"), Path(f"{base}.2")))


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal return codes are Unix-only")
def test_wrapper_preserves_sigkill_exit_semantics(tmp_path: Path) -> None:
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    child_code = (
        "import os, signal; "
        "os.kill(os.getpid(), signal.SIGKILL)"
    )

    result = subprocess.run(
        _wrapper_command(stdout_log, stderr_log, child_code),
        check=False,
        timeout=10,
    )

    assert result.returncode == -signal.SIGKILL


@pytest.mark.skipif(os.name == "nt", reason="process groups are Unix-only")
def test_wrapper_does_not_hang_on_inherited_background_pipe(tmp_path: Path) -> None:
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    child_code = (
        "import subprocess, sys; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "print('direct-child-exited', flush=True)"
    )

    result = subprocess.run(
        _wrapper_command(stdout_log, stderr_log, child_code),
        check=False,
        timeout=10,
    )

    assert result.returncode == 0
    assert "direct-child-exited" in stdout_log.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="LaunchAgents and POSIX signals are Unix-only")
def test_wrapper_forwards_sigterm_to_child(tmp_path: Path) -> None:
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    child_code = "\n".join(
        [
            "import signal",
            "import time",
            "def stop(_signum, _frame):",
            "    print('got-sigterm', flush=True)",
            "    raise SystemExit(42)",
            "signal.signal(signal.SIGTERM, stop)",
            "import os",
            "print(f'ready:{os.getpid()}', flush=True)",
            "while True:",
            "    time.sleep(0.05)",
        ]
    )
    process = subprocess.Popen(
        _wrapper_command(stdout_log, stderr_log, child_code),
    )
    child_pid = None

    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if stdout_log.exists():
                content = stdout_log.read_text(encoding="utf-8")
                ready = next((line for line in content.splitlines() if line.startswith("ready:")), None)
                if ready:
                    child_pid = int(ready.split(":", 1)[1])
                    break
            time.sleep(0.05)
        else:
            pytest.fail("child did not become ready")

        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 42
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if child_pid is not None:
                    try:
                        os.killpg(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.kill()
                process.wait(timeout=5)

    assert "got-sigterm" in stdout_log.read_text(encoding="utf-8")


@pytest.mark.parametrize("filename, expected_logs", PLIST_LOGS.items())
def test_launchagent_plists_route_output_through_rotating_wrapper(
    filename: str,
    expected_logs: tuple[str, str],
) -> None:
    raw = (PLIST_DIR / filename).read_bytes()
    config = plistlib.loads(raw)
    arguments = config["ProgramArguments"]

    assert arguments[0] == "__PYTHON__"
    assert arguments[1] == "__PROJECT_DIR__/scripts/run_with_rotating_logs.py"
    assert arguments[arguments.index("--stdout-log") + 1] == expected_logs[0]
    assert arguments[arguments.index("--stderr-log") + 1] == expected_logs[1]
    assert arguments[arguments.index("--max-bytes") + 1] == "10485760"
    assert arguments[arguments.index("--backup-count") + 1] == "3"
    separator = arguments.index("--")
    assert arguments[separator + 1] == "__PYTHON__"
    assert config["StandardOutPath"] == "/dev/null"
    assert config["StandardErrorPath"] == "/dev/null"
