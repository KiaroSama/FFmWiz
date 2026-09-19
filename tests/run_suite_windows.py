"""A Windows Job Object owns a supervised module, including orphaned children.

The module waits on its stdin gate until it has been assigned to the job.
Unlike taskkill /T, this boundary survives the root interpreter exiting.
Only stdlib ctypes is used; importing this module on POSIX is harmless.
"""
from __future__ import annotations

import ctypes
import os
import uuid
from ctypes import wintypes


class Job:
    """A private kill-on-close job; never attach an unrelated process."""

    def __init__(self, name: str | None = None) -> None:
        if os.name != "nt":
            raise OSError("Job Objects require Windows")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel = kernel
        signatures = {
            "OpenJobObjectW": ([wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int,
                                         ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int,
                                           ctypes.c_void_p, wintypes.DWORD,
                                           ctypes.c_void_p], wintypes.BOOL),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for api_name, (args, result) in signatures.items():
            function = getattr(kernel, api_name)
            function.argtypes, function.restype = args, result
        self.name = name or ("Local\\FFmWizSuite-" + uuid.uuid4().hex)
        self.handle = (kernel.OpenJobObjectW(0x0001 | 0x0004, False, name) if name
                       else kernel.CreateJobObjectW(None, self.name))
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if name is not None:
            return
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            self._check(kernel.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _check(ok) -> None:
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, pid: int) -> None:
        process = self._kernel.OpenProcess(0x0100 | 0x0001, False, pid)
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self._check(self._kernel.AssignProcessToJobObject(self.handle, process))
        finally:
            self._kernel.CloseHandle(process)

    def active(self) -> int:
        accounting = _Accounting()
        self._check(self._kernel.QueryInformationJobObject(
            self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None))
        return int(accounting.active_processes)

    def terminate(self) -> None:
        self._check(self._kernel.TerminateJobObject(self.handle, 1))

    def close(self) -> None:
        if self.handle:
            handle, self.handle = self.handle, None
            self._check(self._kernel.CloseHandle(handle))


class _BasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
                ("maximum_working_set", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD)]


class _IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in
                ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", _BasicLimits), ("io", _IOCounters),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_longlong) for name in
                ("user_time", "kernel_time", "period_user_time", "period_kernel_time")] + [
                    (name, wintypes.DWORD) for name in
                    ("page_faults", "total_processes", "active_processes", "terminated_processes")]


def join_current_process(name: str) -> None:
    """Join the ACTUAL interpreter, not a venv redirector that spawned it.

    CPython's Windows venv executable is a launcher, and can create the real
    interpreter before Popen returns. Assigning the launcher afterwards misses
    that existing descendant. The controlled child joins before opening its
    test-import gate, while the supervisor remains the job's lifetime owner.
    """
    job = Job(name)
    try:
        job.assign(os.getpid())
    finally:
        job.close()
