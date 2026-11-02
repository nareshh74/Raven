"""Inject session-routed wiki context into Raven turns."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from raven.agent import workdir as raven_workdir
from raven.agent.hook.participant import ParticipantHook
from raven.contracts.participant import AgentParticipant, Intake, StepView

PERSONAL_VAULT = "C:/Users/kumarnaresh/Documents/obsidian-wiki-personal"
WORK_VAULT = "C:/Users/kumarnaresh/Documents/obsidian-wiki-work"
WIKI_CLI = "C:/Users/kumarnaresh/.local/bin/obsidian-wiki.exe"

VAULTS = {
    "personal": PERSONAL_VAULT,
    "work": WORK_VAULT,
}

NO_TOOLS_RE = re.compile(
    r"\b(?:no\s+tools?|without(?:\s+using)?(?:\s+any)?\s+tools?|"
    r"(?:do\s+not|don\x27t|don\u2019t|never|avoid)\s+(?:use|using|call|run)(?:\s+any)?\s+tools?)\b",
    re.I,
)


def _result(scope: str, reason: str, ambiguous: bool = False) -> dict:
    warning = (
        "WARNING: Session scope is ambiguous; using PERSONAL wiki. "
        "Reason: " + reason + ". Review this routing before sharing notes."
    ) if ambiguous else ""
    return {
        "scope": scope,
        "vault": VAULTS[scope],
        "reason": reason,
        "ambiguous": ambiguous,
        "warning": warning,
    }


def configured_enterprise_hosts() -> set[str]:
    """Read host names only; never return credential values from gh configuration."""
    home = Path.home()
    paths = [
        home / ".config/gh/hosts.yml",
        Path(os.environ.get("APPDATA", home)) / "GitHub CLI/hosts.yml",
    ]
    if os.environ.get("GH_CONFIG_DIR"):
        paths.insert(0, Path(os.environ["GH_CONFIG_DIR"]) / "hosts.yml")
    hosts: set[str] = set()
    for path in paths:
        try:
            with path.open(encoding="utf-8") as lines:
                for line in lines:
                    match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9.-]*):\s*", line)
                    if match and match[1].lower() not in {"github.com", "ssh.github.com"}:
                        hosts.add(match[1].lower())
        except OSError:
            continue
    return hosts


def classify_origin(origin: str, *, enterprise_hosts: set[str] | None = None) -> dict:
    """Inspect a URL in memory without storing/returning its credentials or path."""
    host = ""
    if not isinstance(origin, str) or not origin.strip():
        return _result("personal", "origin_missing", True)
    value = origin.strip()
    try:
        if "://" in value:
            parsed = urlsplit(value)
            if parsed.scheme.lower() in {"http", "https", "ssh", "git"}:
                host = (parsed.hostname or "").lower().rstrip(".")
        elif not re.match(r"^[A-Za-z]:[\\/]", value):
            match = re.fullmatch(r"(?:[^@/\s]+@)?([^:/\s]+):.+", value)
            if match:
                host = match[1].lower().rstrip(".")
    except ValueError:
        return _result("personal", "origin_invalid", True)
    return classify_host(host, enterprise_hosts=enterprise_hosts)


def classify_host(host: str, *, enterprise_hosts: set[str] | None = None) -> dict:
    """Use an explicitly recorded session-origin hostname without inventing a URL."""
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        return _result("personal", "origin_invalid", True)
    host = host.lower().rstrip(".")
    if host in {"github.com", "ssh.github.com", "www.github.com"}:
        return _result("personal", "public_github_origin")
    if host in {"dev.azure.com", "ssh.dev.azure.com"} or host.endswith(".visualstudio.com"):
        return _result("work", "azure_devops_origin")
    enterprise_hosts = configured_enterprise_hosts() if enterprise_hosts is None else enterprise_hosts
    if host.endswith(".ghe.com") or host in enterprise_hosts:
        return _result("work", "github_enterprise_origin")
    return _result("personal", "origin_unrecognized", True)


def classify_session(
    cwd: str | Path | None,
    *,
    origin: str | None = None,
    enterprise_hosts: set[str] | None = None,
) -> dict:
    """Route from the session CWD or an explicitly recorded session origin."""
    if origin is not None:
        return classify_origin(origin, enterprise_hosts=enterprise_hosts)
    if not isinstance(cwd, (str, Path)) or not cwd or not Path(cwd).is_dir():
        return _result("personal", "session_directory_unavailable", True)
    env = os.environ.copy()
    env.update(LC_ALL="C", GIT_TERMINAL_PROMPT="0")
    try:
        probe = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=1,
            env=env,
        )
        if probe.returncode:
            if "not a git repository" in probe.stderr.lower():
                return _result("personal", "non_git_session")
            return _result("personal", "git_inspection_failed", True)
        remote = subprocess.run(
            ["git", "-C", probe.stdout.strip(), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=1,
            env=env,
        )
        if remote.returncode:
            return _result("personal", "origin_missing", True)
        return classify_origin(remote.stdout.strip(), enterprise_hosts=enterprise_hosts)
    except (OSError, subprocess.TimeoutExpired):
        return _result("personal", "git_inspection_unavailable", True)


def _route(cwd: str | Path | None) -> tuple[str, str, bool, str]:
    info = classify_session(cwd)
    return info["scope"], info["vault"], bool(info["ambiguous"]), info["reason"]


def _resolve_cwd(step: StepView | None = None) -> str:
    bound = raven_workdir.current()
    if bound is not None:
        return str(bound)
    if step is not None:
        overlay = getattr(step, "mode_overlay", None)
        if isinstance(overlay, dict) and overlay.get("cwd"):
            return str(overlay["cwd"])
    return os.environ.get("RAVEN_WORKDIR") or os.getcwd()


def _is_no_tools(step: StepView | None = None, text: str = "") -> bool:
    q = getattr(step, "question", "") if step is not None else ""
    if isinstance(q, str) and q and NO_TOOLS_RE.search(q):
        return True
    if step is not None and not q:
        for msg in getattr(step, "transcript", ()) or ():
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and content and NO_TOOLS_RE.search(content):
                    return True
    if isinstance(text, str) and text and NO_TOOLS_RE.search(text):
        return True
    return False


def _route_instruction(kind: str, vault: str, ambiguous: bool, reason: str) -> str:
    lines = [
        f"VAULT_KIND={kind} VAULT_PATH={vault}",
        f'Use --vault "{vault}" for obsidian-wiki memory and context-pack, and positional "{vault}" for lint, graph-query, cache-update, and code-understand.',
        "Use wiki skills for workflow guidance, obsidian-wiki.exe through exec for CLI operations, and file tools for note reads or edits. Do not invoke wiki-* skills through tool_call.",
        "Keep personal and work vaults separate.",
    ]
    if ambiguous:
        lines.extend(
            [
                f"AMBIGUOUS=true. Routing warning: {reason}. Tell the user this session uses the personal vault because routing is ambiguous.",
                "For authorized saved notes, set routing_ambiguous: true and add a prominent > [!warning] banner with the non-secret reason and source session ID. Never include remote credentials.",
            ]
        )
    return "\n".join(lines)


class ObsidianWikiParticipant(AgentParticipant):
    """Provide one cached recap and the source-session vault route per turn."""

    def __init__(self) -> None:
        self._cached_cwd: str | None = None
        self._route: tuple[str, str, bool, str] | None = None
        self._addendum_text: str | None = None
        self._no_tools: bool = False

    def _current_route(self, step: StepView | None = None) -> tuple[str, str, bool, str]:
        cwd = _resolve_cwd(step)
        if self._route is None or self._cached_cwd != cwd:
            self._cached_cwd = cwd
            self._route = _route(cwd)
            self._addendum_text = None
        return self._route

    async def system_addendum(self, step: StepView):
        if _is_no_tools(step):
            self._no_tools = True
            return None
        kind, vault, ambiguous, reason = self._current_route(step)
        if self._addendum_text is None:
            try:
                env = os.environ.copy()
                env.setdefault("PYTHONUTF8", "1")
                env.setdefault("PYTHONIOENCODING", "utf-8")
                recap = subprocess.run(
                    [WIKI_CLI, "memory", "recap", "--vault", vault],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    timeout=15,
                )
                recap_text = (recap.stdout or "").strip()
                if recap.returncode:
                    recap_block = (
                        "[WIKI_BUNDLED_HOOK event=SessionStart status=error]\n"
                        f"obsidian-wiki memory recap exited with status {recap.returncode}."
                    )
                    if recap.stderr.strip():
                        recap_block += f"\n{recap.stderr.strip()}"
                    recap_block += "\n[/WIKI_BUNDLED_HOOK]"
                else:
                    recap_block = "[WIKI_BUNDLED_HOOK event=SessionStart status=ok]"
                    if recap_text:
                        recap_block += f"\n{recap_text}"
                    recap_block += "\n[/WIKI_BUNDLED_HOOK]"
            except (OSError, subprocess.TimeoutExpired) as exc:
                recap_block = (
                    "[WIKI_BUNDLED_HOOK event=SessionStart status=error]\n"
                    f"obsidian-wiki memory recap failed: {exc}\n"
                    "[/WIKI_BUNDLED_HOOK]"
                )
            route_block = (
                "[WIKI_SESSION_ROUTE]\n"
                + _route_instruction(kind, vault, ambiguous, reason)
                + "\n[/WIKI_SESSION_ROUTE]"
            )
            self._addendum_text = f"{recap_block}\n\n{route_block}"
        return Intake(text=self._addendum_text)

    async def intake(self, text: str, step: StepView):
        if _is_no_tools(step, text):
            self._no_tools = True
            return None
        if "[WIKI_SESSION_ROUTE]" in text:
            return None
        kind, vault, ambiguous, reason = self._current_route(step)
        block = (
            "[WIKI_SESSION_ROUTE]\n"
            + _route_instruction(kind, vault, ambiguous, reason)
            + "\n[/WIKI_SESSION_ROUTE]"
        )
        return Intake(text=f"{text}\n\n{block}")

    async def archive(self, step: StepView, reply: str | None):
        if self._no_tools or _is_no_tools(step):
            return None
        kind, vault, _, _ = self._current_route(step)
        return {"obsidian-wiki": {"vault_kind": kind, "vault_path": vault}}


def make_hook(ctx):
    if (ctx.config or {}).get("enabled") is not True:
        return None
    return ParticipantHook("obsidian_wiki", ObsidianWikiParticipant)


def register(ctx):
    """Entry-point callable; the manifest factory owns Raven activation."""
    return make_hook(ctx)
