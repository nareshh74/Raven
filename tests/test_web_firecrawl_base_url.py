"""Firecrawl base URL: env var > config baseUrl > hosted default; custom URL needs no key."""

from __future__ import annotations

import json

import pytest

from raven.agent.tools import web as web_mod
from raven.agent.tools.web import WebSearchTool
from raven.config.schema import WebProvidersConfig


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr("raven.config.loader.get_config_path", lambda: path)
    monkeypatch.delenv("FIRECRAWL_API_BASE_URL", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    def write(base_url: str) -> None:
        path.write_text(json.dumps({"tools": {"web": {"providers": {"firecrawl": {"baseUrl": base_url}}}}}))

    return write


def test_default_is_hosted(cfg):
    assert web_mod._firecrawl_url("/v1/search") == "https://api.firecrawl.dev/v1/search"


def test_config_base_url(cfg):
    cfg("http://localhost:3003/")
    assert web_mod._firecrawl_url("/v1/search") == "http://localhost:3003/v1/search"


def test_env_beats_config(cfg, monkeypatch):
    cfg("http://localhost:3003")
    monkeypatch.setenv("FIRECRAWL_API_BASE_URL", "http://other:1")
    assert web_mod._firecrawl_url("/x") == "http://other:1/x"


def test_custom_base_url_needs_no_key(cfg):
    assert not WebSearchTool(provider="firecrawl").ready
    cfg("http://localhost:3003")
    assert WebSearchTool(provider="firecrawl").ready
    assert WebProvidersConfig.model_validate({"firecrawl": {"baseUrl": "http://x"}}).firecrawl.base_url == "http://x"
