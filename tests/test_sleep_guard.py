"""Tests for cross-platform sleep prevention guard and active task hooks."""

from __future__ import annotations

import asyncio
import ctypes
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from raven.agent.loop.main import AgentLoop
from raven.agent.subagent.manager import SubagentManager
from raven.agent.tools import background_exec
from raven.config.live import LiveConfig
from raven.utils import sleep_guard


class _FakeBackend:
    def __init__(self, *, acquire_ok: bool = True, fail_acquire: bool = False, fail_release: bool = False) -> None:
        self.acquire_calls = 0
        self.release_calls = 0
        self.acquire_ok = acquire_ok
        self.fail_acquire = fail_acquire
        self.fail_release = fail_release

    def acquire(self) -> bool:
        self.acquire_calls += 1
        if self.fail_acquire:
            raise RuntimeError("acquire boom")
        return self.acquire_ok

    def release(self) -> None:
        self.release_calls += 1
        if self.fail_release:
            raise RuntimeError("release boom")


def test_sleep_guard_refcount_idempotency_and_live_toggle():
    backend = _FakeBackend()
    guard = sleep_guard.SleepGuard(backend=backend, enabled=False)

    guard.acquire("t1")
    guard.acquire("t1")
    assert guard.active_count == 1
    assert not guard.is_inhibiting
    assert backend.acquire_calls == 0

    guard.set_enabled(True)
    assert guard.is_inhibiting
    assert backend.acquire_calls == 1

    guard.acquire("t2")
    assert guard.active_count == 2
    assert backend.acquire_calls == 1

    guard.release("t1")
    assert guard.active_count == 1
    assert guard.is_inhibiting
    assert backend.release_calls == 0

    guard.set_enabled(False)
    assert not guard.is_inhibiting
    assert backend.release_calls == 1
    assert guard.active_count == 1

    guard.set_enabled(True)
    assert guard.is_inhibiting
    assert backend.acquire_calls == 2

    guard.release("t2")
    guard.release("t2")
    guard.release("missing")
    assert guard.active_count == 0
    assert not guard.is_inhibiting
    assert backend.release_calls == 2


def test_sleep_guard_release_all_and_error_resilience():
    backend = _FakeBackend(fail_acquire=True)
    guard = sleep_guard.SleepGuard(backend=backend, enabled=True)
    guard.acquire("a")
    assert guard.active_count == 1
    assert not guard.is_inhibiting

    backend2 = _FakeBackend(fail_release=True)
    guard2 = sleep_guard.SleepGuard(backend=backend2, enabled=True)
    guard2.acquire("a")
    guard2.acquire("b")
    assert guard2.is_inhibiting
    guard2.release_all()
    assert guard2.active_count == 0
    assert not guard2.is_inhibiting
    assert backend2.release_calls == 1


def test_sleep_guard_env_disable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RAVEN_DISABLE_SLEEP_INHIBIT", "1")
    backend = sleep_guard._default_backend()
    assert isinstance(backend, sleep_guard._NoopBackend)
    assert backend.acquire() is False


def test_windows_reason_context_layout_64bit():
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(sleep_guard._ReasonContext) == 32


def test_windows_backend_sets_and_clears_system_and_display(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, tuple]] = []
    fake_handle = 0x1234

    class _FakeFunc:
        def __init__(self, name: str, rv):
            self._name = name
            self._rv = rv
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            calls.append((self._name, args))
            return self._rv

    fake_kernel32 = SimpleNamespace(
        PowerCreateRequest=_FakeFunc("PowerCreateRequest", fake_handle),
        PowerSetRequest=_FakeFunc("PowerSetRequest", 1),
        PowerClearRequest=_FakeFunc("PowerClearRequest", 1),
        CloseHandle=_FakeFunc("CloseHandle", 1),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: fake_kernel32, raising=False)

    backend = sleep_guard._WindowsBackend()
    assert backend.acquire() is True
    assert [c[0] for c in calls] == ["PowerCreateRequest", "PowerSetRequest", "PowerSetRequest"]
    assert calls[1][1] == (fake_handle, sleep_guard._POWER_REQUEST_SYSTEM_REQUIRED)
    assert calls[2][1] == (fake_handle, sleep_guard._POWER_REQUEST_DISPLAY_REQUIRED)

    backend.release()
    assert [c[0] for c in calls[3:]] == ["PowerClearRequest", "PowerClearRequest", "CloseHandle"]
    assert calls[3][1] == (fake_handle, sleep_guard._POWER_REQUEST_DISPLAY_REQUIRED)
    assert calls[4][1] == (fake_handle, sleep_guard._POWER_REQUEST_SYSTEM_REQUIRED)
    assert calls[5][1] == (fake_handle,)


