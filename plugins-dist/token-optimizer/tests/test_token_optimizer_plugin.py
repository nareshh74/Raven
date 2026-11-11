"""Unit and integration tests for raven-token-optimizer plugin."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from raven.plugins.context import ServiceLocator
from raven.plugins.discover import DiscoveredPlugin, ManifestOrigin
from raven.plugins.manifest import PluginManifest
from raven.plugins.registry import PluginRegistry
from raven_token_optimizer.plugin import (
    TokenOptimizerTool,
    build_measure_command,
    make_tool,
    resolve_measure_script,
)


def test_manifest_parses_and_activates_in_registry() -> None:
    manifest_path = PLUGIN_ROOT / "raven_token_optimizer" / "raven-plugin.toml"
    manifest = PluginManifest.from_toml_path(manifest_path)
    assert manifest.id == "token-optimizer"
    assert manifest.version == "0.1.0"
    assert [t.name for t in manifest.contributes.tools] == ["token_optimizer"]

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
    assert "token-optimizer" in registry.activated_ids()
    assert "token_optimizer" in registry.tool_names()
    assert registry.tool_plugin_id("token_optimizer") == "token-optimizer"


def test_build_measure_command_variants(tmp_path: Path) -> None:
    fake_script = tmp_path / "measure.py"
    fake_script.write_text("print('ok')\n", encoding="utf-8")

    cmd_scan = build_measure_command(
        fake_script,
        action="scan",
        runtime="claude",
        as_json=True,
        context_size=1000000,
        python_executable="python",
    )
    assert cmd_scan == [
        "python",
        str(fake_script),
        "--runtime",
        "claude",
        "--context-size",
        "1000000",
        "quick",
        "--json",
    ]

    cmd_hermes_doc = build_measure_command(
        fake_script,
        action="doctor",
        runtime="hermes",
        as_json=True,
        python_executable="python",
    )
    assert cmd_hermes_doc == [
        "python",
        str(fake_script),
        "--runtime",
        "hermes",
        "hermes-doctor",
        "--json",
    ]

    cmd_trends = build_measure_command(
        fake_script,
        action="trends",
        days=14,
        as_json=True,
        python_executable="python",
    )
    assert cmd_trends == [
        "python",
        str(fake_script),
        "trends",
        "--json",
        "--days",
        "14",
    ]


def test_build_measure_command_rejects_unknown_action(tmp_path: Path) -> None:
    fake_script = tmp_path / "measure.py"
    fake_script.write_text("print('ok')\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported action"):
        build_measure_command(fake_script, action="rm-rf")


def test_make_tool_respects_enabled_flag_and_script_existence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_script = tmp_path / "measure.py"
    fake_script.write_text("print('ok')\n", encoding="utf-8")

    disabled_ctx = SimpleNamespace(config={"enabled": False, "measure_script": str(fake_script)})
    assert make_tool(disabled_ctx) is None

    enabled_ctx = SimpleNamespace(config={"enabled": True, "measure_script": str(fake_script)})
    tool = make_tool(enabled_ctx)
    assert isinstance(tool, TokenOptimizerTool)
    assert tool.name == "token_optimizer"

    monkeypatch.setattr("raven_token_optimizer.plugin.DEFAULT_MEASURE_CANDIDATES", ())
    monkeypatch.delenv("TOKEN_OPTIMIZER_MEASURE_SCRIPT", raising=False)
    missing_ctx = SimpleNamespace(config={"enabled": True, "measure_script": str(tmp_path / "missing.py")})
    assert make_tool(missing_ctx) is None


@pytest.mark.asyncio
async def test_execute_runs_stub_measure_script(tmp_path: Path) -> None:
    fake_script = tmp_path / "measure.py"
    fake_script.write_text(
        "import json, os, sys\n"
        "print(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}))\n",
        encoding="utf-8",
    )
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()

    tool = TokenOptimizerTool(measure_script=fake_script, default_runtime="codex")
    res = await tool.execute(action="scan", as_json=True, project_path=str(proj_dir))
    assert res.ok is True
    payload = json.loads(res.model_text)
    assert payload["argv"] == ["--runtime", "codex", "quick", "--json"]
    assert Path(payload["cwd"]).resolve() == proj_dir.resolve()


@pytest.mark.asyncio
async def test_execute_handles_invalid_runtime(tmp_path: Path) -> None:
    fake_script = tmp_path / "measure.py"
    fake_script.write_text("print('ok')\n", encoding="utf-8")
    tool = TokenOptimizerTool(measure_script=fake_script)
    res = await tool.execute(action="report", runtime="nonexistent_runtime")
    assert res.ok is False
    assert "Unsupported runtime" in res.model_text


@pytest.mark.asyncio
async def test_execute_live_measure_script_if_present(tmp_path: Path) -> None:
    live_script = resolve_measure_script()
    if live_script is None:
        pytest.skip("Live measure.py not present on this machine")

    manifest_path = PLUGIN_ROOT / "raven_token_optimizer" / "raven-plugin.toml"
    manifest = PluginManifest.from_toml_path(manifest_path)
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
    tool = registry.build_tool(
        "token_optimizer",
        config={"enabled": True, "measure_script": str(live_script)},
        services=ServiceLocator(workspace=tmp_path, user_id="test", agent_id="raven"),
    )
    assert tool is not None

    scan_res = await tool.execute(action="scan", runtime="claude", as_json=True)
    assert scan_res.ok is True
    scan_data = json.loads(scan_res.model_text)
    assert "context_window" in scan_data
    assert "overhead_tokens" in scan_data

    doc_res = await tool.execute(action="doctor", runtime="codex", as_json=True)
    assert doc_res.ok is True
    doc_data = json.loads(doc_res.model_text)
    assert "checks" in doc_data
