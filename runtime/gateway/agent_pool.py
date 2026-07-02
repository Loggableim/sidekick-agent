"""
Agent Pool — Concurrency limiter for parallel LLM agent sessions.

Each provider gets its own asyncio.Semaphore so we never exceed:
- Local GPU/CPU: 1 slot (hardware constraint)
- ollama-cloud Sub: 3 slots, Free Keys: 1 each
- opencode-go: 6 slots per active key (2 active = 12 total)
- nvidia: 5 slots (gratis, stabil)
- gemini: 5 slots (free tier, wie nvidia)
- openai-codex: 3 slots (90% nur 5.4-mini)
- minimax: 3 slots (solange Sub läuft)
- rest: 3 slots default

Key Recovery Logic:
- exhausted Keys mit abgelaufenem last_error_reset_at → auto-reaktivieren
- 6h Ban bei Error (429/5xx) → nach 6h zurück in Pool
- Liest auth.json für Status + Reset-Timestamps

Queue:
- FIFO-Warteschlange pro Provider
- Busy-Message an wartende Sessions
"""

import asyncio
import json
import logging
import os
import time
from collections import OrderedDict
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# --- Default pool limits per provider ---
# Key: provider name (as used in config.yaml / auth.json)
# Value: max concurrent agents for that provider
DEFAULT_POOL_LIMITS: Dict[str, int] = {
    "local-gpu": 1,
    "local-cpu": 1,
    "router_provider": 1,
    "ollama-cloud": 6,        # Sub = 3 (max 3 active) + 3 Free Keys (je 1)
    "opencode-go": 12,        # 2 aktive Keys × 6
    "nvidia": 5,              # Gratis, stabil
    "gemini": 5,              # Free Tier, großzügig
    "openai-codex": 3,        # 90% 5.4-mini
    "minimax": 3,             # Sub aktiv
    "openrouter": 3,
    "chutes": 3,
    "cerebras": 3,
    "mistral": 3,
    "deepseek": 2,
    "opencode": 3,
}

# How many seconds a key is banned after an error before retrying
KEY_BAN_SECONDS = 6 * 3600  # 6 hours

# How often to check for recovered keys (seconds)
RECOVERY_CHECK_INTERVAL = 300  # 5 minutes

# Path to auth.json for key status monitoring
AUTH_JSON_PATHS = [
    os.path.join(os.environ.get("SIDEKICK_HOME", ""), "auth.json"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "home", "auth.json"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "home", "auth.json"),
]


