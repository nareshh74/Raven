"""Builds a :class:`ModelBinding` for a model id, and caches it.

One place answers "which credential serves this model", so a per-session
model, a subsystem pin and the configured default all resolve the same way
instead of each guessing. Caching matters because a session switching back
and forth must not rebuild a provider per turn -- building one imports
LiteLLM and writes vendor env vars.

Provider resolution has to go through a config copy with the model
substituted: :meth:`Config._match_provider` short-circuits on a forced
``agents.defaults.provider``, so asking the live config about another
vendor's model would answer with the forced one and pair the wrong key.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from loguru import logger
import httpx

from raven.providers.binding import ModelBinding

if TYPE_CHECKING:
    from raven.config.schema import Config
    from raven.providers.base import LLMProvider
    from raven.providers.decision_protocols import DecisionCapability, DecisionTransportContext


def live_pin_resolver(
    pool: "ProviderPool | None",
    read_pin: "Callable[[], tuple[str | None, str | None]]",
    *,
    key: str,
    follower: str,
) -> "Callable[[], ModelBinding | None]":
    """A subsystem's pin, re-read per call, reported once when it is unusable.

    Per call rather than once at build: the pair is a preference, and a
    subsystem whose pin was resolved when its owner was constructed could only
    be repointed by restarting the process. The pool is what keeps that cheap
    -- a pair it has already built comes back from the cache.

    ``None`` means run on the conversation's model, which is both what an
    unset pin means and what an unusable one falls back to. The two are not
    the same mistake, so a pin that names a vendor with no credentials says so
    -- once per pin, because the alternative is a line per call for as long as
    the config stays wrong, and per pin rather than per resolver because the
    resolver outlives the config that produced it: a later, different unusable
    pin is a new problem and gets its own line.
    """
    warned: set[tuple[str, str | None]] = set()

    def _resolve_now() -> "ModelBinding | None":
        model, provider_name = read_pin()
        if not model:
            return None
        binding = pool.bind_pin(model, provider_name) if pool is not None else None
        if binding is None and (model, provider_name) not in warned:
            warned.add((model, provider_name))
            logger.warning(
                "{}={!r} has no usable credentials of its own; {} follows the conversation's model instead",
                key,
                model,
                follower,
            )
        return binding

    def resolve() -> "ModelBinding | None":
        from raven.config.live import held

        return held(key, _resolve_now)

    return resolve


class ProviderPool:
    """Resolve and cache one provider per (provider name, model) pair."""

    def __init__(self, config: "Config | Callable[[], Config]") -> None:
        # A supplier, not a snapshot: a credential fixed after start (an OAuth
        # re-login, an edited config file) has to be visible without a restart.
        # Cached bindings are dropped when the config that produced them is no
        # longer the current one.
        self._supplier: "Callable[[], Config]" = config if callable(config) else (lambda: config)
        self._cache: dict[tuple[str, str], ModelBinding] = {}
        self._cache_key: str | None = None
        self._decision_clients: dict[bool, httpx.AsyncClient] = {}

    @property
    def config(self) -> "Config":
        return self._supplier()

    def _live_cache(self) -> dict[tuple[str, str], ModelBinding]:
        """Drop cached bindings when anything they were built from changed.

        Not identity on the config object: a supplier that re-reads the file
        returns a new object every call, which would clear the cache every
        time and defeat the pool. A fingerprint of what a binding is actually
        built from is the thing that has to match.
        """
        fingerprint = self._cache_fingerprint()
        if self._cache_key != fingerprint:
            self._cache_key = fingerprint
            self._cache = {}
        return self._cache

    def _cache_fingerprint(self) -> str:
        """Everything a cached binding was built from.

        The credentials, because that is what a provider is built out of -- and
        also ``contextWindowTokens``, because ``bind`` copies it onto the binding.
        A key that does not cover the value it caches means editing that number
        leaves a stale binding serving the old one until a restart, which is
        exactly the kind of "it did nothing" the pin ladder exists to avoid.
        """
        import hashlib
        import json

        try:
            material = {
                "providers": credentials_fingerprint(self.config),
                "window": self.config.agents.defaults.context_window_tokens,
            }
        except Exception:
            return ""
        return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()

    def bind(self, model: str, provider_name: str | None = None) -> ModelBinding:
        """Build (or reuse) the provider that serves ``model``.

        ``provider_name`` is what the caller already knows -- the picker sends
        one, and ``agents.defaults.provider`` is one. Absent or ``"auto"``, the
        vendor is derived from the model id, the same derivation
        ``config.set model`` uses for a hand-typed id.
        """
        resolved = self._resolve_provider_name(model, provider_name)
        cache = self._live_cache()
        key = (resolved, model)
        cached = cache.get(key)
        if cached is not None:
            return cached

        from raven.providers.factory import make_provider

        cfg = self.config.model_copy(deep=True)
        cfg.agents.defaults.model = model
        cfg.agents.defaults.provider = resolved
        # The pin travels with the binding rather than being read where the
        # window is used: a session on another model still owes the user the
        # number they pinned.
        binding = ModelBinding(make_provider(cfg), model, cfg.agents.defaults.context_window_tokens, resolved, self)
        cache[key] = binding
        return binding

    def bind_pin(self, model: str | None, provider_name: str | None = None) -> ModelBinding | None:
        """A subsystem's own model, on its own credential -- or None.

        This is what lets a pinned subsystem run off the session's model. None
        means the pin is unusable, and the caller should fall back to the
        session's binding rather than send one vendor's key to another.

        ``provider_name`` is the configured half of the pair, and when present
        nothing is derived: an id alone cannot say whether ``anthropic`` or a
        gateway reselling it is meant, and those are different credentials and
        different bills. Absent, a configured gateway takes the pin and only
        without one is the vendor derived from the id -- which is what a config
        written before the provider field existed gets. Note the asymmetry: an
        explicitly named vendor that turns out unusable is dropped, while the
        gateway branch binds whatever it is handed, because a gateway having no
        route for an id is not something this can see from here.
        """
        if not model:
            return None
        if provider_name and provider_name != "auto":
            configured = provider_name
            if not self._has_credentials(configured, model):
                # Explicitly configured and still unusable: a config error the
                # user can fix, and silence here is what let a pinned
                # subsystem look configured while never running.
                logger.warning(
                    "pinned model {!r} names provider {!r}, which has no usable credentials; "
                    "the subsystem follows the conversation's model instead",
                    model,
                    configured,
                )
                return None
            resolved = configured
        else:
            # A gateway (or a local deployment) serves whatever id it is handed
            # under its own credential, so the pin is already paired -- asking
            # whether the upstream vendor has a key of its own would drop a pin
            # that works. Bind it through the gateway instead.
            gateway = self._configured_gateway()
            resolved = gateway if gateway is not None else self._resolve_provider_name(model, None)
            if gateway is None and not self._has_credentials(resolved, model):
                return None
        try:
            return self.bind(model, resolved)
        except Exception as exc:
            # Called from the context-engine factory at construction, so this
            # must leave the subsystem following the conversation rather than
            # stop the agent from starting. Deliberately broad: building a
            # provider imports a vendor module, so the failure modes are not
            # only the credential ones.
            logger.warning("cannot build a provider for pinned model {!r}: {}", model, exc)
            return None

    def _configured_gateway(self) -> str | None:
        """The agent's provider, when it is a gateway or a local deployment."""
        from raven.providers.registry import find_by_name

        forced = self.config.agents.defaults.provider
        if not forced or forced == "auto":
            return None
        spec = find_by_name(forced)
        if spec is None:
            return None
        return forced if (spec.is_gateway or spec.is_local) else None

    def _resolve_provider_name(self, model: str, provider_name: str | None) -> str:
        if provider_name and provider_name != "auto":
            return provider_name
        from raven.providers.registry import find_by_model

        spec = find_by_model(model)
        return spec.name if spec is not None else "auto"

    def decision_capability(self, binding: ModelBinding) -> DecisionCapability:
        from raven.providers.decision_protocols import resolve_decision_capability
        from raven.providers.registry import find_by_name
        from raven.providers.served_models import recall

        section = self.config.providers.get(binding.provider_name or "")
        spec = find_by_name(binding.provider_name or "")
        return resolve_decision_capability(binding.model, binding.provider_name, section, spec, recall(binding.provider_name or ""))

    async def decision_provider(self, binding: ModelBinding) -> LLMProvider:
        """Resolve wrappers before inspecting a decision provider's wire support."""
        import asyncio

        provider = binding.provider
        if hasattr(provider, "_built"):
            provider = await asyncio.to_thread(provider._built)
        if hasattr(provider, "_pick"):
            provider = provider._pick(binding.model)
        if getattr(provider, "_inners", None):
            provider = provider._inners[provider._healthy_order()[0]]
        return provider

    async def decision_transport(self, binding: ModelBinding) -> DecisionTransportContext:
        from raven.providers.decision_protocols import DecisionTransportContext
        from raven.providers.registry import find_by_name
        from raven.providers.wire import wire_model

        provider = await self.decision_provider(binding)
        section = self.config.providers.get(binding.provider_name or "")
        spec = find_by_name(binding.provider_name or "")
        base, headers = await provider.decision_credentials()
        base = base or getattr(section, "api_base", None) or (
            getattr(spec, "decision_api_base", "") or getattr(spec, "default_api_base", "")
        )
        verify = getattr(provider, "tls_verify", getattr(section, "tls_verify", True))
        client = self._decision_clients.get(verify)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(verify=verify, follow_redirects=False, limits=httpx.Limits(
                max_connections=4, max_keepalive_connections=4,
            ))
            self._decision_clients[verify] = client
        capability = self.decision_capability(binding)
        if capability.auth_header and capability.auth_header != "Authorization":
            token = headers.pop("Authorization", "").removeprefix("Bearer ")
            if token:
                headers[capability.auth_header] = token
        cfg = binding.decision_config
        path = (cfg.endpoint_path if cfg else None) or capability.endpoint_path
        return DecisionTransportContext(
            client, base, headers, path,
            wire_model(binding.model, client_provider=binding.provider_name) if binding.provider_name else binding.model,
        )

    async def aclose(self) -> None:
        for client in self._decision_clients.values():
            await client.aclose()
        self._decision_clients.clear()

    def _has_credentials(self, provider_name: str, model: str) -> bool:
        """Is there a usable section for this vendor, or only a placeholder?

        Every declared provider exists as an empty section, so presence proves
        nothing -- reuse the same check the credential preflight uses.
        """
        from raven.config.schema import section_has_credentials as section_is_usable
        from raven.providers.registry import find_by_name

        if provider_name == "auto":
            # No vendor could be derived, so there is no section to check and
            # no way to tell a working pin from a mis-paired one. Treat it as
            # unusable: the caller's fallback is the session's own binding,
            # which is at least a pair.
            return False
        # ``providers.get`` is the only spelling-insensitive lookup; reading the
        # attribute sees one spelling of a name that has several.
        section = self.config.providers.get(provider_name)
        if section is None:
            return False
        return bool(section_is_usable(section, find_by_name(provider_name)))


def credentials_fingerprint(config: "Config") -> str:
    """A fingerprint of every credential a provider can be built from.

    The one answer to "did the providers section change", shared by the pool's
    binding cache and ``ResolvingProvider``'s per-vendor adapters, so the two
    caches cannot age at different rates. Raises whatever the dump raises --
    each caller owns its own failure posture.
    """
    import hashlib
    import json

    material = config.providers.model_dump(exclude_none=True)
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()
