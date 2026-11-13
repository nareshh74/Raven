"""Unit tests for raven-everos-tools plugin and subagent skill_forge catalog wiring."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dist" / "everos-tools"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from raven.agent.subagent.backends.raven_loop import RavenLoopBackend, build_subagent_prompt
from raven.agent.subagent.dag_tool import SubAgentDagTool
from raven.plugins.discover import DiscoveredPlugin, ManifestOrigin
from raven.plugins.manifest import PluginManifest
from raven.plugins.registry import PluginRegistry
from raven_everos_tools.plugin import (
    EverosRecallTool,
    EverosRetainTool,
    make_everos_recall_tool,
    make_everos_retain_tool,
)


def test_everos_tools_manifest_and_activation() -> None:
    manifest_path = PLUGIN_ROOT / "raven_everos_tools" / "raven-plugin.toml"
    manifest = PluginManifest.from_toml_path(manifest_path)
    assert manifest.id == "everos-tools"
    assert manifest.version == "0.1.0"
    assert [t.name for t in manifest.contributes.tools] == ["everos_recall", "everos_retain"]

    registry = PluginRegistry()
    registry.activate(
        [
            DiscoveredPlugin(
                manifest=manifest,
                source=ManifestOrigin.PROJECT,
                location=manifest_path,
            )
        ]
    )
    assert "everos-tools" in registry.activated_ids()
    assert {"everos_recall", "everos_retain"} <= set(registry.tool_names())
    assert registry.tool_plugin_id("everos_recall") == "everos-tools"
    assert registry.tool_plugin_id("everos_retain") == "everos-tools"

    ctx = SimpleNamespace(
        config={"base_url": "http://127.0.0.1:8000", "user_id": "tester", "agent_id": "bot"},
        services=None,
    )
    recall_tool = make_everos_recall_tool(ctx)
    retain_tool = make_everos_retain_tool(ctx)
    assert isinstance(recall_tool, EverosRecallTool)
    assert isinstance(retain_tool, EverosRetainTool)
    assert recall_tool.name == "everos_recall"
    assert retain_tool.name == "everos_retain"


@pytest.mark.asyncio
async def test_everos_tools_validation_errors_on_empty_input() -> None:
    recall_tool = EverosRecallTool(base_url="http://127.0.0.1:8000", user_id="tester")
    retain_tool = EverosRetainTool(base_url="http://127.0.0.1:8000", user_id="tester", agent_id="bot")
    res_recall = json.loads(await recall_tool.execute(query="   "))
    res_retain = json.loads(await retain_tool.execute(content="   "))
    assert "error" in res_recall
    assert "error" in res_retain


def test_subagent_skill_catalog_passes_live_skill_forge_config(tmp_path: Path) -> None:
    assert RavenLoopBackend._MAX_ITERATIONS == 500
    captured_configs: list[object] = []
    sentinel_sf = SimpleNamespace(enabled=False, always_max=20, local_dirs=[], blocklist=[])
    fake_cfg = SimpleNamespace(skill_forge=sentinel_sf)

    class _FakeCatalog:
        def __init__(self, workspace: Path, config: object = None, **kwargs: object) -> None:
            captured_configs.append(config)
            self.registry = SimpleNamespace(list_all=lambda: [])

        def build_skills_summary(self, only: object = None) -> str:
            return ""

    with (
        patch("raven.config.live.LiveConfig.get", return_value=fake_cfg),
        patch("raven.memory_engine.LocalSkillCatalog", _FakeCatalog),
    ):
        build_subagent_prompt(tmp_path, tmp_path, frozenset())
        dag = SubAgentDagTool.__new__(SubAgentDagTool)
        dag._workspace = str(tmp_path)
        dag._skill_catalog()

    assert captured_configs == [sentinel_sf, sentinel_sf]