def _resolve_auth_path() -> Optional[str]:
    """Find auth.json in standard locations."""
    for path in AUTH_JSON_PATHS:
        resolved = os.path.abspath(path)
        if os.path.exists(resolved):
            return resolved
    # Try common sidekick home locations
    candidates = [
        os.path.expanduser("~/.sidekick/auth.json"),
        "/c/HermesPortable/home/auth.json",
        "C:\\HermesPortable\\home\\auth.json",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def _load_auth_json() -> dict:
    """Load auth.json and return the credential pool section."""
    path = _resolve_auth_path()
    if not path:
        return {"credential_pool": {}}
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load auth.json: %s", e)
        return {"credential_pool": {}}


def _check_key_recovery(auth_data: dict, provider: str) -> int:
    """
    Count how many keys for this provider are OK (not exhausted or recovered).
    
    Returns the number of viable keys.
    Also updates auth.json if recovered keys are found (sets exhausted→ok).
    """
    pool = auth_data.get("credential_pool", {})
    keys = pool.get(provider, [])
    if not keys:
        return 0
    
    viable = 0
    now = time.time()
    changed = False
    
    for key in keys:
        status = key.get("last_status", "ok")
        reset_at = key.get("last_error_reset_at")
        
        if status == "ok":
            viable += 1
        elif status == "exhausted" and reset_at and reset_at < now:
            # Reset time passed — key recovered
            logger.info(
                "Key recovery: %s/%s — reset time passed (%.0fs ago), re-enabling",
                provider, key.get("label", "?"),
                now - reset_at,
            )
            key["last_status"] = "ok"
            key["last_error_code"] = None
            key["last_error_message"] = None
            key["last_error_reset_at"] = None
            viable += 1
            changed = True
    
    if changed:
        _save_auth_json(auth_data)
    
    return viable if viable > 0 else len(keys)  # fallback: assume all keys viable


def _save_auth_json(data: dict) -> None:
    """Persist updated auth.json (e.g. after key recovery)."""
    path = _resolve_auth_path()
    if not path:
        return
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        logger.warning("Failed to write auth.json: %s", e)


def _normalize_provider_name(raw: str) -> str:
    """Normalize provider names to pool keys."""
    if not raw:
        return "unknown"
    name = raw.lower().strip()
    # Map common prefixes/aliases
    if name.startswith("custom:"):
        name = name[7:]
    if name.startswith("@") :
        name = name[1:]
    # Handle provider@model patterns
    if ":" in name:
        name = name.split(":")[0]
    return name


class PoolSlot:
    """Tracks a single slot usage within a provider pool."""
    __slots__ = ("session_key", "provider", "started_at")
    
    def __init__(self, session_key: str, provider: str):
        self.session_key = session_key
        self.provider = provider
        self.started_at = time.time()


class ProviderPool:
    """
    Per-provider concurrency control.
    
    Each provider gets a semaphore and a FIFO queue.
    """
    
    def __init__(self, provider: str, max_concurrent: int):
        self.provider = provider
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._queue: "OrderedDict[str, asyncio.Event]" = OrderedDict()
        self._active: Dict[str, PoolSlot] = {}
        self._last_recovery_check = 0.0
    
    @property
    def available(self) -> int:
        """Number of currently available slots."""
        return self.semaphore._value  # type: ignore
    
    @property
    def queued_count(self) -> int:
        """Number of sessions waiting in queue."""
        return len(self._queue)
    
    @property
    def active_count(self) -> int:
        """Number of currently active sessions."""
        return len(self._active)
    
    def get_queue_position(self, session_key: str) -> Optional[int]:
        """Get position in queue (0-indexed), or None if not queued."""
        for i, key in enumerate(self._queue):
            if key == session_key:
                return i
        return None


class AgentPool:
    """
    Global agent pool — manages per-provider concurrency.
    
    Usage:
        pool = AgentPool(config)
        ctx = await pool.acquire(session_key, provider_name)
        try:
            await run_agent(...)
        finally:
            await pool.release(session_key, provider_name)
    """
    
    def __init__(self, config: Optional[dict] = None):
        self._config = config or {}
        self._pool_config = self._config.get("agent_pool", {})
        
        # Build pools from config limits
        self._enabled = self._pool_config.get("enabled", False)
        user_limits = self._pool_config.get("strategies", {})
        
        self._pools: Dict[str, ProviderPool] = {}
        self._limits = dict(DEFAULT_POOL_LIMITS)
        
        # Apply user-configured overrides
        if isinstance(user_limits, dict):
            for provider, limit in user_limits.items():
                normalized = _normalize_provider_name(provider)
                if isinstance(limit, (int, float)) and limit > 0:
                    self._limits[normalized] = int(limit)
        
        # Initialize all known pools
        for provider_name, limit in self._limits.items():
            self._pools[provider_name] = ProviderPool(provider_name, limit)
        
        # Default pool for unknown providers
        default_limit = self._pool_config.get("default_max_concurrent", 3)
        self._default_pool = ProviderPool("__default__", default_limit)
        
        # Recovery state
        self._last_recovery_check = 0.0
        
        logger.info(
            "AgentPool initialized: enabled=%s, pools=%d, default_limit=%d",
            self._enabled, len(self._pools), default_limit,
        )
    
    @property
    def enabled(self) -> bool:
        return self._enabled
    
    def get_pool(self, provider: str) -> ProviderPool:
        """Get or create a pool for a provider."""
        normalized = _normalize_provider_name(provider)
        pool = self._pools.get(normalized)
        if pool is None:
            # Lazy create for unknown providers
            limit = self._limits.get(normalized, self._pool_config.get("default_max_concurrent", 3))
            pool = ProviderPool(normalized, limit)
            self._pools[normalized] = pool
            self._limits[normalized] = limit
            logger.debug("Created pool for provider '%s' with limit %d", normalized, limit)
        return pool
    
    def _check_recover_keys(self) -> None:
        """Periodically check auth.json for recovered keys."""
        now = time.time()
        if now - self._last_recovery_check < RECOVERY_CHECK_INTERVAL:
            return
        self._last_recovery_check = now
        
        if not self._enabled:
            return
        
        auth_data = _load_auth_json()
        if not auth_data.get("credential_pool"):
            return
        
        # Check all known providers for key recovery
        for provider in list(self._pools.keys()) + list(self._limits.keys()):
            _check_key_recovery(auth_data, provider)
    
    async def acquire(self, session_key: str, provider: str) -> bool:
        """
        Acquire a slot in the pool for this session+provider.

        Returns True immediately if a slot is available.
        Returns False if pool is disabled (passthrough mode).
        Blocks (awaits) if all slots are busy — waits in FIFO queue.
        """
        if not self._enabled:
            return False

        self._check_recover_keys()

        pool = self.get_pool(provider)
        queue_event: Optional[asyncio.Event] = None

        # Track if we need to queue
        if pool.semaphore.locked():
            # All slots busy — join FIFO queue
            queue_event = asyncio.Event()
            pool._queue[session_key] = queue_event
            queue_pos = pool.get_queue_position(session_key)
            logger.info(
                "AgentPool: %s queueing for %s (pos %d, active: %d/%d)",
                session_key, provider, queue_pos,
                pool.active_count, pool.max_concurrent,
            )

        # Acquire the semaphore manually (NOT via async with — must stay
        # held until release() is called after agent run completes)
        await pool.semaphore.acquire()

        # We got a slot — register active usage
        slot = PoolSlot(session_key, provider)
        pool._active[session_key] = slot

        # If we were queued, clean up
        if queue_event and session_key in pool._queue:
            del pool._queue[session_key]

        logger.debug(
            "AgentPool: %s acquired slot on %s (active: %d/%d, queued: %d)",
            session_key, provider,
            pool.active_count, pool.max_concurrent,
            pool.queued_count,
        )

        return True

    async def release(self, session_key: str, provider: str) -> None:
        """
        Release a slot. Must be called after agent run completes.
        Releases the semaphore so the next queued session can proceed.
        """
        if not self._enabled:
            return

        pool = self.get_pool(provider)
        if session_key in pool._active:
            elapsed = time.time() - pool._active[session_key].started_at
            del pool._active[session_key]
            logger.debug(
                "AgentPool: %s released %s (ran %.1fs, active: %d/%d, queued: %d)",
                session_key, provider, elapsed,
                pool.active_count, pool.max_concurrent,
                pool.queued_count,
            )

        # Release the semaphore so the next waiter can proceed
        pool.semaphore.release()

        # Promote next in queue if any
        if pool._queue:
            next_key, next_event = next(iter(pool._queue.items()))
            logger.info(
                "AgentPool: promoting %s from queue on %s",
                next_key, provider,
            )
    
    def get_status(self) -> dict:
        """Get full pool status for diagnostics/display."""
        pools = {}
        for name, pool in self._pools.items():
            pools[name] = {
                "max": pool.max_concurrent,
                "active": pool.active_count,
                "available": pool.available,
                "queued": pool.queued_count,
            }
        pools["__default__"] = {
            "max": self._default_pool.max_concurrent,
            "active": self._default_pool.active_count,
            "available": self._default_pool.available,
            "queued": self._default_pool.queued_count,
        }
        return {
            "enabled": self._enabled,
            "pools": pools,
        }


class AgentPoolContext:
    """
    Async context manager for pool slot acquisition.
    
    Usage:
        async with AgentPoolContext(pool, session_key, provider):
            await run_agent(...)
    """
    
    def __init__(self, pool: AgentPool, session_key: str, provider: str):
        self.pool = pool
        self.session_key = session_key
        self.provider = provider
        self.acquired = False
    
    async def __aenter__(self):
        self.acquired = await self.pool.acquire(self.session_key, self.provider)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.acquired:
            await self.pool.release(self.session_key, self.provider)
        self.acquired = False
