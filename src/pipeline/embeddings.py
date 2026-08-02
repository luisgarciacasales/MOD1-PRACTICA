"""Generación de embeddings en español (PRD §6.5, ADR-9).

Dos backends conmutables por `EMBEDDING_BACKEND`, ambos de **1024 dimensiones**
para encajar en `gold_enriched_news.embedding vector(1024)`:

· `sentence_transformers` → `intfloat/multilingual-e5-large`. Es lo que fija el
  PRD §6.5. Descarga ~2,2 GB la primera vez, que quedan en `data/hf_cache`.
· `ollama` → `bge-m3`, ya cargado en el `lab-ollama` compartido. Cero descarga
  y cero torch, pero disputa la VRAM con el modelo de inferencia.

Los vectores se devuelven **normalizados a norma 1**. Con eso, el producto
interno de FAISS (`IndexFlatIP`) es exactamente la similitud coseno que pide el
PRD, sin tener que normalizar en cada consulta.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol

import numpy as np

from src.config import get_settings

# e5 exige prefijos: fue entrenado con "query: " para consultas y "passage: "
# para documentos, y omitirlos degrada la recuperación de forma notable —el
# modelo deja de distinguir el rol de cada texto—. bge-m3 no los usa.
PREFIJO_CONSULTA = "query: "
PREFIJO_DOCUMENTO = "passage: "


class Embebedor(Protocol):
    nombre: str

    def documentos(self, textos: list[str]) -> np.ndarray: ...
    def consulta(self, texto: str) -> np.ndarray: ...


def _normalizar(v: np.ndarray) -> np.ndarray:
    normas = np.linalg.norm(v, axis=1, keepdims=True)
    # Un vector nulo dividiría por cero; se deja como está (su similitud con
    # cualquier cosa será 0, que es la respuesta honesta).
    normas[normas == 0] = 1.0
    return (v / normas).astype(np.float32)


class EmbebedorSentenceTransformers:
    """`intfloat/multilingual-e5-large` en local (CPU o GPU según disponibilidad)."""

    def __init__(self, modelo: str) -> None:
        self.nombre = modelo
        self._modelo = None

    def _cargar(self):
        if self._modelo is None:
            from sentence_transformers import SentenceTransformer

            # Se carga en la primera llamada, no al importar: `verify` y otras
            # etapas importan este módulo sin necesitar el modelo, y arrastrar
            # 2 GB a memoria para nada retrasaría cada arranque.
            self._modelo = SentenceTransformer(self.nombre)
        return self._modelo

    def documentos(self, textos: list[str]) -> np.ndarray:
        v = self._cargar().encode(
            [PREFIJO_DOCUMENTO + t for t in textos],
            batch_size=16,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return _normalizar(np.asarray(v, dtype=np.float32))

    def consulta(self, texto: str) -> np.ndarray:
        v = self._cargar().encode(
            [PREFIJO_CONSULTA + texto], show_progress_bar=False, convert_to_numpy=True
        )
        return _normalizar(np.asarray(v, dtype=np.float32))


class EmbebedorOllama:
    """`bge-m3` a través del lab-ollama compartido. Sin prefijos y sin torch."""

    def __init__(self, modelo: str = "bge-m3") -> None:
        self.nombre = modelo
        self._url = get_settings().ollama_base_url.rstrip("/")

    def _embeber(self, textos: list[str]) -> np.ndarray:
        import requests

        respuesta = requests.post(
            f"{self._url}/api/embed",
            json={"model": self.nombre, "input": textos},
            timeout=180,
        )
        respuesta.raise_for_status()
        return _normalizar(np.asarray(respuesta.json()["embeddings"], dtype=np.float32))

    def documentos(self, textos: list[str]) -> np.ndarray:
        return self._embeber(textos)

    def consulta(self, texto: str) -> np.ndarray:
        return self._embeber([texto])


@lru_cache(maxsize=1)
def obtener_embebedor() -> Embebedor:
    """El backend configurado en el `.env` del servidor (ADR-9).

    **Cacheado a propósito.** Sin el `lru_cache`, cada llamada devolvía una
    instancia nueva cuyo modelo se cargaba de cero: `search_semantic()` pagaba
    ~2 s de carga en *cada* consulta y el SLA de 500 ms del PRD §7 era
    inalcanzable por construcción. El modelo pesa 2 GB y es de solo lectura;
    compartirlo en el proceso es lo correcto.
    """
    settings = get_settings()
    if settings.embedding_backend == "ollama":
        return EmbebedorOllama()
    return EmbebedorSentenceTransformers(settings.embedding_model)


def verificar_dimension(vectores: np.ndarray) -> None:
    """La dimensión debe coincidir con la columna `vector(1024)`.

    Se comprueba explícitamente porque el fallo alternativo es un error de
    psycopg a mitad de la carga, con la mitad de los embeddings escritos y un
    mensaje que no menciona el modelo.
    """
    esperada = get_settings().embedding_dim
    if vectores.shape[1] != esperada:
        raise ValueError(
            f"el modelo produce vectores de {vectores.shape[1]} dimensiones y la "
            f"columna espera {esperada}. Ajusta EMBEDDING_MODEL o el esquema."
        )
