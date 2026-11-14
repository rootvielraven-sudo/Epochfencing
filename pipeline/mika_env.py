# Autor: @Raven
"""Carga de configuración desde el ``.env`` de MIKA (gen-ai-backend): Redis y flags de VERA.

El ``.env`` trae credenciales reales → se trata como PRIVADO: nunca se imprime ni se envía a
ningún servicio externo. Solo se usa localmente para conectar a Redis.

Ruta del .env (en orden): variable de entorno ``MIKA_ENV`` → ``gen-ai-backend/.env`` del
repo mika → ``./.env`` local. Si no hay Redis alcanzable, ``redis_client`` devuelve ``None``
y el pipeline cae a los backends en memoria (degradación elegante).
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

_DEFAULT_PATHS = [
    os.environ.get("MIKA_ENV", ""),
    "/home/athenas/DESARROLLO/gen-ai-backend/.env",
    os.path.join(os.path.dirname(__file__), "..", ".env"),
]


@lru_cache(maxsize=4)
def load_env(path: str | None = None) -> dict[str, str]:
    """Parsea un ``.env`` a dict (sin exportar al entorno del proceso). Claves sin valor → ''."""
    candidates = [path] if path else _DEFAULT_PATHS
    env: dict[str, str] = {}
    for p in candidates:
        if p and os.path.isfile(p):
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
            env["_source_path"] = p
            break
    return env


def redis_config(path: str | None = None) -> dict[str, Any]:
    """Devuelve kwargs para ``redis.Redis(**cfg)`` desde el .env (sin la password en claro en logs)."""
    e = load_env(path)
    cfg: dict[str, Any] = {
        "host": e.get("REDIS_HOST", "127.0.0.1"),
        "port": int(e.get("REDIS_PORT", "6379") or "6379"),
        "socket_connect_timeout": 2,
        "socket_timeout": 2,
    }
    if e.get("REDIS_USERNAME"):
        cfg["username"] = e["REDIS_USERNAME"]
    if e.get("REDIS_PASSWORD"):
        cfg["password"] = e["REDIS_PASSWORD"]
    if e.get("REDIS_DB"):
        cfg["db"] = int(e["REDIS_DB"])
    return cfg


def redis_client(path: str | None = None, *, required: bool = False) -> Any | None:
    """Cliente Redis síncrono conectado y verificado con PING. ``None`` si no se puede (salvo
    ``required=True``, que levanta la excepción). No imprime credenciales."""
    try:
        import redis
    except ImportError:
        if required:
            raise
        return None
    cfg = redis_config(path)
    try:
        c = redis.Redis(**cfg)
        c.ping()
        return c
    except Exception as exc:
        if required:
            raise
        # No re-propagamos ni logueamos credenciales; solo el tipo de error.
        import logging
        logging.getLogger(__name__).warning(
            "Redis no alcanzable (%s) en %s:%s → fallback en memoria",
            type(exc).__name__, cfg.get("host"), cfg.get("port"))
        return None


def vera_flags(path: str | None = None) -> dict[str, str]:
    """Flags de VERA del .env (VERA_CALC, VERA_CALC_MODE, VERA_ENGINE, VERA_MODE)."""
    e = load_env(path)
    return {k: e[k] for k in ("VERA_CALC", "VERA_CALC_MODE", "VERA_ENGINE", "VERA_MODE")
            if k in e}
