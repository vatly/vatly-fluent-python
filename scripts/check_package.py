"""Isolated wheel installation; the pinned SDK resolves from PyPI."""

import pathlib
import subprocess
import tempfile

root = pathlib.Path(__file__).resolve().parents[1]
wheel = next((root / "dist").glob("*.whl"))
with tempfile.TemporaryDirectory() as tmp:
    env = pathlib.Path(tmp) / "venv"
    subprocess.run(["uv", "venv", str(env)], check=True)
    python = str(env / "bin/python")
    subprocess.run(["uv", "pip", "install", "--python", python, str(wheel)], check=True)
    subprocess.run(
        [
            python,
            "-c",
            "from importlib.resources import files; from vatly_fluent import Vatly, Wiring; assert files('vatly_fluent').joinpath('py.typed').is_file(); print('Fluent wheel import and py.typed OK')",
        ],
        cwd=tmp,
        check=True,
    )
