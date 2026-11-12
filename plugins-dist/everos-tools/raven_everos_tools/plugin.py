"""Unified EverOS recall and retain tools for Raven."""

from __future__ import annotations

from typing import Any

from raven.contracts.tool import Tool


class EverosRecallTool(Tool):
    """Search unified long-term EverOS memory."""

    def __init__(self, *, base_url: str = "http://127.0.0.1:8000", user_id: str = "naresh") -> None:
        self._base_url = base_url.rstrip("/")
        self._user_id = user_id or "naresh"

    @property
    def name(self) -> str:
        return "everos_recall"

    @property
    def description(self) -> str:
        return (
            "Search unified long-term EverOS memory (shared across all harnesses) "
            "for relevant facts, preferences, decisions, or past experiences."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query describing what to recall from EverOS memory.",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Maximum token budget for recalled memories (default 1024).",
                },
            },
            "required": ["query"],
        }

    async def execute(self, query: str = "", max_tokens: int = 1024, **_: Any) -> str:
        import json
        import httpx

        del max_tokens
        q = (query or "").strip()
        if not q:
            return json.dumps({"error": "'query' is required."})
        url = f"{self._base_url}/api/v2/memory/search"
        last_err = "unknown error"
        for method in ("hybrid", "keyword"):
            payload = {
                "user_id": self._user_id,
                "app_id": "shared",
                "project_id": "default",
                "query": q,
                "method": method,
                "top_k": 10,
                "include_profile": True,
            }
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                    data = (resp.json() or {}).get("data") or {}
                episodes = data.get("episodes") or []
                lines = []
                for ep in episodes[:10]:
                    if isinstance(ep, dict):
                        subj = (ep.get("subject") or "").strip()
                        summ = (ep.get("summary") or ep.get("episode") or "").strip()
                        app_id = ep.get("app_id") or "default"
                        lines.append(f"- [{app_id}] {subj}: {summ}" if subj else f"- [{app_id}] {summ}")
                return json.dumps({"results": "\n".join(lines), "count": len(episodes)})
            except Exception as e:
                last_err = str(e)
        return json.dumps({"error": last_err})


class EverosRetainTool(Tool):
    """Store a durable fact, preference, or decision into EverOS memory."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8000",
        user_id: str = "naresh",
        agent_id: str = "raven",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._user_id = user_id or "naresh"
        self._agent_id = agent_id or "raven"

    @property
    def name(self) -> str:
        return "everos_retain"

    @property
    def description(self) -> str:
        return (
            "Save a durable fact, user preference, architectural decision, or verified finding "
            "to unified long-term EverOS memory."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The fact, preference, or decision to store in EverOS.",
                },
                "context": {
                    "type": "string",
                    "description": "Optional context label (default 'raven').",
                },
            },
            "required": ["content"],
        }

    async def execute(self, content: str = "", context: str = "raven", **_: Any) -> str:
        import json
        import time
        import uuid
        import httpx

        text = (content or "").strip()
        if not text:
            return json.dumps({"error": "'content' is required."})
        now_ms = int(time.time() * 1000)
        ses = f"raven-retain-{now_ms}-{uuid.uuid4().hex[:8]}"
        tagged = f"[{context}] {text}" if context else text
        add_body = {
            "session_id": ses,
            "app_id": "raven",
            "project_id": "default",
            "defer_extraction": True,
            "messages": [
                {"sender_id": self._user_id, "role": "user", "timestamp": now_ms, "content": tagged[:4000]},
                {"sender_id": "assistant", "role": "assistant", "timestamp": now_ms + 1, "content": "Stored durable fact in EverOS."},
            ],
        }
        flush_body = {"session_id": ses, "app_id": "raven", "project_id": "default"}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                r1 = await client.post(f"{self._base_url}/api/v2/memory/add", json=add_body)
                r1.raise_for_status()
                r2 = await client.post(f"{self._base_url}/api/v2/memory/flush", json=flush_body)
                r2.raise_for_status()
                return json.dumps({"status": "retained", "session_id": ses, "agent_id": self._agent_id})
        except Exception as e:
            return json.dumps({"error": str(e)})


def make_everos_recall_tool(ctx: Any) -> Tool:
    cfg = getattr(ctx, "config", None) or {}
    base_url = str(cfg.get("base_url") or "http://127.0.0.1:8000")
    user_id = str(cfg.get("user_id") or getattr(getattr(ctx, "services", None), "user_id", None) or "naresh")
    return EverosRecallTool(base_url=base_url, user_id=user_id)


def make_everos_retain_tool(ctx: Any) -> Tool:
    cfg = getattr(ctx, "config", None) or {}
    base_url = str(cfg.get("base_url") or "http://127.0.0.1:8000")
    user_id = str(cfg.get("user_id") or getattr(getattr(ctx, "services", None), "user_id", None) or "naresh")
    agent_id = str(cfg.get("agent_id") or getattr(getattr(ctx, "services", None), "agent_id", None) or "raven")
    return EverosRetainTool(base_url=base_url, user_id=user_id, agent_id=agent_id)


def register(ctx: Any) -> Tool:
    return make_everos_recall_tool(ctx)
