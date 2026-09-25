#!/usr/bin/env python3
"""Run backend (uvicorn --reload) and frontend (vite dev) together, one command.

    python dev.py

Streams both logs prefixed with [backend]/[frontend] and stops both on Ctrl+C.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

VENV_PYTHON = BACKEND / (".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python")


def _stream(proc: subprocess.Popen, label: str) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        print(f"[{label}] {line}", end="", flush=True)


def main() -> int:
    if not VENV_PYTHON.exists():
        print(
            f"Backend venv not found at {VENV_PYTHON}.\n"
            "First-time setup: cd backend && uv venv && uv pip install -e .",
            file=sys.stderr,
        )
        return 1
    if not (FRONTEND / "node_modules").exists():
        print(
            f"Frontend deps not found at {FRONTEND / 'node_modules'}.\n"
            "First-time setup: cd frontend && npm install",
            file=sys.stderr,
        )
        return 1

    npm = shutil.which("npm")
    if npm is None:
        print("npm not found on PATH.", file=sys.stderr)
        return 1

    backend_proc = subprocess.Popen(
        # --reload-include .env: uvicorn's watcher only tracks *.py by default, so an
        # edited .env (HR_PASSWORD, API keys, VOICE_MODE) stayed invisible to the running
        # process - Settings is read once at import. The symptom is a config change that
        # silently does nothing until someone thinks to restart, which cost an afternoon
        # on a changed HR password. Restarting on .env keeps the file the source of truth.
        [
            str(VENV_PYTHON),
            "-m",
            "uvicorn",
            "app.main:app",
            "--reload",
            "--reload-include",
            ".env",
            "--port",
            "8000",
        ],
        cwd=BACKEND,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    frontend_proc = subprocess.Popen(
        [npm, "run", "dev"],
        cwd=FRONTEND,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    threads = [
        threading.Thread(target=_stream, args=(backend_proc, "backend"), daemon=True),
        threading.Thread(target=_stream, args=(frontend_proc, "frontend"), daemon=True),
    ]
    for t in threads:
        t.start()

    print("backend:  http://localhost:8000", flush=True)
    print("frontend: http://localhost:5173", flush=True)
    print("Ctrl+C to stop both.\n", flush=True)

    try:
        while True:
            backend_code = backend_proc.poll()
            frontend_code = frontend_proc.poll()
            if backend_code is not None:
                print(f"[backend] exited with code {backend_code}")
                break
            if frontend_code is not None:
                print(f"[frontend] exited with code {frontend_code}")
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        for proc in (backend_proc, frontend_proc):
            if proc.poll() is None:
                proc.terminate()
        for proc in (backend_proc, frontend_proc):
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
