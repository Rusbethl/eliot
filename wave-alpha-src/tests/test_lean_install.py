"""Verify wave_alpha works without the [llm] extra installed.

Builds a wheel from the working tree, installs it into an isolated venv with
*no extras* (no anthropic), and runs a non-LLM analysis. This
guards the contract that the deterministic engine never depends on the LLM.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.slow
def test_engine_runs_without_anthropic(tmp_path: Path) -> None:
    if shutil.which("uv") is None:
        pytest.skip("uv not available")

    # Build wheel into tmp_path
    dist = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist)],
        cwd=REPO_ROOT,
        check=True,
        timeout=180,
    )
    wheels = list(dist.glob("wave_alpha-*.whl"))
    assert len(wheels) == 1, f"expected one wheel, got {wheels}"

    # Fresh venv with ONLY base deps (no [llm] extra)
    venv = tmp_path / "venv"
    subprocess.run(["uv", "venv", str(venv)], check=True, timeout=30)
    py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run(
        ["uv", "pip", "install", "--python", str(py), str(wheels[0])],
        check=True,
        timeout=120,
    )

    # anthropic must NOT be importable
    proc = subprocess.run(
        [str(py), "-c", "import anthropic"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode != 0, (
        f"anthropic should not be importable in lean venv\n"
        f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
    )

    # wave_alpha must be importable and engine must work
    proc = subprocess.run(
        [
            str(py),
            "-c",
            "import wave_alpha; "
            "from wave_alpha.elliott.templates import load_bundled_templates; "
            "ts = load_bundled_templates(); "
            "assert len(ts) >= 8, f'expected >=8 templates, got {len(ts)}'; "
            "print('OK', len(ts))",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"engine import failed: {proc.stderr}"
    assert proc.stdout.startswith("OK"), proc.stdout
