"""Supervise a module until its interpreter AND owned process scope have exited."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

MAX_LOG_TAIL = 4000
MAX_RECORD_BYTES = 8 * 1024 * 1024
STOP_SECONDS = 5.0


def module_problem(name: str, status: str, seconds: float, detail: str) -> dict:
    return {"module": name, "status": status, "seconds": seconds, "run": 0,
            "unraisable": [], "failures": [], "errors": [(f"{name} ({status})", detail)],
            "skipped": [], "unexpected": [], "ok": False}


def _log_tail(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - MAX_LOG_TAIL))
            return handle.read(MAX_LOG_TAIL).decode("utf-8", "replace")
    except OSError:
        return ""


def _group_active(pgid: int) -> bool:
    # Linux zombies have exited, but killpg(0) still sees their process group.
    # Inspect state rather than calling those already-dead processes a leak.
    proc_root = Path("/proc")
    if sys.platform.startswith("linux") and proc_root.exists():
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[2]) == pgid and fields[0] not in {"Z", "X"}:
                    return True
            except (OSError, ValueError, IndexError):
                continue
        return False
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


class ProcessScope:
    """Own one new session on POSIX, or a gated Job Object on Windows."""

    def __init__(self, process: subprocess.Popen) -> None:
        self.process, self.pgid, self.job = process, process.pid, None
        if os.name == "nt":
            from run_suite_windows import Job
            self.job = Job()

    def active(self) -> bool:
        return bool(self.job.active()) if self.job else _group_active(self.pgid)

    def stop(self) -> None:
        if self.job:
            self.job.terminate()
        else:
            try:
                os.killpg(self.pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.process.wait(timeout=STOP_SECONDS)
        deadline = time.monotonic() + STOP_SECONDS
        while self.active():
            if time.monotonic() >= deadline:
                raise TimeoutError("owned process scope did not exit after termination")
            time.sleep(0.02)

    def close(self) -> None:
        if self.job:
            self.job.close()


def _record(path: Path, name: str) -> dict | None:
    if not path.exists():
        return None
    if path.stat().st_size > MAX_RECORD_BYTES:
        raise ValueError("module result exceeds the size limit")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("module") != name:
        raise ValueError("module result identity mismatch")
    if type(data.get("run")) is not int or data["run"] < 0:
        raise ValueError("invalid test count")
    for key in ("errors", "failures", "skipped", "unexpected"):
        if not isinstance(data.get(key), list):
            raise ValueError(f"invalid {key} record")
    for key in ("errors", "failures", "skipped"):
        if any(not isinstance(item, (list, tuple)) or len(item) != 2
               or not all(isinstance(value, str) for value in item) for item in data[key]):
            raise ValueError(f"invalid {key} entry")
    if data.get("status") not in {"ok", "timeout", "crash", "cancelled", "not-run"}:
        raise ValueError("invalid module status")
    return data


def supervise_module(name: str, scratch: Path, timeout: float,
                     live: dict | None = None, *, cancel: threading.Event | None = None,
                     child: Path | None = None) -> dict:
    child = child or Path(__file__).with_name("run_suite_child.py")
    root = child.resolve().parent.parent
    result_path, log_path = scratch / f"{name}.json", scratch / f"{name}.log"
    # A result from a previous attempt never belongs to this process.
    result_path.unlink(missing_ok=True)
    started = time.monotonic()
    cancelled = cancel or threading.Event()
    if cancelled.is_set():
        return module_problem(name, "not-run", 0.0, "cancelled before module launch")
    executable, environment = sys.executable, None
    if os.name == "nt" and getattr(sys, "_base_executable", executable) != executable:
        # Match PC/venvlauncher.c's __PYVENV_LAUNCHER__ contract without its
        # intermediate kill-on-close job. That job could kill a leaked child
        # as the interpreter exits, BEFORE our supervisor observes the leak.
        # The base interpreter still resolves this venv's prefix/dependencies.
        environment = dict(os.environ, __PYVENV_LAUNCHER__=executable)
        executable = sys._base_executable
    args = [executable, str(child), name, str(result_path), str(timeout), "--gated"]
    options = {"start_new_session": True} if os.name != "nt" else {}
    outcome, details, scope = "ok", [], None
    with log_path.open("wb") as log:
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=log,
                                   stderr=subprocess.STDOUT, cwd=root, env=environment, **options)
        try:
            scope = ProcessScope(process)
            if live is not None:
                live[name] = process
            if cancelled.is_set():
                outcome = "cancelled"
            else:
                gate = "G" + (scope.job.name if scope.job else "") + "\n"
                process.stdin.write(gate.encode("ascii"))
                process.stdin.flush()
            process.stdin.close()
            deadline = started + timeout
            while process.poll() is None:
                if cancelled.is_set():
                    outcome = "cancelled"
                    break
                if time.monotonic() >= deadline:
                    outcome = "timeout"
                    break
                time.sleep(0.02)
            if outcome != "ok":
                details.append(f"module {outcome} after {time.monotonic() - started:.3f}s")
                scope.stop()
            elif scope.active():
                outcome = "crash"
                details.append("module interpreter exited leaving a live child process")
                scope.stop()
            code = process.wait(timeout=STOP_SECONDS)
        except BaseException:
            # Assignment failure occurs before the gate opens: no test code ran.
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            if scope is not None:
                scope.stop()
            elif process.poll() is None:
                process.kill()
                process.wait(timeout=STOP_SECONDS)
            raise
        finally:
            if live is not None:
                live.pop(name, None)
            if scope is not None:
                scope.close()
    seconds = time.monotonic() - started
    try:
        record = _record(result_path, name)
    except (OSError, ValueError) as exc:
        record = None
        outcome = "crash" if outcome == "ok" else outcome
        details.append(f"invalid module result: {exc}")
    normal_test_failure = bool(record and code == 1 and
                               (record["errors"] or record["failures"] or record["unexpected"]))
    if code != 0 and outcome == "ok" and not normal_test_failure:
        outcome = "crash"
        details.append(f"module interpreter exited {code}")
    if record is None:
        record = module_problem(name, outcome if outcome != "ok" else "crash",
                                seconds, "module did not publish a complete result")
    record["seconds"] = seconds
    record["process_exit_code"] = code
    record["supervisor_outcome"] = outcome
    if outcome != "ok":
        # Keep completed tests and their diagnostics, but never let a pre-exit
        # JSON snapshot overrule a crash, timeout, cancellation or surviving PID.
        record["status"], record["ok"] = outcome, False
        record["errors"].append((f"{name} ({outcome})", "\n".join(details) +
                                 "\nlast output:\n" + _log_tail(log_path)))
    return record
