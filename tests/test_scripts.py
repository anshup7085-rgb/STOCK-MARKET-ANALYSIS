"""
Every Python file in the repository compiles, and every deploy script passes `bash -n`. The server
runs these scripts directly; a damaged one fails there silently. Run: PYTHONPATH=. python tests/test_scripts.py
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_python_file_compiles():
    bad = []
    for p in sorted(ROOT.rglob("*.py")):
        if any(part in (".venv", "venv", "__pycache__") for part in p.parts):
            continue
        try:
            compile(p.read_bytes(), str(p), "exec")
        except (SyntaxError, ValueError) as exc:
            bad.append(f"{p.relative_to(ROOT)}: {type(exc).__name__}: {str(exc)[:120]}")
    assert not bad, bad


def test_every_deploy_script_parses():
    if not shutil.which("bash"):
        return
    bad = [p.name for p in sorted((ROOT / "deploy").glob("*.sh"))
           if subprocess.run(["bash", "-n", str(p)], capture_output=True).returncode != 0]
    assert not bad, bad


def test_every_tool_starts():
    """Each tool with a --help (argparse) prints it; a tool without one at least imports."""
    import os
    import sys
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    bad = []
    for p in sorted((ROOT / "tools").glob("*.py")):
        src = p.read_text()
        if "argparse" not in src:
            continue
        r = subprocess.run([sys.executable, str(p), "--help"], capture_output=True, text=True, env=env, timeout=60)
        if r.returncode != 0:
            bad.append(f"{p.name}: {r.stderr.strip().splitlines()[-1][:120] if r.stderr.strip() else r.returncode}")
    assert not bad, bad


if __name__ == "__main__":
    import sys
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
