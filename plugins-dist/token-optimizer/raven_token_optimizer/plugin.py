"""Native Raven tool wrapper around Token Optimizer's measure.py."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from raven.agent import workdir as raven_workdir
from raven.contracts.tool import Tool, ToolResult

DEFAULT_MEASURE_CANDIDATES = (
    Path("C:/learn/tools/token-optimizer/skills/token-optimizer/scripts/measure.py"),
    Path.home() / ".claude/token-optimizer/skills/token-optimizer/scripts/measure.py",
)

SUPPORTED_RUNTIMES = frozenset({
    "antigravity",
    "claude",
    "codex",
    "copilot",
    "cursor",
    "devin",
    "grok",
    "hermes",
    "opencode",
})

SUPPORTED_ACTIONS = (
    "report",
    "scan",
    "quick",
    "doctor",
    "health",
    "trends",
    "coach",
    "drift",
    "codex-doctor",
    "copilot-doctor",
    "hermes-doctor",
    "hermes-summary",
)


def resolve_measure_script(configured_path: str | Path | None = None) -> Path | None:
    """Resolve the Token Optimizer measure.py script path."""
    candidates: list[Path] = []
    if configured_path:
        candidates.append(Path(configured_path).expanduser())
    env_override = os.environ.get("TOKEN_OPTIMIZER_MEASURE_SCRIPT", "").strip()
    if env_override:
        candidates.append(Path(env_override).expanduser())
    candidates.extend(DEFAULT_MEASURE_CANDIDATES)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _resolve_cwd(project_path: str | None = None) -> str:
    if isinstance(project_path, str) and project_path.strip():
        target = Path(project_path).expanduser()
        if target.is_dir():
            return str(target)
    bound = raven_workdir.current()
    if bound is not None and Path(bound).is_dir():
        return str(bound)
    env_dir = os.environ.get("RAVEN_WORKDIR", "").strip()
    if env_dir and Path(env_dir).is_dir():
        return env_dir
    return os.getcwd()


def _normalize_runtime(runtime: str | None, default_runtime: str | None = None) -> str | None:
    raw = (runtime or default_runtime or "").strip().lower()
    if not raw or raw == "auto":
        return None
    if raw not in SUPPORTED_RUNTIMES:
        raise ValueError(
            f"Unsupported runtime {raw!r}; choose one of: {', '.join(sorted(SUPPORTED_RUNTIMES))}",
        )
    return raw


def build_measure_command(
    script_path: Path,
    *,
    action: str = "report",
    runtime: str | None = None,
    as_json: bool = False,
    days: int | None = None,
    context_size: int | None = None,
    extra_args: list[str] | None = None,
    python_executable: str | None = None,
) -> list[str]:
    """Construct the measure.py CLI argv list."""
    act = (action or "report").strip().lower()
    if act not in SUPPORTED_ACTIONS:
        raise ValueError(
            f"Unsupported action {act!r}; choose one of: {', '.join(SUPPORTED_ACTIONS)}",
        )
    subcommand = "quick" if act == "scan" else act
    if subcommand == "doctor" and runtime == "hermes":
        subcommand = "hermes-doctor"
    elif subcommand == "doctor" and runtime == "copilot":
        subcommand = "copilot-doctor"

    cmd: list[str] = [python_executable or sys.executable, str(script_path)]
    if runtime:
        cmd.extend(["--runtime", runtime])
    if context_size is not None and int(context_size) > 0:
        cmd.extend(["--context-size", str(int(context_size))])
    cmd.append(subcommand)
    if as_json and subcommand in {
        "quick",
        "doctor",
        "codex-doctor",
        "copilot-doctor",
        "hermes-doctor",
        "trends",
        "coach",
    }:
        cmd.append("--json")
    if days is not None and int(days) > 0 and subcommand == "trends":
        cmd.extend(["--days", str(int(days))])
    if extra_args:
        for arg in extra_args:
            if isinstance(arg, str) and arg.strip():
                cmd.append(arg.strip())
    return cmd


def _run_subprocess(cmd: list[str], cwd: str, timeout: float = 45.0) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(
        cmd,
        cwd=cwd,
         env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


class TokenOptimizerTool(Tool):
    """Native Raven tool exposing Token Optimizer diagnostics and reports."""

    timeout_seconds = 60.0

    def __init__(
        self,
        *,
        measure_script: str | Path | None = None,
        default_runtime: str | None = None,
    ) -> None:
        self._configured_script = measure_script
        self._default_runtime = default_runtime

    @property
    def name(self) -> str:
        return "token_optimizer"

    @property
    def description(self) -> str:
        return (
            "Run Token Optimizer diagnostics, quick overhead scans, full token reports, "
            "doctor health checks, and usage trends across runtimes (claude, codex, copilot, "
            "hermes, etc.) or project paths."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(SUPPORTED_ACTIONS),
                    "description": (
                        "Action to run: 'report' (full overhead report), 'scan' or 'quick' "
                        "(startup overhead + top offenders + quick wins), 'doctor' "
                        "(component/hook health check), 'trends', 'coach', 'health', or 'drift'."
                    ),
                },
                "runtime": {
                    "type": "string",
                    "description": (
                        "Optional target runtime (e.g. 'claude', 'codex', 'copilot', 'hermes'). "
                        "Defaults to auto-detection or configured default_runtime."
                    ),
                },
                "project_path": {
                    "type": "string",
                    "description": "Optional project directory to run the scan/check against.",
                },
                "as_json": {
                    "type": "boolean",
                    "description": "Return machine-readable JSON output when supported by the action.",
                },
                "days": {
                    "type": "integer",
                    "description": "Optional day window for 'trends' (e.g. 7, 14, 30).",
                },
                "context_size": {
                    "type": "integer",
                    "description": "Optional context window size override in tokens (e.g. 1000000).",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str = "report",
        runtime: str | None = None,
        project_path: str | None = None,
        as_json: bool = False,
        days: int | None = None,
        context_size: int | None = None,
        **_: Any,
    ) -> str | ToolResult:
        script = resolve_measure_script(self._configured_script)
        if script is None:
            return ToolResult(
                model_text=(
                    "Error: Token Optimizer measure.py was not found. Configure "
                    "plugins.config['token-optimizer'].measure_script or set "
                    "TOKEN_OPTIMIZER_MEASURE_SCRIPT."
                ),
                ok=False,
            )

        try:
            resolved_runtime = _normalize_runtime(runtime, self._default_runtime)
            cmd = build_measure_command(
                script,
                action=action,
                runtime=resolved_runtime,
                as_json=bool(as_json),
                days=days,
                context_size=context_size,
            )
        except ValueError as exc:
            return ToolResult(model_text=f"Error: {exc}", ok=False)

        cwd = _resolve_cwd(project_path)
        try:
            proc = await asyncio.to_thread(_run_subprocess, cmd, cwd)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ToolResult(
                model_text=f"Error running Token Optimizer ({action}): {exc}",
                ok=False,
            )

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        output_parts = [p for p in (stdout, stderr) if p]
        combined = "\n".join(output_parts) if output_parts else f"Token Optimizer ({action}) produced no output."

        # Doctor commands may return exit code 1 when a check has FAIL status,
        # while still emitting valid diagnostic output. Treat non-empty doctor output as ok.
        is_doctor = action.strip().lower() in {"doctor", "codex-doctor", "copilot-doctor", "hermes-doctor"}
        ok = proc.returncode == 0 or (is_doctor and bool(stdout))
        return ToolResult(
            model_text=combined,
            display_text=f"token_optimizer {action}" + (f" ({resolved_runtime})" if resolved_runtime else ""),
            ok=ok,
        )


def make_tool(ctx: Any) -> Tool | None:
    """Plugin tool-factory entry point (manifest ``contributes.tools``)."""
    cfg = getattr(ctx, "config", None) or {}
    if cfg.get("enabled") is False:
        return None
    measure_script = cfg.get("measure_script")
    default_runtime = cfg.get("default_runtime")
    if resolve_measure_script(measure_script) is None:
        return None
    return TokenOptimizerTool(
        measure_script=measure_script,
        default_runtime=default_runtime,
    )


def register(ctx: Any) -> Tool | None:
    """Entry-point callable; manifest factory owns Raven activation."""
    return make_tool(ctx)


__all__ = [
    "SUPPORTED_ACTIONS",
    "SUPPORTED_RUNTIMES",
    "TokenOptimizerTool",
    "build_measure_command",
    "make_tool",
    "register",
    "resolve_measure_script",
]
