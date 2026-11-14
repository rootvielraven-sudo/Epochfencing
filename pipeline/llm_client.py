# Autor: @Raven
"""Cliente LLM (OpenAI) para el reconstructor real (Modo B).

La API key y el modelo se leen EN TIEMPO DE EJECUCIÓN de un fichero de configuración del
usuario (por defecto ``/config/managerextractor-pro/config.yml``), nunca se escriben en el
código ni se registran en logs. Autorización del usuario: usar GPT (servicio de pago).

Contrato de privacidad: la key solo viaja a la API de OpenAI. El prompt de reconstrucción
contiene únicamente el OCR degradado y el esquema de dominio — ningún dato personal del
usuario ni del proyecto.

Config esperada (YAML):
    openai_key: {key: "sk-..."}
    config:     {model_4: "gpt-4o", model_3.5: "gpt-3.5-turbo"}

``available()`` no hace llamadas de red (no gasta): solo comprueba que haya key y paquete.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

DEFAULT_CONFIG = "/config/managerextractor-pro/config.yml"


@dataclass
class LLMConfig:
    api_key: str
    model: str
    source: str                      # de dónde salió (para trazabilidad, sin la key)


def load_llm_config(config_path: str | None = None, *, prefer: str = "model_4",
                    model_override: str | None = None) -> LLMConfig | None:
    """Carga key+modelo del YAML. Devuelve ``None`` si no hay fichero o no hay key."""
    path = config_path or os.environ.get("MEXTRACTOR_CONFIG", DEFAULT_CONFIG)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return None
    key = (((data.get("openai_key") or {}).get("key"))
           or os.environ.get("OPENAI_API_KEY"))
    if not key:
        return None
    cfgblk = data.get("config") or {}
    model = model_override or cfgblk.get(prefer) or cfgblk.get("model_4") or "gpt-4o"
    return LLMConfig(api_key=str(key), model=str(model), source=os.path.basename(path))


class LLMClient:
    """Envoltorio fino sobre el SDK de OpenAI. Determinista: ``temperature=0``."""

    def __init__(self, cfg: LLMConfig, *, temperature: float = 0.0, timeout: float = 120.0,
                 base_url: str | None = None):
        self.cfg = cfg
        self.temperature = temperature
        self.timeout = timeout
        self.base_url = base_url          # p.ej. Ollama OpenAI-compatible: http://127.0.0.1:11434/v1
        self._client = None
        # Contabilidad de tokens (coste medible). Se acumula entre llamadas.
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0

    @classmethod
    def from_config(cls, config_path: str | None = None, **kw) -> "LLMClient | None":
        cfg = load_llm_config(config_path)
        return cls(cfg, **kw) if cfg else None

    @classmethod
    def for_ollama(cls, model: str = "qwen3.5:35b",
                   host: str = "127.0.0.1:11434", **kw) -> "LLMClient":
        """Backend LOCAL y GRATIS: Ollama expone una API OpenAI-compatible en ``/v1``.
        La api_key es un placeholder (Ollama no la valida); nada viaja fuera del equipo."""
        cfg = LLMConfig(api_key="ollama-local", model=model, source="ollama")
        return cls(cfg, base_url=f"http://{host}/v1", **kw)

    def available(self) -> bool:
        try:
            import openai  # noqa: F401
        except Exception:
            return False
        return bool(self.cfg and self.cfg.api_key)

    def _ensure(self):
        if self._client is None:
            from openai import OpenAI
            kw = {"api_key": self.cfg.api_key, "timeout": self.timeout}
            if self.base_url:
                kw["base_url"] = self.base_url
            self._client = OpenAI(**kw)
        return self._client

    def complete_json(self, system: str, user: str) -> str:
        """Devuelve el texto (JSON) de la respuesta del modelo. Puede lanzar excepción de red.
        Reintenta sin ``response_format`` si el backend no lo soporta (algunos Ollama)."""
        client = self._ensure()
        base = dict(model=self.cfg.model, temperature=self.temperature,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}])
        try:
            resp = client.chat.completions.create(
                response_format={"type": "json_object"}, **base)
        except Exception as e:  # noqa: BLE001
            if "response_format" not in str(e) and "format" not in str(e).lower():
                raise
            resp = client.chat.completions.create(**base)
        self.calls += 1
        u = getattr(resp, "usage", None)
        if u is not None:
            self.prompt_tokens += getattr(u, "prompt_tokens", 0) or 0
            self.completion_tokens += getattr(u, "completion_tokens", 0) or 0
        return resp.choices[0].message.content or "{}"

    def ping(self) -> tuple[bool, str]:
        """Comprobación de disponibilidad REAL (hace una llamada mínima; gasta unos tokens)."""
        try:
            out = self.complete_json(
                "Responde solo JSON.", 'Devuelve {"ok": true}. Nada más.')
            return ("ok" in out.lower()), f"modelo={self.cfg.model} resp={out[:60]!r}"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"
