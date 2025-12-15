# script_agent.py
from __future__ import annotations
import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

# === CONFIGURATION ===
BASE_DIR = Path(__file__).parent.resolve()   # Agent folder
WATCH_FOLDER = BASE_DIR / "ScriptQueue"
SCRIPT_FOLDER = BASE_DIR                     # Where scripts live (adjust if needed)
POLL_INTERVAL = 1                            # seconds
ENV_ROOT = Path.home() / ".script_agent_envs"  # Where per-script venvs are cached

def log(msg: str) -> None:
    print(f"[ScriptAgent] {msg}", flush=True)

def _venv_python_for(script: Path) -> Path:
    """Create (if needed) and return the Python executable inside a per-script venv."""
    ENV_ROOT.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(str(script.resolve()).encode("utf-8")).hexdigest()[:16]
    venv_dir = ENV_ROOT / f"env_{key}"
    py_in_venv = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    if not py_in_venv.exists():
        log(f"Creating venv: {venv_dir}")
        subprocess.check_call([sys.executable, "-m", "venv", str(venv_dir)])
        subprocess.check_call([str(py_in_venv), "-m", "pip", "install", "--upgrade", "pip"])

    return py_in_venv

def _parse_header_requirements(script: Path) -> List[str]:
    """Look for a header like:  # requirements: ttkbootstrap, requests==2.32.3"""
    try:
        with script.open("r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if i > 30:  # only scan the top
                    break
                m = re.match(r"^\s*#\s*requirements\s*:\s*(.+)$", line, flags=re.I)
                if m:
                    items = [p.strip() for p in re.split(r"[,\s]+", m.group(1)) if p.strip()]
                    return items
    except Exception:
        pass
    return []

def _requirements_next_to_script(script: Path) -> List[str]:
    """Read requirements.txt adjacent to the script, if present."""
    req = script.with_name("requirements.txt")
    if not req.exists():
        return []
    pkgs: List[str] = []
    for line in req.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pkgs.append(line)
    return pkgs

def _pip_install(py: Path, packages: List[str]) -> None:
    if not packages:
        return
    log(f"pip install: {packages}")
    subprocess.check_call([str(py), "-m", "pip", "install", "--disable-pip-version-check", *packages])

def _collect_requirements(script: Path) -> List[str]:
    # Priority: requirements.txt > header line
    pkgs = _requirements_next_to_script(script)
    if pkgs:
        log(f"Using requirements.txt ({len(pkgs)} packages)")
        return pkgs
    header = _parse_header_requirements(script)
    if header:
        log(f"Using header requirements: {header}")
        return header
    return []

_MNF_RE = re.compile(r"ModuleNotFoundError:\s+No module named '([^']+)'")

def _pip_name(import_name: str) -> str:
    """Map import names to common pip package names when they differ."""
    mapping = {
        "cv2": "opencv-python",
        "PIL": "Pillow",
        "sklearn": "scikit-learn",
        "yaml": "PyYAML",
    }
    return mapping.get(import_name, import_name)

def _run_once(py: Path, script: Path, args: List[str], timeout: Optional[int]) -> Tuple[int, Optional[str]]:
    """Run the script once; return (rc, missing_module_if_any)."""
    cmd = [str(py), str(script), *args]
    log(f"Executing: {script}")
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log("Timeout")
        return 124, None

    # Echo through output to agent logs
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)

    # Detect missing module
    if proc.returncode != 0 and proc.stderr:
        m = _MNF_RE.search(proc.stderr)
        if m:
            return proc.returncode, _pip_name(m.group(1))

    return proc.returncode, None

def run_script(script_path: Path, args: Optional[List[str]] = None, timeout: Optional[int] = None) -> int:
    """Ensure venv, install deps, run, auto-heal a single missing-module error."""
    script = script_path.resolve()
    if not script.is_file():
        log(f"Script not found: {script}")
        return 2

    py = _venv_python_for(script)

    # Pre-install dependencies if declared
    reqs = _collect_requirements(script)
    if reqs:
        _pip_install(py, reqs)

    rc, missing = _run_once(py, script, args or [], timeout)

    # Auto-install exactly one missing module, then retry
    if rc != 0 and missing:
        log(f"Missing module detected: '{missing}'. Installing and retrying once.")
        try:
            _pip_install(py, [missing])
        except subprocess.CalledProcessError as e:
            log(f"pip failed for '{missing}': {e}")
            return rc
        rc, _ = _run_once(py, script, args or [], timeout)

    return rc

def run() -> None:
    log(f"Watching for scripts in: {WATCH_FOLDER}")
    WATCH_FOLDER.mkdir(parents=True, exist_ok=True)

    while True:
        for task_file in list(WATCH_FOLDER.glob("*.task")):
            try:
                # Task file content: a single line = script name/path relative to SCRIPT_FOLDER
                script_name = task_file.read_text(encoding="utf-8").strip()
                script_path = (SCRIPT_FOLDER / script_name).resolve()

                # Run synchronously so we can auto-install + retry
                rc = run_script(script_path)
                log(f"Finished: {script_path} (rc={rc})")
            except Exception as e:
                log(f"Error processing {task_file.name}: {e}")
            finally:
                # Remove the task file so it doesn't re-run
                try:
                    task_file.unlink()
                except Exception:
                    pass
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    run()
