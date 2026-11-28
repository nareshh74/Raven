"""Cross-platform opt-in system and display sleep prevention while tasks run."""

from __future__ import annotations

import atexit
import ctypes
import os
import shutil
import subprocess
import sys
import threading
from typing import TYPE_CHECKING, Callable, Protocol

from loguru import logger

if TYPE_CHECKING:
    from raven.config.live import LiveConfig

_REASON = "Raven is running an active task"
_DISABLE_ENV = "RAVEN_DISABLE_SLEEP_INHIBIT"

_POWER_REQUEST_CONTEXT_VERSION = 0
_POWER_REQUEST_CONTEXT_SIMPLE_STRING = 0x00000001
_POWER_REQUEST_DISPLAY_REQUIRED = 0
_POWER_REQUEST_SYSTEM_REQUIRED = 1


class _DetailedReason(ctypes.Structure):
    _fields_ = [
        ("LocalizedReasonModule", ctypes.c_void_p),
        ("LocalizedReasonId", ctypes.c_uint32),
        ("ReasonStringCount", ctypes.c_uint32),
        ("ReasonStrings", ctypes.POINTER(ctypes.c_wchar_p)),
    ]


class _ReasonUnion(ctypes.Union):
    _fields_ = [
        ("Detailed", _DetailedReason),
        ("SimpleReasonString", ctypes.c_wchar_p),
    ]


class _ReasonContext(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_uint32),
        ("Flags", ctypes.c_uint32),
        ("Reason", _ReasonUnion),
    ]


class _SleepBackend(Protocol):
    def acquire(self) -> bool: ...

    def release(self) -> None: ...


class _NoopBackend:
    def acquire(self) -> bool:
        return False

    def release(self) -> None:
        return None

    def is_alive(self) -> bool:
        return True


class _WindowsBackend:
    def __init__(self) -> None:
        self._handle: int | None = None
        self._set_types: list[int] = []
        self._kernel32 = None
        self._invalid_handle = ctypes.c_void_p(-1).value

    def _init_api(self) -> bool:
        if self._kernel32 is not None:
            return True
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

            handle_t = ctypes.c_void_p
            kernel32.PowerCreateRequest.argtypes = [ctypes.POINTER(_ReasonContext)]
            kernel32.PowerCreateRequest.restype = handle_t
            kernel32.PowerSetRequest.argtypes = [handle_t, ctypes.c_int]
            kernel32.PowerSetRequest.restype = ctypes.c_int
            kernel32.PowerClearRequest.argtypes = [handle_t, ctypes.c_int]
            kernel32.PowerClearRequest.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = [handle_t]
            kernel32.CloseHandle.restype = ctypes.c_int

            self._kernel32 = kernel32
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("sleep_guard: windows api init failed: {}", exc)
            return False

    def is_alive(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        if not self._init_api():
            return False
        assert self._kernel32 is not None
        handle = None
        set_types: list[int] = []
        try:
            ctx = _ReasonContext()
            ctx.Version = _POWER_REQUEST_CONTEXT_VERSION
            ctx.Flags = _POWER_REQUEST_CONTEXT_SIMPLE_STRING
            ctx.Reason.SimpleReasonString = _REASON
            handle = self._kernel32.PowerCreateRequest(ctypes.byref(ctx))
            if not handle or handle == self._invalid_handle:
                logger.debug("sleep_guard: PowerCreateRequest failed")
                return False
            for req_type in (_POWER_REQUEST_SYSTEM_REQUIRED, _POWER_REQUEST_DISPLAY_REQUIRED):
                if self._kernel32.PowerSetRequest(handle, req_type):
                    set_types.append(req_type)
            if not set_types:
                self._kernel32.CloseHandle(handle)
                handle = None
                logger.debug("sleep_guard: PowerSetRequest failed for both system and display")
                return False
            self._handle = handle
            self._set_types = set_types
            return True
        except Exception as exc:  # noqa: BLE001
            if handle and handle != self._invalid_handle:
                for req_type in reversed(set_types):
                    try:
                        self._kernel32.PowerClearRequest(handle, req_type)
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    self._kernel32.CloseHandle(handle)
                except Exception:  # noqa: BLE001
                    pass
            logger.debug("sleep_guard: windows acquire failed: {}", exc)
            return False

    def release(self) -> None:
        handle = self._handle
        set_types = self._set_types
        self._handle = None
        self._set_types = []
        if handle is None or self._kernel32 is None:
            return
        for req_type in reversed(set_types):
            try:
                self._kernel32.PowerClearRequest(handle, req_type)
            except Exception as exc:  # noqa: BLE001
                logger.debug("sleep_guard: PowerClearRequest({}) failed: {}", req_type, exc)
        try:
            self._kernel32.CloseHandle(handle)
        except Exception as exc:  # noqa: BLE001
            logger.debug("sleep_guard: CloseHandle failed: {}", exc)


class _SubprocessBackend:
    def __init__(self, cmd_factory) -> None:
        self._cmd_factory = cmd_factory
        self._proc: subprocess.Popen | None = None

    def is_alive(self) -> bool:
        if self._proc is None:
            return False
        if self._proc.poll() is not None:
            self._proc = None
            return False
        return True

    def acquire(self) -> bool:
        if self.is_alive():
            return True
        try:
            cmd = self._cmd_factory()
            if not cmd:
                return False
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if proc.poll() is not None:
                logger.debug("sleep_guard: inhibitor exited immediately with rc={}", proc.returncode)
                return False
            self._proc = proc
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("sleep_guard: subprocess acquire failed: {}", exc)
            return False

    @staticmethod
    def _terminate_proc(proc: subprocess.Popen) -> None:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2.0)
        except Exception as exc:  # noqa: BLE001
            logger.debug("sleep_guard: subprocess release failed: {}", exc)

    def detach_release(self) -> Callable[[], None] | None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return None
        return lambda: self._terminate_proc(proc)

    def release(self) -> None:
        cleanup = self.detach_release()
        if cleanup is not None:
            cleanup()


