"""Etapa 7 — Indexación vectorial (Gold → FAISS). PRD §4.4 paso 7, §6.5.

Vectoriza las noticias enriquecidas, persiste el vector en `pgvector` y
construye o actualiza el índice FAISS en NVMe.

**`IndexFlatIP` sobre vectores normalizados = similitud coseno.** No se usa
`ivfflat` ni `hnsw`: son índices aproximados que necesitan entrenamiento y solo
compensan a partir de cientos de miles de vectores. Con este volumen, la
búsqueda exacta es más rápida que el entrenamiento y no tiene pérdida de
precisión (PRD §7 pide <500 ms; la exacta responde en microsegundos).

**Por qué el vector va también a PostgreSQL:** FAISS vive en un archivo y no
sabe nada de las demás columnas. Guardarlo en `gold_enriched_news.embedding`
permite reconstruir el índice sin volver a pagar la vectorización, y deja la
puerta abierta a consultas híbridas (`WHERE sentiment_label = 'negative'` +
distancia) que FAISS por sí solo no puede resolver.

    docker compose exec -T app python -m src.pipeline.index
    docker compose exec -T app python -m src.pipeline.index --rebuild
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import psycopg

from src.config import get_settings
from src.pipeline import db
from src.pipeline.embeddings import obtener_embebedor, verificar_dimension

# Tamaño de lote para la vectorización. No tiene relación con NLP_BATCH_SIZE:
# aquí no hay concurrencia de red, solo pasadas por el modelo.
LOTE = 32


def _texto_a_vectorizar(titulo: str, contenido: str) -> str:
    """Titular + cuerpo. El titular se incluye porque concentra la carga
    semántica de la nota y en un embedding promediado pesa más que diluido."""
    return f"{titulo}\n\n{contenido}".strip()


def _ruta_indice() -> Path:
    return Path(get_settings().faiss_index_path)


def construir_indice(ids: np.ndarray, vectores: np.ndarray):
    """Índice plano con IDs propios.

    `IndexIDMap2` permite usar el `id` de `gold_enriched_news` como etiqueta,
    en vez de la posición dentro del índice. Sin eso, cualquier reconstrucción
    parcial desplazaría las posiciones y las respuestas apuntarían a otras
    noticias — un fallo silencioso y difícil de detectar.
    """
    import faiss

    dim = vectores.shape[1]
    indice = faiss.IndexIDMap2(faiss.IndexFlatIP(dim))
    indice.add_with_ids(vectores, ids.astype(np.int64))
    return indice


def guardar_indice(indice) -> Path:
    import faiss

    ruta = _ruta_indice()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    # Escritura atómica: un corte a mitad dejaría un .index truncado que FAISS
    # carga sin quejarse pero devuelve resultados incompletos.
    temporal = ruta.with_suffix(".index.tmp")
    faiss.write_index(indice, str(temporal))
    temporal.replace(ruta)
    return ruta


def cargar_indice():
    import faiss

    ruta = _ruta_indice()
    return faiss.read_index(str(ruta)) if ruta.exists() else None


def ejecutar(*, rebuild: bool, limite: int) -> int:
    settings = get_settings()
    embebedor = obtener_embebedor()

    condicion = "TRUE" if rebuild else "embedding IS NULL"
    with db.conectar() as conexion, conexion.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            f"""SELECT id, guid, title, content FROM gold_enriched_news
                WHERE {condicion} ORDER BY id LIMIT %s""",
            (limite,),
        )
        pendientes = cur.fetchall()

    if not pendientes:
        print("[index] no hay noticias pendientes de vectorizar")
        # Aun así se reconstruye el índice desde pgvector: puede faltar el
        # archivo aunque los vectores existan.
    else:
        print(
            f"[index] vectorizando {len(pendientes)} noticias "
            f"· backend={settings.embedding_backend} · modelo={embebedor.nombre}"
        )
        inicio = time.monotonic()
        with db.conectar() as conexion, conexion.cursor() as cur:
            for i in range(0, len(pendientes), LOTE):
                trozo = pendientes[i : i + LOTE]
                vectores = embebedor.documentos(
                    [_texto_a_vectorizar(f["title"], f["content"]) for f in trozo]
                )
                verificar_dimension(vectores)
                for fila, vector in zip(trozo, vectores, strict=True):
                    cur.execute(
                        "UPDATE gold_enriched_news SET embedding = %s WHERE id = %s",
                        (vector.tolist(), fila["id"]),
                    )
                print(f"[index]   {min(i + LOTE, len(pendientes))}/{len(pendientes)}", flush=True)
            conexion.commit()
        print(f"[index] vectorización en {time.monotonic() - inicio:.1f} s")

    # --- Índice FAISS desde pgvector ----------------------------------------
    # Se reconstruye siempre desde la base y no de forma incremental sobre el
    # archivo: con este volumen cuesta milisegundos y elimina toda una clase de
    # bugs de desincronización entre el índice y la tabla.
    with db.conectar() as conexion, conexion.cursor() as cur:
        cur.execute(
            "SELECT id, embedding FROM gold_enriched_news WHERE embedding IS NOT NULL ORDER BY id"
        )
        filas = cur.fetchall()

    if not filas:
        print("[index] no hay embeddings que indexar")
        return 1

    ids = np.array([f[0] for f in filas], dtype=np.int64)
    vectores = np.array(
        [np.fromstring(f[1].strip("[]"), sep=",") if isinstance(f[1], str) else f[1]
         for f in filas],
        dtype=np.float32,
    )
    verificar_dimension(vectores)

    inicio = time.monotonic()
    indice = construir_indice(ids, vectores)
    ruta = guardar_indice(indice)
    construccion = time.monotonic() - inicio

    print()
    print(f"{'vectores en el índice':<26} {indice.ntotal}")
    print(f"{'dimensión':<26} {vectores.shape[1]}")
    print(f"{'archivo':<26} {ruta} ({ruta.stat().st_size / 1024:.0f} KB)")
    print(f"{'construcción':<26} {construccion:.2f} s  (SLA §7: <30 s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="src.pipeline.index",
        description="Vectoriza las noticias enriquecidas y construye el índice FAISS.",
    )
    parser.add_argument("--rebuild", action="store_true",
                        help="Recalcula los embeddings de todas las noticias.")
    parser.add_argument("--limit", type=int, default=10_000)
    args = parser.parse_args(argv)
    return ejecutar(rebuild=args.rebuild, limite=args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
