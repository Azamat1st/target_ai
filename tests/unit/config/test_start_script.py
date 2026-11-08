import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BASH = shutil.which("bash")


def make_workdir(tmp_path: Path) -> Path:
    work = tmp_path / "proj"
    work.mkdir()
    shutil.copy(ROOT / "start.sh", work / "start.sh")
    shutil.copy(ROOT / ".env.example", work / ".env.example")
    return work


def run(work: Path, path_dirs: list[Path]):
    env = {"PATH": os.pathsep.join(str(p) for p in path_dirs) + os.pathsep + "/usr/bin:/bin"}
    return subprocess.run([BASH, str(work / "start.sh")], capture_output=True, text=True, env=env, timeout=30)


def fake_uv(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text("#!/bin/sh\necho \"uv $@\"\n")
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def test_start_script_is_executable():
    assert os.access(ROOT / "start.sh", os.X_OK)


def test_without_uv_prints_install_command_and_fails(tmp_path):
    work = make_workdir(tmp_path)
    if shutil.which("uv", path="/usr/bin:/bin"):
        pytest.skip("uv лежит в системном PATH — сценарий не воспроизвести")
    result = run(work, [])
    assert result.returncode != 0
    assert "astral.sh/uv/install.sh" in result.stdout


def test_without_env_file_creates_it_and_explains(tmp_path):
    work = make_workdir(tmp_path)
    result = run(work, [fake_uv(tmp_path)])
    assert result.returncode == 0
    assert (work / ".env").read_text() == (work / ".env.example").read_text()
    assert "TELEGRAM_BOT_TOKEN" in result.stdout
    assert "uv sync" in result.stdout and "uv run python -m target_ai" in result.stdout


def test_existing_env_file_is_not_overwritten(tmp_path):
    work = make_workdir(tmp_path)
    (work / ".env").write_text("LOG_LEVEL=DEBUG\n")
    run(work, [fake_uv(tmp_path)])
    assert (work / ".env").read_text() == "LOG_LEVEL=DEBUG\n"
