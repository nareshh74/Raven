"""Provider-independent, bounded, fail-open skill and specialist decisions."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from loguru import logger

from raven.config.live import LiveConfig, decision_settings
from raven.providers.decision_protocols import DecisionCandidate, DecisionRequest, get_decision_adapter

if TYPE_CHECKING:
    from raven.providers.binding import ModelBinding
    from raven.providers.pool import ProviderPool


@dataclass
class _Circuit:
    failures: int = 0
    opened_at: float | None = None
    probing: bool = False


_CIRCUITS: dict[tuple[int, str, str, str, str], _Circuit] = {}
_WARNED: set[tuple[str, str]] = set()


def resolve_decision_binding(
    live_config: LiveConfig | None, provider_pool: ProviderPool | None,
) -> ModelBinding | None:
    if live_config is None or provider_pool is None:
        return None
    cfg = decision_settings(live_config)
    if not cfg.model:
        return None
    try:
        binding = provider_pool.bind_pin(cfg.model, cfg.provider)
        if binding is not None and hasattr(provider_pool, "decision_transport"):
            binding = replace(binding, decision_config=cfg)
        return binding
    except Exception as exc:
        logger.bind(event="decision_model.fallback", reason="binding_error").warning(
            "decision_model.fallback reason=binding_error error_type={}", type(exc).__name__,
        )
        return None


async def _decide(
    binding: ModelBinding, query: str, candidates: Sequence[tuple[str, str]],
    use_case: Literal["skills", "specialists"], max_choices: int, timeout: float,
    live_config: LiveConfig | None,
) -> list[str] | None:
    started = time.monotonic()
    cfg = decision_settings(live_config) if live_config else binding.decision_config
    if live_config:
        binding = replace(binding, decision_config=cfg)
    pool = binding.pool
    protocol = (cfg.protocol if cfg else None) or "chat"
    provider = binding.provider_name or getattr(binding.provider, "_provider_name", "") or type(binding.provider).__name__
    log = logger.bind(
        provider=provider, model=binding.model, protocol=protocol, use_case=use_case,
        candidates=len(candidates),
    )
    warning_key = (provider, binding.model)
    if (cfg is None or cfg.protocol is None) and protocol == "chat" and warning_key not in _WARNED:
        _WARNED.add(warning_key)
        log.bind(event="decision_model.protocol_auto_chat").warning(
            "decision_model.protocol_auto_chat provider={} model={} protocol=chat; "
            "set decisionModel.protocol explicitly (criteria_map_inline / criteria_map for native endpoints)", provider, binding.model,
        )
    key = (id(pool or binding.provider), provider, binding.model, protocol, use_case)
    circuit = _CIRCUITS.setdefault(key, _Circuit())

    def fallback(reason: str) -> None:
        elapsed = round((time.monotonic() - started) * 1000, 2)
        log.bind(event="decision_model.fallback", latency_ms=elapsed, reason=reason,
                 fallback_target="bm25_rrf" if use_case == "skills" else "main_model").warning(
            "decision_model.fallback provider={} model={} protocol={} use_case={} latency_ms={} reason={}",
            provider, binding.model, protocol, use_case, elapsed, reason,
        )
        return None

    if circuit.opened_at is not None:
        if started - circuit.opened_at < 30 or circuit.probing:
            return fallback("circuit_open")
        circuit.probing = True
        log.bind(event="decision_model.circuit").warning("decision_model.circuit state=half_open")
    cap = cfg.timeout_seconds if cfg and cfg.timeout_seconds is not None else (2.0 if use_case == "skills" else 4.0)
    default_timeout = 2.0 if use_case == "skills" else 4.0
    deadline = started + (cap if timeout == default_timeout else min(timeout, cap))
    request = DecisionRequest(
        use_case, query, f"Select at most {max_choices} relevant options for this {use_case} request.",
        tuple(DecisionCandidate(str(i), target, desc) for i, (target, desc) in enumerate(candidates)),
        max_choices, True, deadline,
    )
    try:
        async with asyncio.timeout_at(asyncio.get_running_loop().time() + max(0, deadline - time.monotonic())):
            adapter = get_decision_adapter(protocol)
            transport = await pool.decision_transport(binding) if pool and protocol != "chat" else None
            result = await adapter.decide(binding, request, transport)
            allowed = {c.opaque_id: c.target_id for c in request.candidates}
            if (
                len(result.selected_ids) > max_choices or len(set(result.selected_ids)) != len(result.selected_ids)
                or any(s not in allowed for s in result.selected_ids)
                or any(s not in allowed and s != "__none__" for s in result.probabilities)
            ):
                raise ValueError("invalid decision indices or cardinality")
            selected = [allowed[s] for s in result.selected_ids]
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        circuit.failures += 1
        if circuit.failures >= 3:
            circuit.opened_at = time.monotonic()
            log.bind(event="decision_model.circuit").warning("decision_model.circuit state=open")
        status = getattr(getattr(exc, "response", None), "status_code", None)
        reason = "timeout" if isinstance(exc, TimeoutError) else (
            f"http_{status // 100}xx" if isinstance(status, int) else
            "invalid_output" if isinstance(exc, (ValueError, KeyError, TypeError, IndexError)) else "transport_error"
        )
        return fallback(reason)
    finally:
        circuit.probing = False
    if circuit.opened_at is not None:
        log.bind(event="decision_model.circuit").info("decision_model.circuit state=closed")
    circuit.failures = 0
    circuit.opened_at = None
    elapsed = round((time.monotonic() - started) * 1000, 2)
    confidence = max(result.probabilities.values(), default=None)
    log.bind(event="decision_model.routed", latency_ms=elapsed, selected=selected, confidence=confidence).info(
        "decision_model.routed provider={} model={} protocol={} use_case={} latency_ms={} selected={} confidence={}",
        provider, binding.model, protocol, use_case, elapsed, selected, confidence,
    )
    return selected


async def select_skills_with_decision_model(
    binding: ModelBinding, query: str, candidates: Sequence[tuple[str, str]], *,
    max_skills: int = 3, timeout: float = 2.0, live_config: LiveConfig | None = None,
) -> list[str] | None:
    unique = list(dict((sid, desc) for sid, desc in candidates if sid).items())
    if not unique or max_skills <= 0:
        return []
    return await _decide(binding, query, unique, "skills", max_skills, timeout, live_config)


async def select_specialist_with_decision_model(
    binding: ModelBinding, task: str, roster_text: str, valid_names: Iterable[str], *,
    timeout: float = 4.0, live_config: LiveConfig | None = None,
) -> str | None:
    names = list(dict.fromkeys(n for n in valid_names if isinstance(n, str) and n))
    if not names:
        return None
    # The roster stays untrusted query context; only allowlisted indices can be returned.
    selected = await _decide(
        binding, task + "\nSpecialist roster:\n" + roster_text,
        [(name, name) for name in names], "specialists", 1, timeout, live_config,
    )
    return selected[0] if selected else None
