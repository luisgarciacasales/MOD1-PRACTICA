"""Cliente asíncrono del `lab-ollama` compartido (PRD §2.1, §6.4).

Lotes de N llamadas simultáneas con `asyncio` + `aiohttp` para saturar la GPU
sin desbordar los 16 GB de VRAM. El paralelismo lo limita un semáforo, no el
tamaño de la lista: así se puede pasar el corpus entero y aun así nunca haber
más de N peticiones en vuelo.

Dos particularidades de `qwen3.5:9b` descubiertas al integrarlo, ambas
manejadas aquí para que no contaminen la lógica de negocio:

1. **Es un modelo de razonamiento.** Por defecto emite su cadena de pensamiento
   en el campo `thinking` y agota `num_predict` antes de escribir una sola
   palabra en `content` (`done_reason: length`, respuesta vacía). Se envía
   `think: false`.
2. **Envuelve el JSON en vallas de Markdown** pese a `format: "json"`, así que
   hay que despegarlas antes de parsear.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

import aiohttp

from src.config import get_settings

# Una respuesta vacía o un timeout puntual no deben costar la noticia: se
# reintenta con backoff. Más de dos reintentos no compensa — si el modelo no
# produjo JSON dos veces, el problema es el prompt, no la suerte.
REINTENTOS = 2
ESPERA_BASE = 1.5

_VALLA = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


@dataclass
class RespuestaLLM:
    ok: bool
    datos: dict[str, Any] | None = None
    error: str | None = None


def _despegar_vallas(texto: str) -> str:
    return _VALLA.sub("", texto).strip()


class ClienteOllama:
    """Cliente con concurrencia acotada. Úsalo como contexto asíncrono."""

    def __init__(self, *, concurrencia: int | None = None, timeout: int = 180) -> None:
        settings = get_settings()
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.concurrencia = concurrencia or settings.nlp_batch_size
        self._semaforo = asyncio.Semaphore(self.concurrencia)
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._sesion: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> ClienteOllama:
        self._sesion = aiohttp.ClientSession(timeout=self._timeout)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._sesion:
            await self._sesion.close()

    async def chat_json(
        self,
        *,
        modelo: str,
        sistema: str,
        usuario: str,
        num_predict: int = 800,
    ) -> RespuestaLLM:
        """Una inferencia que debe devolver JSON. Nunca lanza."""
        assert self._sesion is not None, "usa ClienteOllama como contexto asíncrono"

        cuerpo = {
            "model": modelo,
            "think": False,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": sistema},
                {"role": "user", "content": usuario},
            ],
            "options": {
                # Temperatura 0: la extracción de entidades y la clasificación
                # deben ser reproducibles. Un mismo texto tiene que dar el
                # mismo resultado en dos corridas o la idempotencia de Gold
                # deja de significar nada.
                "temperature": 0,
                "num_predict": num_predict,
            },
        }

        ultimo_error = "sin intentos"
        for intento in range(REINTENTOS + 1):
            async with self._semaforo:
                try:
                    async with self._sesion.post(
                        f"{self.base_url}/api/chat", json=cuerpo
                    ) as respuesta:
                        respuesta.raise_for_status()
                        payload = await respuesta.json()
                except Exception as exc:  # noqa: BLE001
                    ultimo_error = f"{type(exc).__name__}: {exc}"[:200]
                    payload = None

            if payload is not None:
                contenido = (payload.get("message") or {}).get("content") or ""
                if payload.get("done_reason") == "length" and not contenido.strip():
                    ultimo_error = "el modelo agotó num_predict sin emitir contenido"
                else:
                    try:
                        return RespuestaLLM(ok=True, datos=json.loads(_despegar_vallas(contenido)))
                    except json.JSONDecodeError as exc:
                        ultimo_error = f"JSON inválido: {exc}; crudo={contenido[:160]!r}"

            if intento < REINTENTOS:
                await asyncio.sleep(ESPERA_BASE * (intento + 1))

        return RespuestaLLM(ok=False, error=ultimo_error)


async def salud(base_url: str | None = None, timeout: int = 10) -> tuple[bool, str]:
    """¿Responde el lab-ollama compartido? Se comprueba antes del batch para
    fallar en 10 segundos en vez de tras 78 timeouts."""
    url = (base_url or get_settings().ollama_base_url).rstrip("/")
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as sesion, sesion.get(f"{url}/api/tags") as r:
            r.raise_for_status()
            modelos = [m["name"] for m in (await r.json()).get("models", [])]
            return True, f"{len(modelos)} modelos disponibles"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"[:200]
