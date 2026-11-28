"""Custom Firecrawl URLs accept keyless calls without an empty Authorization header."""

from __future__ import annotations

import json

import httpx
import pytest

from raven.agent.tools import web as web_mod
from raven.agent.tools.web import WebFetchTool, WebSearchTool
from raven.config.schema import WebProvidersConfig


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr("raven.config.loader.get_config_path", lambda: path)
    monkeypatch.delenv("FIRECRAWL_API_BASE_URL", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_URL", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    def write(base_url: str) -> None:
        path.write_text(json.dumps({"tools": {"web": {"providers": {"firecrawl": {"baseUrl": base_url}}}}}))

    return write


def test_default_is_hosted(cfg):
    assert web_mod._firecrawl_url("/v2/search") == "https://api.firecrawl.dev/v2/search"


def test_config_base_url(cfg):
    cfg("http://localhost:3003/")
    assert web_mod._firecrawl_url("/v2/search") == "http://localhost:3003/v2/search"


def test_env_beats_config(cfg, monkeypatch):
    cfg("http://localhost:3003")
    monkeypatch.setenv("FIRECRAWL_API_BASE_URL", "http://other:1")
    assert web_mod._firecrawl_url("/x") == "http://other:1/x"


def test_api_url_alias(cfg, monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_URL", "http://localhost:3002/")
    assert web_mod._firecrawl_url("/v2/search") == "http://localhost:3002/v2/search"
    assert WebSearchTool(provider="firecrawl").ready


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", "test-key"])
@pytest.mark.parametrize("operation", ["search", "scrape"])
async def test_firecrawl_omits_empty_authorization(cfg, monkeypatch, key, operation):
    cfg("http://localhost:3002")
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"success": True, "data": {"web": [], "markdown": "Test page content"}},
        )

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        web_mod.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs),
    )
    if operation == "search":
        async with httpx.AsyncClient() as client:
            await WebSearchTool(provider="firecrawl")._provider_request(client, "test", 1, "firecrawl", key)
    else:
        await WebFetchTool(provider="firecrawl")._provider_fetch("https://example.com", "firecrawl", key)

    assert len(requests) == 1
    assert str(requests[0].url) == f"http://localhost:3002/v2/{operation}"
    assert requests[0].headers.get("Authorization") == (f"Bearer {key}" if key else None)


def test_custom_base_url_needs_no_key(cfg):
    assert not WebSearchTool(provider="firecrawl").ready
    cfg("http://localhost:3003")
    assert WebSearchTool(provider="firecrawl").ready
    assert WebProvidersConfig.model_validate({"firecrawl": {"baseUrl": "http://x"}}).firecrawl.base_url == "http://x"


def test_inherit_web_vendors_treats_custom_firecrawl_base_url_as_payable(monkeypatch):
    from raven.config.product_render import inherit_web_vendors

    monkeypatch.delenv("FIRECRAWL_API_BASE_URL", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_URL", raising=False)
    config: dict = {"tools": {"web": {}}}
    host = {
        "tools": {
            "web": {
                "search": {"provider": "firecrawl"},
                "fetch": {"provider": "firecrawl"},
                "providers": {"firecrawl": {"apiKey": "", "baseUrl": "http://127.0.0.1:3002"}},
            }
        }
    }
    inherit_web_vendors(config, host, lookup=lambda _name: None)
    assert config["tools"]["web"]["search"]["provider"] == "firecrawl"
    assert config["tools"]["web"]["fetch"]["provider"] == "firecrawl"
    assert config["tools"]["web"]["providers"]["firecrawl"]["baseUrl"] == "http://127.0.0.1:3002"


def test_research_flow_firecrawl_custom_base_url_keyless(cfg, tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    plugin_dir = str(Path(__file__).resolve().parents[1] / "agents" / "raven-research" / "plugins" / "research-flow")
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)

    from research_flow.plugin import make_web_fetch, make_web_search
    from research_flow.tools import web as rf_web

    assert rf_web._firecrawl_url("/v2/search", "http://127.0.0.1:3002/") == "http://127.0.0.1:3002/v2/search"
    assert rf_web._firecrawl_url("/v2/scrape", "http://127.0.0.1:3002/") == "http://127.0.0.1:3002/v2/scrape"

    ctx = SimpleNamespace(
        config={
            "enabled": True,
            "sufficiency": {"enabled": False},
            "search": {"provider": "firecrawl", "baseUrl": "http://127.0.0.1:3002"},
            "fetch": {"provider": "firecrawl", "baseUrl": "http://127.0.0.1:3002"},
        },
        services=SimpleNamespace(workspace=tmp_path, provider=SimpleNamespace()),
    )
    search_tool = make_web_search(ctx)
    fetch_tool = make_web_fetch(ctx)
    assert search_tool is not None
    assert search_tool.provider == "firecrawl"
    assert search_tool.ready
    assert fetch_tool.provider == "firecrawl"
    assert rf_web.WebFetchTool.effective_provider("firecrawl", None, base_url="http://127.0.0.1:3002") == "firecrawl"