def test_windows_backend_partial_set_and_exception_closes_handle(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, tuple]] = []
    fake_handle = 0x5678

    def _set_partial(handle, req_type):
        calls.append(("PowerSetRequest", (handle, req_type)))
        return 1 if req_type == sleep_guard._POWER_REQUEST_SYSTEM_REQUIRED else 0

    fake_kernel32 = SimpleNamespace(
        PowerCreateRequest=MagicMock(side_effect=lambda *a: (calls.append(("PowerCreateRequest", a)), fake_handle)[1]),
        PowerSetRequest=MagicMock(side_effect=_set_partial),
        PowerClearRequest=MagicMock(side_effect=lambda h, t: (calls.append(("PowerClearRequest", (h, t))), 1)[1]),
        CloseHandle=MagicMock(side_effect=lambda h: (calls.append(("CloseHandle", (h,))), 1)[1]),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: fake_kernel32, raising=False)

    backend = sleep_guard._WindowsBackend()
    assert backend.acquire() is True
    backend.release()
    clear_calls = [c[1] for c in calls if c[0] == "PowerClearRequest"]
    assert clear_calls == [(fake_handle, sleep_guard._POWER_REQUEST_SYSTEM_REQUIRED)]
    assert calls[-1] == ("CloseHandle", (fake_handle,))

    calls.clear()

    def _set_raises(handle, req_type):
        if req_type == sleep_guard._POWER_REQUEST_DISPLAY_REQUIRED:
            raise RuntimeError("boom")
        return 1

    fake_kernel32.PowerSetRequest = MagicMock(side_effect=_set_raises)
    backend2 = sleep_guard._WindowsBackend()
    assert backend2.acquire() is False
    assert ("CloseHandle", (fake_handle,)) in calls
    assert ("PowerClearRequest", (fake_handle, sleep_guard._POWER_REQUEST_SYSTEM_REQUIRED)) in calls


def test_macos_backend_invokes_caffeinate(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sleep_guard.shutil, "which", lambda name: "/usr/bin/caffeinate" if name == "caffeinate" else None)
    popen_args: list[list[str]] = []
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None

    def _fake_popen(cmd, **kwargs):
        popen_args.append(cmd)
        return fake_proc

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    backend = sleep_guard._SubprocessBackend(sleep_guard._macos_cmd)
    assert backend.acquire() is True
    assert popen_args[0][:4] == ["/usr/bin/caffeinate", "-i", "-d", "-w"]
    backend.release()
    fake_proc.terminate.assert_called_once()


def test_linux_backend_invokes_systemd_inhibit_and_handles_fast_exit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        sleep_guard.shutil,
        "which",
        lambda name: "/usr/bin/systemd-inhibit" if name == "systemd-inhibit" else None,
    )
    popen_args: list[list[str]] = []
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None

    def _fake_popen(cmd, **kwargs):
        popen_args.append(cmd)
        return fake_proc

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    backend = sleep_guard._SubprocessBackend(sleep_guard._linux_cmd)
    assert backend.acquire() is True
    assert popen_args[0][0] == "/usr/bin/systemd-inhibit"
    assert "--what=idle:sleep" in popen_args[0]
    backend.release()
    fake_proc.terminate.assert_called_once()

    dead_proc = MagicMock()
    dead_proc.poll.return_value = 1
    dead_proc.returncode = 1
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kwargs: dead_proc)
    backend2 = sleep_guard._SubprocessBackend(sleep_guard._linux_cmd)
    assert backend2.acquire() is False