def _macos_cmd() -> list[str] | None:
    exe = shutil.which("caffeinate")
    if not exe:
        return None
    return [exe, "-i", "-d", "-w", str(os.getpid())]


def _linux_cmd() -> list[str] | None:
    exe = shutil.which("systemd-inhibit")
    if not exe:
        return None
    return [
        exe,
        "--what=idle:sleep",
        "--who=Raven",
        "--why=Raven has active tasks",
        "--mode=block",
        "sleep",
        "infinity",
    ]


def _default_backend() -> _SleepBackend:
    if os.environ.get(_DISABLE_ENV) == "1":
        return _NoopBackend()
    if sys.platform == "win32":
        return _WindowsBackend()
    if sys.platform == "darwin":
        return _SubprocessBackend(_macos_cmd)
    if sys.platform.startswith("linux"):
        return _SubprocessBackend(_linux_cmd)
    return _NoopBackend()


class SleepGuard:
    """Reference-counted OS sleep inhibitor controlled by an opt-in switch."""

    def __init__(self, *, backend: _SleepBackend | None = None, enabled: bool = False) -> None:
        self._lock = threading.Lock()
        self._active: set[str] = set()
        self._enabled = bool(enabled)
        self._inhibiting = False
        self._backend = backend if backend is not None else _default_backend()

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    def _backend_alive(self) -> bool:
        check = getattr(self._backend, "is_alive", None)
        if check is None:
            return True
        try:
            return bool(check())
        except Exception:  # noqa: BLE001
            return False

    @property
    def is_inhibiting(self) -> bool:
        with self._lock:
            if self._inhibiting and not self._backend_alive():
                self._inhibiting = False
            return self._inhibiting

    def _apply_locked(self) -> Callable[[], None] | None:
        if self._inhibiting and not self._backend_alive():
            self._inhibiting = False
        want = self._enabled and bool(self._active)
        if want and not self._inhibiting:
            try:
                self._inhibiting = bool(self._backend.acquire())
            except Exception as exc:  # noqa: BLE001
                logger.debug("sleep_guard: backend acquire raised: {}", exc)
                self._inhibiting = False
        elif not want and self._inhibiting:
            self._inhibiting = False
            detach = getattr(self._backend, "detach_release", None)
            if detach is not None:
                try:
                    return detach()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("sleep_guard: backend detach_release raised: {}", exc)
                    return None
            try:
                self._backend.release()
            except Exception as exc:  # noqa: BLE001
                logger.debug("sleep_guard: backend release raised: {}", exc)
        return None

    @staticmethod
    def _run_cleanup(cleanup: Callable[[], None] | None) -> None:
        if cleanup is not None:
            try:
                cleanup()
            except Exception as exc:  # noqa: BLE001
                logger.debug("sleep_guard: deferred release raised: {}", exc)

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)
            cleanup = self._apply_locked()
        self._run_cleanup(cleanup)

    def sync_from_config(self, live: "LiveConfig | None" = None) -> None:
        try:
            from raven.config.live import default_live, prevent_sleep_enabled

            target = live if live is not None else default_live()
            enabled = prevent_sleep_enabled(target)
        except Exception as exc:  # noqa: BLE001
            logger.debug("sleep_guard: config sync failed: {}", exc)
            return
        self.set_enabled(enabled)

    def acquire(self, token: str) -> None:
        with self._lock:
            self._active.add(token)
            cleanup = self._apply_locked()
        self._run_cleanup(cleanup)

    def release(self, token: str) -> None:
        with self._lock:
            self._active.discard(token)
            cleanup = self._apply_locked()
        self._run_cleanup(cleanup)

    def release_all(self) -> None:
        with self._lock:
            self._active.clear()
            cleanup = self._apply_locked()
        self._run_cleanup(cleanup)


_GUARD_LOCK = threading.Lock()
_GUARD: SleepGuard | None = None


def get_sleep_guard() -> SleepGuard:
    global _GUARD
    if _GUARD is None:
        with _GUARD_LOCK:
            if _GUARD is None:
                guard = SleepGuard()
                atexit.register(guard.release_all)
                _GUARD = guard
    return _GUARD
