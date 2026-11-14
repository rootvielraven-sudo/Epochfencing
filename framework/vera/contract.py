# Autor: @Raven
"""VERA — carga de CONTRATOS por tipo de documento desde la config de MIKA.

Convención (no hardcodeado): ``config/<cliente>/vera_contracts.yml`` es un mapping

    <doc_type>:
      entities:
        - {name: "Total a pagar", type: currency_amount, currency: ARS, aliases: ["Total"]}
        - {name: "Fecha",         type: date, locale: es-AR}
        - {name: "CUIT emisor",   type: identifier, fmt: cuit}
        - ...

Cada entrada se valida con ``vera_coverage.load_contract`` (que exige name+type y
valida fmt/currency). Sin fichero o sin ese doc_type → ``None`` (el pipeline cae al
camino legacy). El LLM nunca ve el tipo; el contrato vive solo en VERA.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import yaml

from .vera_coverage import load_contract
from .vera_schema import EntitySpec

_FILENAME = "vera_contracts.yml"


def _contracts_path(client: str, config_root: str) -> str:
    return os.path.join(config_root, client, _FILENAME)


@lru_cache(maxsize=64)
def _load_file(path: str, mtime: float) -> dict[str, Any]:
    """Lee y cachea el YAML de contratos (invalida por mtime)."""
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: se esperaba un mapping doc_type→contrato")
    return data


def _read(client: str, config_root: str) -> dict[str, Any] | None:
    path = _contracts_path(client, config_root)
    if not os.path.isfile(path):
        return None
    return _load_file(path, os.path.getmtime(path))


def load_contract_for(
    doc_type: str | None,
    *,
    client: str = "default",
    config_root: str = "config",
) -> list[EntitySpec] | None:
    """Devuelve el contrato (lista de EntitySpec) para ``doc_type``, o ``None`` si no hay
    fichero, no está ese tipo, o ``doc_type`` es None. Cae a ``default`` si el cliente no
    tiene fichero propio."""
    if not doc_type:
        return None
    raw = _read(client, config_root)
    if raw is None and client != "default":
        raw = _read("default", config_root)
    if not raw or doc_type not in raw:
        return None
    return load_contract(raw[doc_type])


def available_doc_types(client: str = "default", config_root: str = "config") -> list[str]:
    """Tipos de documento con contrato declarado para ese cliente (o default)."""
    raw = _read(client, config_root) or (_read("default", config_root) if client != "default" else None)
    return sorted(raw.keys()) if raw else []
