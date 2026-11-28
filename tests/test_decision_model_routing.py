"""Decision coordinator deadlines, isolation, live reload and fallback contracts."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from loguru import logger

from raven.agent import decision_model as dm
from raven.config.live import LiveConfig, decision_settings, hold_for_this_turn
from raven.config.raven import DecisionModelConfig
from raven.providers.binding import ModelBinding


@pytest.fixture(autouse=True)
def circuits():
    dm._CIRCUITS.clear()
    dm._WARNED.clear()


def binding(chat, model="alias"):
    return ModelBinding(SimpleNamespace(chat=chat), model)


@pytest.mark.asyncio
async def test_circuit_opens_after_three_failures_and_is_scoped():
    calls = []

    async def broken(*args, **kwargs):
        calls.append(1)
        raise OSError("unavailable")

    b = binding(broken)
    for _ in range(4):
        assert await dm.select_skills_with_decision_model(b, "task", [("a", "desc")]) is None
    assert len(calls) == 3
    assert await dm.select_specialist_with_decision_model(b, "task", "roster", ["agent"]) is None
    assert len(calls) == 4
    assert await dm.select_skills_with_decision_model(binding(broken, "another"), "task", [("a", "desc")]) is None
    assert len(calls) == 5


@pytest.mark.asyncio
async def test_half_open_has_one_probe_and_recovers():
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def chat(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls <= 3:
            raise OSError("unavailable")
        started.set()
        await release.wait()
        return SimpleNamespace(content='{"selected":["0"]}')

    b = binding(chat)
    for _ in range(3):
        await dm.select_skills_with_decision_model(b, "task", [("a", "desc")])
    state = next(iter(dm._CIRCUITS.values()))
    state.opened_at -= 31
    probe = asyncio.create_task(dm.select_skills_with_decision_model(b, "task", [("a", "desc")]))
    await started.wait()
    assert await dm.select_skills_with_decision_model(b, "task", [("a", "desc")]) is None
    release.set()
    assert await probe == ["a"]
    assert state.failures == 0 and state.opened_at is None
    assert calls == 4


@pytest.mark.asyncio
async def test_timeout_cancels_the_single_attempt():
    cancelled = asyncio.Event()

    async def slow(*args, **kwargs):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    assert await dm.select_skills_with_decision_model(binding(slow), "task", [("a", "desc")], timeout=0.01) is None
    assert cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("output", [
    '{"selected":["99"]}', '{"selected":["0","0"]}', '{"selected":[1]}',
    '{"selected":["0","1"]}', "not json",
])
async def test_invalid_indices_and_cardinality_fail_open(output):
    async def chat(*args, **kwargs):
        return SimpleNamespace(content=output)

    assert await dm.select_skills_with_decision_model(
        binding(chat), "task", [("a", "desc"), ("b", "desc")], max_skills=1,
    ) is None


@pytest.mark.asyncio
async def test_success_and_failure_telemetry_never_logs_query():
    events = []
    sink = logger.add(lambda message: events.append(message.record))

    async def chat(*args, **kwargs):
        return SimpleNamespace(content='{"selected":["0"]}')

    try:
        b = binding(chat)
        assert await dm.select_skills_with_decision_model(b, "secret-query", [("a", "desc")]) == ["a"]
        success = next(e for e in events if e["extra"].get("event") == "decision_model.routed")
        assert success["level"].name == "INFO"
        assert {"provider", "model", "protocol", "latency_ms", "selected", "confidence"} <= success["extra"].keys()
        assert any(e["extra"].get("event") == "decision_model.protocol_auto_chat" for e in events)
        assert "secret-query" not in repr(events)
    finally:
        logger.remove(sink)


@pytest.mark.asyncio
async def test_specialist_and_explicit_none():
    async def chat(*args, **kwargs):
        return SimpleNamespace(content='{"selected":["1"]}')

    assert await dm.select_specialist_with_decision_model(binding(chat), "task", "roster", ["one", "two"]) == "two"

    async def none(*args, **kwargs):
        return SimpleNamespace(content='{"selected":[]}')

    assert await dm.select_skills_with_decision_model(binding(none), "task", [("a", "desc")]) == []


def test_live_settings_are_atomic_held_and_last_good(tmp_path):
    path = tmp_path / "config.json"

    def write(value):
        path.write_text(json.dumps({"decisionModel": value}), encoding="utf-8")

    write({"model": "first", "protocol": "chat", "timeoutSeconds": 1.0})
    live = LiveConfig(path)
    with hold_for_this_turn():
        assert decision_settings(live).model == "first"
        write({"model": "next", "protocol": "criteria_map", "endpointPath": "/route"})
        assert decision_settings(live).model == "first"
    assert decision_settings(live).model == "next"
    write({"model": "broken", "timeoutSeconds": "fast"})
    assert decision_settings(live).model == "next"


def test_provider_metadata_selects_dialect_without_names():
    from raven.config.schema import Config
    from raven.providers.pool import ProviderPool

    cfg = Config.model_validate({"providers": {"custom": {
        "apiKey": "test", "apiBase": "https://gateway.invalid",
        "modelOverlay": {"renamed": {"decision": {"protocolId": "choices_array"}}},
    }}})
    pool = ProviderPool(cfg)
    b = ModelBinding(SimpleNamespace(), "renamed", provider_name="custom", pool=pool)
    assert pool.decision_capability(b).protocol_id == "choices_array"


@pytest.mark.asyncio
async def test_pool_transport_reuses_client_and_closes_it():
    from raven.config.schema import Config
    from raven.providers.pool import ProviderPool

    async def credentials():
        return "https://gateway.invalid/v1", {"Authorization": "Bearer test"}

    pool = ProviderPool(Config())
    b = ModelBinding(SimpleNamespace(decision_credentials=credentials), "alias", pool=pool,
                     decision_config=DecisionModelConfig(endpoint_path="/native"))
    first = await pool.decision_transport(b)
    second = await pool.decision_transport(b)
    assert first.client is second.client
    assert first.endpoint_path == "/native"
    await pool.aclose()
    assert first.client.is_closed


@pytest.mark.asyncio
async def test_unset_protocol_never_uses_native_capability():
    async def chat(*args, **kwargs):
        return SimpleNamespace(content='{"selected":["0"]}')

    def unexpected_capability(*args):
        raise AssertionError("unset protocol must not consult native metadata")

    pool = SimpleNamespace(decision_capability=unexpected_capability)
    b = ModelBinding(
        SimpleNamespace(chat=chat), "renamed", pool=pool,
        decision_config=DecisionModelConfig(model="renamed"),
    )
    assert await dm.select_skills_with_decision_model(b, "task", [("allowed", "desc")]) == ["allowed"]


@pytest.mark.asyncio
async def test_native_auth_is_inside_the_same_deadline():
    cancelled = asyncio.Event()

    async def transport(_):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    pool = SimpleNamespace(
        decision_capability=lambda _: SimpleNamespace(protocol_id=None),
        decision_transport=transport,
    )
    b = ModelBinding(
        SimpleNamespace(), "arbitrary", pool=pool,
        decision_config=DecisionModelConfig(protocol="criteria_map"),
    )
    assert await dm.select_skills_with_decision_model(b, "task", [("a", "desc")], timeout=0.01) is None
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_lazy_chat_support_is_checked_on_the_effective_provider():
    from raven.config.schema import Config
    from raven.providers.pool import ProviderPool

    async def chat(messages, *, model, temperature, max_tokens, tools, tool_choice):
        assert tool_choice["function"]["name"] == "record_decision"
        return SimpleNamespace(
            tool_calls=[SimpleNamespace(name="record_decision", arguments={"selected": ["0"]})],
        )

    concrete = SimpleNamespace(chat=chat)

    async def forward(*args, **kwargs):
        return await concrete.chat(*args, **kwargs)

    lazy = SimpleNamespace(_built=lambda: concrete, chat=forward)
    b = ModelBinding(lazy, "any-model", pool=ProviderPool(Config()))
    assert await dm.select_skills_with_decision_model(b, "task", [("a", "desc")]) == ["a"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply,expected", [
    ('{"selected":["1"]}', ["local/second"]),
    ("invalid json", ["local/first"]),
    ('{"selected":[]}', []),
    ('{"selected":["blocked"]}', ["local/first"]),
])
async def test_scent_menu_preserves_authorization_on_success_and_fallback(reply, expected):
    from raven.context_engine.scent import ScentMenu
    from raven.skill_hub.policy import SkillPolicy

    async def select(*args, **kwargs):
        return [
            SimpleNamespace(qualified_id=f"local/{name}", name=name,
                            meta={"description": f"Help with {name}"}, content="")
            for name in ("blocked", "first", "second")
        ]

    async def chat(messages, **kwargs):
        assert "Help with blocked" not in repr(messages)
        return SimpleNamespace(content=reply)

    menu = ScentMenu(
        SimpleNamespace(select=select), top_k=1,
        policy=SkillPolicy.create(blocklist=["blocked"]),
        decision_binding=lambda: binding(chat),
    )
    result = await menu.build("Refactor the parser and add focused regression tests", [])
    assert result.skill_ids == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("output,expected,main_calls", [
    ('{"selected":["0"]}', "coder", 0), ("invalid json", "writer", 1),
])
async def test_specialist_manager_preserves_main_model_fallback(tmp_path, output, expected, main_calls):
    from raven.agent.subagent.manager import SubagentManager

    calls = []

    async def main_chat(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content="writer")

    async def decide(*args, **kwargs):
        return SimpleNamespace(content=output)

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"decisionModel": {
        "model": "renamed", "provider": "gateway", "protocol": "chat", "useForSpecialists": True,
    }}))
    pool = SimpleNamespace(bind_pin=lambda *args: binding(decide))
    manager = SubagentManager(
        provider=SimpleNamespace(chat=main_chat), workspace=tmp_path, model="main",
        provider_pool=pool, live_config=LiveConfig(path),
    )
    assert await manager._classify([("coder", "Code"), ("writer", "Prose")], "Refactor parser", "general") == expected
    assert len(calls) == main_calls
