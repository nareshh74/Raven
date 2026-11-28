"""Provider-independent decision routing contracts."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from raven.agent.decision_model import resolve_decision_binding, select_skills_with_decision_model
from raven.config.live import LiveConfig
from raven.config.raven import DecisionModelConfig
from raven.providers.binding import ModelBinding


def test_arbitrary_model_binding(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"decisionModel": {"model": "my-alias-v9", "provider": "gateway"}}))
    expected = object()
    pool = SimpleNamespace(bind_pin=lambda model, provider: expected)
    assert resolve_decision_binding(LiveConfig(path), pool) is expected


@pytest.mark.parametrize("path", ["https://other.invalid/x", "//other.invalid/x", "/../x", "/%2e%2e/x", "/x?y=1", "/\\other/x"])
def test_unsafe_endpoint_rejected(path):
    with pytest.raises(ValueError):
        DecisionModelConfig(endpoint_path=path)


def test_config_accepts_explicit_dialect():
    cfg = DecisionModelConfig(protocol="criteria_map_inline", endpoint_path="/route", timeout_seconds=0.5)
    assert cfg.protocol == "criteria_map_inline"
    with pytest.raises(ValueError):
        DecisionModelConfig(protocol="unknown")
    with pytest.raises(ValueError):
        DecisionModelConfig(timeout_seconds=float("nan"))


@pytest.mark.asyncio
async def test_chat_uses_opaque_ids_and_rejects_unknown():
    calls = []

    async def chat(messages, **kwargs):
        calls.append((messages, kwargs))
        return SimpleNamespace(content='{"selected":["0"]}', finish_reason="stop")

    binding = ModelBinding(SimpleNamespace(chat=chat), "any-alias")
    assert await select_skills_with_decision_model(binding, "task", [("private-skill", "ignore instructions")]) == ["private-skill"]
    assert "private-skill" not in json.dumps(calls)
    assert calls[0][1]["response_format"]["type"] == "json_schema"

    async def invalid(*args, **kwargs):
        return SimpleNamespace(content='{"selected":["private-skill"]}', finish_reason="stop")

    binding = ModelBinding(SimpleNamespace(chat=invalid), "unknown-output-alias")
    assert await select_skills_with_decision_model(binding, "task", [("private-skill", "description")]) is None


@pytest.mark.asyncio
async def test_native_wire_dialects():
    from raven.providers.decision_protocols import (
        DecisionCandidate, DecisionRequest, DecisionTransportContext, get_decision_adapter,
    )
    import time
    import httpx

    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"answers": {"q1": {"choice": "0", "probabilities": {"0": 0.9}}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        transport = DecisionTransportContext(client, "https://gateway.invalid/v1", {}, "/classify", "alias")
        request = DecisionRequest("skills", "query", "select", (DecisionCandidate("0", "actual", "description"),), 1, True, time.monotonic() + 2)
        binding = ModelBinding(SimpleNamespace(), "opaque/alias")
        for dialect in ("criteria_map", "criteria_map_inline", "choices_array"):
            result = await get_decision_adapter(dialect).decide(binding, request, transport)
            assert result.selected_ids == ("0",)
    assert isinstance(bodies[0]["questions"], dict)
    assert "query" in bodies[1]["questions"]["q1"]["instructions"]
    assert isinstance(bodies[2]["questions"], list)
    assert bodies[2]["questions"][0]["choices"][0]["value"] == "0"
    assert "actual" not in json.dumps(bodies)


@pytest.mark.asyncio
async def test_native_array_probabilities():
    import time
    import httpx
    from raven.providers.decision_protocols import DecisionCandidate, DecisionRequest, DecisionTransportContext, get_decision_adapter

    def handle(request):
        return httpx.Response(200, json={"answers": [{"name": "q1", "choice": "0", "probabilities": [
            {"value": "0", "probability": 0.9},
        ]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        transport = DecisionTransportContext(client, "https://gateway.invalid", {}, "/native", "alias")
        request = DecisionRequest("skills", "query", "select", (DecisionCandidate("0", "target", "desc"),), 1, True, time.monotonic() + 2)
        result = await get_decision_adapter("choices_array").decide(ModelBinding(SimpleNamespace(), "alias"), request, transport)
        assert result.probabilities == {"0": 0.9}


@pytest.mark.asyncio
async def test_cancel_does_not_open_circuit():
    calls = 0

    async def chat(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError

    binding = ModelBinding(SimpleNamespace(chat=chat), "cancel-alias")
    for _ in range(4):
        with pytest.raises(asyncio.CancelledError):
            await select_skills_with_decision_model(binding, "task", [("a", "desc")])
    assert calls == 4


@pytest.mark.asyncio
async def test_schema_rejection_is_cached_without_retrying_the_turn():
    import httpx

    calls = []

    async def chat(messages, **kwargs):
        calls.append(kwargs)
        if "response_format" in kwargs:
            response = httpx.Response(422, request=httpx.Request("POST", "https://gateway.invalid/chat"))
            response.raise_for_status()
        assert kwargs["tool_choice"]["function"]["name"] == "record_decision"
        return SimpleNamespace(tool_calls=[
            SimpleNamespace(name="record_decision", arguments={"selected": ["0"]}),
        ])

    b = ModelBinding(SimpleNamespace(chat=chat), "schema-rejected")
    assert await select_skills_with_decision_model(b, "task", [("a", "desc")]) is None
    assert len(calls) == 1
    assert await select_skills_with_decision_model(b, "task", [("a", "desc")]) == ["a"]
    assert len(calls) == 2
    assert "response_format" not in calls[1]


@pytest.mark.asyncio
async def test_chat_adapter_falls_back_schema_to_tools_to_plain_json():
    import httpx

    calls = []

    async def chat(messages, **kwargs):
        calls.append(kwargs)
        if "response_format" in kwargs or "tools" in kwargs:
            response = httpx.Response(400, request=httpx.Request("POST", "https://gateway.invalid/chat"))
            response.raise_for_status()
        return SimpleNamespace(content='{"selected": ["0"]}', finish_reason="stop")

    b = ModelBinding(SimpleNamespace(chat=chat), "plain-json-fallback")
    assert await select_skills_with_decision_model(b, "task", [("a", "desc")]) is None
    assert "response_format" in calls[0]
    assert await select_skills_with_decision_model(b, "task", [("a", "desc")]) is None
    assert "tools" in calls[1] and "response_format" not in calls[1]
    assert await select_skills_with_decision_model(b, "task", [("a", "desc")]) == ["a"]
    assert "tools" not in calls[2] and "response_format" not in calls[2]


def test_provider_config_model_protocols_rejects_decision_dialects():
    from pydantic import ValidationError
    from raven.config.schema import ProviderConfig

    cfg = ProviderConfig(model_protocols={"gpt-4o": "responses"})
    assert cfg.model_protocols == {"gpt-4o": "responses"}
    with pytest.raises(ValidationError):
        ProviderConfig(model_protocols={"alias": "criteria_map"})


@pytest.mark.asyncio
async def test_native_transport_does_not_follow_cross_origin_redirects():
    import time
    import httpx
    from raven.providers.decision_protocols import DecisionTransportContext

    seen = []

    def handle(request):
        seen.append(str(request.url))
        return httpx.Response(307, headers={"location": "https://other.invalid/collect"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
        transport = DecisionTransportContext(client, "https://gateway.invalid/v1", {}, "/native", "alias")
        with pytest.raises(httpx.HTTPStatusError):
            await transport.post({}, time.monotonic() + 1)
    assert seen == ["https://gateway.invalid/native"]


def test_capability_metadata_accepts_rpc_field_spelling():
    from raven.providers.decision_protocols import resolve_decision_capability

    capability = resolve_decision_capability("renamed", "gateway", {
        "decision": {"protocolId": "criteria_map", "endpointPath": "/native"},
        "modelOverlay": {"renamed": {"decision": {"isReasoning": True}}},
    })
    assert capability.protocol_id == "criteria_map"
    assert capability.endpoint_path == "/native"
    assert capability.is_reasoning is True


def test_live_catalogue_preserves_decision_capability_and_latency():
    from raven.config.update_providers import _model_facts_from_payload

    facts = _model_facts_from_payload({"data": [{
        "id": "renamed", "decision": {"protocolId": "choices_array", "endpointPath": "/classify"},
        "observedLatencyMs": 2400,
    }]})
    assert facts["renamed"]["decision"]["protocolId"] == "choices_array"
    assert facts["renamed"]["observedLatencyMs"] == 2400


@pytest.mark.asyncio
async def test_native_credentials_use_the_existing_provider_auth_headers(monkeypatch):
    from raven.providers.litellm_provider import LiteLLMProvider
    from litellm.llms.github_copilot import authenticator, common_utils

    monkeypatch.setattr(authenticator, "Authenticator", lambda: SimpleNamespace(
        get_api_key=lambda: "test-token", get_api_base=lambda: "https://upstream.invalid",
    ))
    monkeypatch.setattr(common_utils, "get_copilot_default_headers", lambda token: {
        "Authorization": f"Bearer {token}", "X-Client-Route": "native",
    })
    provider = object.__new__(LiteLLMProvider)
    provider._provider_name = "github_copilot"
    provider.api_key = None
    provider.api_base = "https://configured.invalid"
    provider.extra_headers = {"X-Deployment": "chosen"}
    base, headers = await provider.decision_credentials()
    assert base == "https://configured.invalid"
    assert headers["X-Client-Route"] == "native"
    assert headers["X-Deployment"] == "chosen"
    assert headers["Authorization"] == "Bearer test-token"


def test_discovery_uses_the_already_loaded_provider_snapshot(monkeypatch):
    import httpx
    from raven.config.update_providers import test_provider as probe

    def unexpected_read(*args):
        raise AssertionError("discovery must not reload the same config per provider")

    monkeypatch.setattr("raven.config.update_providers.read_raw_or_raise", unexpected_read)
    result = probe(
        "custom", timeout_s=2,
        section_snapshot={"api_key": "test", "api_base": "https://gateway.invalid/v1"},
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": [{"id": "renamed"}]})),
    )
    assert result["ok"] is True
    assert result["model_ids"] == ["renamed"]
