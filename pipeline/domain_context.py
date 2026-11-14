# Autor: @Raven
"""Contexto de dominio φ que recibe el reconstructor LLM-GSR.

En el paper, R(ĉ, φ) condiciona la reconstrucción al contexto de dominio φ. Aquí φ es
EXACTAMENTE lo que un reconstructor *productivo* (Modo B/C) tiene permitido ver:

  - ``doc_type``        : el tipo de documento (p.ej. ``factura``).
  - ``fields``          : el ESQUEMA de campos esperados (nombre, tipo, alias, opcional).
  - ``calc_keys``       : las claves de nivel-extracción de las líneas/cálculo.
  - evidencia documental: el propio ``ocr_text`` degradado (se pasa aparte, por factura).

φ NO contiene el documento limpio ni la respuesta gold. Se construye desde el MISMO contrato
de VERA (``config/<client>/vera_contracts.yml``), de modo que el esquema que ve el LLM
coincide con el que luego verifica VERA, sin filtrar la verdad.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str
    aliases: tuple[str, ...] = ()
    optional: bool = False


@dataclass(frozen=True)
class DomainContext:
    """Contexto de dominio φ (sin verdad): esquema de campos + tipo de doc."""
    doc_type: str
    fields: tuple[FieldSpec, ...]
    calc_keys: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_contract(cls, doc_type: str = "factura", *, client: str = "default",
                      config_root: str = "config") -> "DomainContext":
        path = os.path.join(config_root, client, "vera_contracts.yml")
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        block = (data or {}).get(doc_type, {}) or {}
        specs: list[FieldSpec] = []
        for e in block.get("entities", []) or []:
            specs.append(FieldSpec(
                name=e["name"], type=e.get("type", "text"),
                aliases=tuple(e.get("aliases", []) or []),
                optional=bool(e.get("optional", False)),
            ))
        return cls(doc_type=doc_type, fields=tuple(specs),
                   calc_keys=block.get("calc", {}) or {})

    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]

    def schema_for_prompt(self) -> str:
        """Descripción legible del esquema para el prompt del LLM (sin ningún valor real)."""
        lines = []
        for f in self.fields:
            al = f" (alias: {', '.join(f.aliases)})" if f.aliases else ""
            opt = " [opcional]" if f.optional else ""
            lines.append(f'  - "{f.name}": {f.type}{opt}{al}')
        calc = self.calc_keys
        if calc:
            lines.append("  Además, si el detalle de líneas es legible, devolvé un array "
                         f'"{calc.get("lines", {}).get("array", "items")}" con objetos '
                         f'{{"{calc.get("lines", {}).get("qty", "cantidad")}", '
                         f'"{calc.get("lines", {}).get("price", "precio_unitario")}", '
                         f'"{calc.get("lines", {}).get("amount", "importe")}"}} y las claves '
                         f'de cálculo "{calc.get("subtotal", "subtotal")}", '
                         f'"{calc.get("total", "total")}", "importe_iva", "base_imponible", '
                         '"alicuota_iva".')
        return "\n".join(lines)