def test_subprocess_backend_dead_child_detection_and_lock_free_release(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sleep_guard.shutil, "which", lambda name: "/usr/bin/caffeinate" if name == "caffeinate" else None)
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kwargs: fake_proc)

    backend = sleep_guard._SubprocessBackend(sleep_guard._macos_cmd)
    guard = sleep_guard.SleepGuard(backend=backend, enabled=True)
    guard.acquire("t1")
    assert guard.is_inhibiting

    lock_was_held_during_wait = []

    def _check_lock_wait(timeout=None):
        acquired = guard._lock.acquire(blocking=False)
        lock_was_held_during_wait.append(not acquired)
        if acquired:
            guard._lock.release()
        return 0

    fake_proc.wait.side_effect = _check_lock_wait
    guard.release("t1")
    assert lock_was_held_during_wait == [False]
    assert not guard.is_inhibiting

    fake_proc2 = MagicMock()
    fake_proc2.poll.return_value = None
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kwargs: fake_proc2)
    guard.acquire("t2")
    assert guard.is_inhibiting
    fake_proc2.poll.return_value = 1
    assert not guard.is_inhibiting


def test_agent_loop_enter_exit_turn_acquires_and_releases(tmp_path, monkeypatch: pytest.MonkeyPatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"runtime": {"preventSleep": true}}', encoding="utf-8")
    backend = _FakeBackend()
    guard = sleep_guard.SleepGuard(backend=backend, enabled=False)
    monkeypatch.setattr(sleep_guard, "get_sleep_guard", lambda: guard)

    loop = AgentLoop.__new__(AgentLoop)
    loop._active_turns = 0
    loop._turns_idle = asyncio.Event()
    loop._turns_idle.set()
    loop._live_config = LiveConfig(cfg_path)
    loop._land_queued_switch = lambda: None

    loop._enter_turn()
    assert guard.is_inhibiting
    assert guard.active_count == 1
    loop._enter_turn()
    assert guard.active_count == 1
    loop._exit_turn()
    assert guard.is_inhibiting
    loop._exit_turn()
    assert not guard.is_inhibiting
    assert guard.active_count == 0


@pytest.mark.asyncio
async def test_subagent_manager_track_and_track_direct(tmp_path, monkeypatch: pytest.MonkeyPatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"runtime": {"preventSleep": true}}', encoding="utf-8")
    backend = _FakeBackend()
    guard = sleep_guard.SleepGuard(backend=backend, enabled=False)
    monkeypatch.setattr(sleep_guard, "get_sleep_guard", lambda: guard)

    mgr = SubagentManager.__new__(SubagentManager)
    mgr._live_config = LiveConfig(cfg_path)
    mgr._running_tasks = {}
    mgr._session_tasks = {}
    mgr._instance_tasks = {}
    mgr._direct_live = {}

    done = asyncio.Event()

    async def _worker() -> None:
        await done.wait()

    task = asyncio.create_task(_worker())
    mgr._track("t-1", task, "web:s1")
    assert guard.is_inhibiting
    assert guard.active_count == 1
    done.set()
    await task
    await asyncio.sleep(0)
    assert not guard.is_inhibiting
    assert guard.active_count == 0

    async with mgr._hold_instance_slot("web:s1", "raven-code", "default"):
        assert guard.is_inhibiting
        assert guard.active_count == 1
    assert not guard.is_inhibiting
    assert guard.active_count == 0


def test_background_exec_acquires_and_releases_on_exit(tmp_path, monkeypatch: pytest.MonkeyPatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"runtime": {"preventSleep": true}}', encoding="utf-8")
    backend = _FakeBackend()
    guard = sleep_guard.SleepGuard(backend=backend, enabled=False)
    monkeypatch.setattr(sleep_guard, "get_sleep_guard", lambda: guard)
    monkeypatch.setattr("raven.config.live.default_live", lambda: LiveConfig(cfg_path))
    monkeypatch.setattr(background_exec, "_root", lambda: tmp_path)

    cmd = f'"{sys.executable}" -c "import time; time.sleep(0.25)"'
    task = background_exec.start(cmd)
    try:
        assert guard.is_inhibiting
        assert guard.active_count == 1
        deadline = time.monotonic() + 5.0
        while guard.active_count > 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert guard.active_count == 0
        assert not guard.is_inhibiting
    finally:
        background_exec.reap(task.task_id, force=True)
