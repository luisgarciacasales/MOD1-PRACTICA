"""Escritura de lotes en la capa Bronze (PRD §5.1 y §6.1).

Bronze es **inmutable y sin transformar**: los registros se persisten tal como
los devolvió la fuente, y este módulo es el único que escribe ahí.

Layout, según el skill medallion-pipeline:

    data/bronze/{categoria}/{source}/{YYYY-MM-DD}/{batch_id}/
        metadata.json        batch_uuid, source, ingested_at, record_count, checksum
        raw_payload.json     array de objetos, exactamente como se recibieron
        raw_payload.parquet  mismo contenido en columnar, para exploración

La inmutabilidad se defiende de dos formas: el directorio del lote incluye un
UUID (nunca colisiona, nunca se sobrescribe) y los archivos se dejan en modo
solo-lectura tras escribirse.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

_MODO_SOLO_LECTURA = 0o444


@dataclass(frozen=True)
class LoteBronze:
    """Resultado de persistir un lote. Su `batch_uuid` viaja a Silver como
    `raw_batch_uuid`, cerrando la trazabilidad de punta a punta."""

    batch_uuid: UUID
    source: str
    categoria: str
    ingested_at: datetime
    record_count: int
    checksum_sha256: str
    ruta: Path


def escribir_lote(
    registros: list[dict[str, Any]],
    *,
    source: str,
    categoria: str,
    fecha: date,
    raiz_bronze: Path,
) -> LoteBronze:
    """Persiste un lote en Bronze y devuelve su descriptor.

    `categoria` es "news" o "market" — la partición de primer nivel del PRD.
    """
    batch_uuid = uuid4()
    ingested_at = datetime.now(UTC)

    # Serialización canónica (claves ordenadas, sin espacios superfluos): el
    # checksum debe depender del contenido, no de cómo lo formateó json.dumps.
    payload = json.dumps(
        registros, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    destino = raiz_bronze / categoria / source / fecha.isoformat() / batch_uuid.hex
    destino.mkdir(parents=True, exist_ok=False)

    metadata = {
        "batch_uuid": str(batch_uuid),
        "source": source,
        "categoria": categoria,
        "fecha_lote": fecha.isoformat(),
        "ingested_at": ingested_at.isoformat(),
        "record_count": len(registros),
        "checksum_sha256": checksum,
    }

    _escribir(destino / "raw_payload.json", payload)
    _escribir(
        destino / "metadata.json",
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
    )
    _escribir_parquet(destino / "raw_payload.parquet", registros)

    return LoteBronze(
        batch_uuid=batch_uuid,
        source=source,
        categoria=categoria,
        ingested_at=ingested_at,
        record_count=len(registros),
        checksum_sha256=checksum,
        ruta=destino,
    )


def leer_lote(ruta: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Devuelve `(metadata, registros)` de un lote ya escrito."""
    metadata = json.loads((ruta / "metadata.json").read_text(encoding="utf-8"))
    registros = json.loads((ruta / "raw_payload.json").read_text(encoding="utf-8"))
    return metadata, registros


def leer_metadata(ruta: Path) -> dict[str, Any]:
    """Solo el `metadata.json` del lote, sin cargar el payload.

    Para decidir si un lote toca procesarlo basta con `source` y `batch_uuid`;
    `leer_lote` traería además los registros —hasta 8.520 en un lote de
    `yahoo_finance`— para leer dos campos.
    """
    return json.loads((ruta / "metadata.json").read_text(encoding="utf-8"))


def verificar_checksum(ruta: Path) -> bool:
    """¿El payload sigue siendo el que se escribió? Detecta corrupción o
    manipulación de Bronze, que por contrato nunca debería cambiar."""
    metadata = json.loads((ruta / "metadata.json").read_text(encoding="utf-8"))
    crudo = (ruta / "raw_payload.json").read_text(encoding="utf-8")
    actual = hashlib.sha256(crudo.encode("utf-8")).hexdigest()
    return actual == metadata["checksum_sha256"]


def listar_lotes(raiz_bronze: Path, *, categoria: str | None = None,
                 source: str | None = None, fecha: date | None = None) -> list[Path]:
    """Enumera directorios de lote, opcionalmente filtrando. Lo consume la
    etapa de validación para saber qué hay pendiente de procesar.

    **En orden cronológico real**, que no es el de la ruta. El directorio
    termina en el UUID del lote, así que un `sorted()` sobre la ruta ordena por
    UUID —es decir, al azar— en cuanto hay más de un lote de la misma fuente el
    mismo día. Eso importa porque Bronze es acumulativo y el UPSERT deja ganar
    al último aplicado: un lote que corrige a otro anterior puede quedar
    revertido por él, y el resultado de reprocesar depende de qué UUID salió.

    Se detectó el 31-ago-2026 con tres lotes de `reportes_pdf` del mismo día
    (29 registros, 34 con un defecto de extracción y 34 corregidos): tras
    `validate --todo` ganó el defectuoso, porque su UUID empezaba por `f` y el
    del bueno por `b`.
    """
    patron = "/".join([
        categoria or "*",
        source or "*",
        fecha.isoformat() if fecha else "*",
        "*",
    ])
    return sorted(
        (p for p in raiz_bronze.glob(patron) if (p / "metadata.json").exists()),
        key=_orden_cronologico,
    )


def _orden_cronologico(lote: Path) -> tuple[str, str, str]:
    """`(fecha_lote, ingested_at, nombre)` leídos del metadata.

    El nombre solo desempata lotes escritos en el mismo instante, para que el
    orden sea total y estable. Un metadata ilegible va al principio: es un lote
    anómalo y lo que se quiera corregir después debe poder pisarlo.
    """
    try:
        meta = json.loads((lote / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return ("", "", lote.name)
    return (meta.get("fecha_lote", ""), meta.get("ingested_at", ""), lote.name)


# --- Interno ---------------------------------------------------------------


def _escribir(ruta: Path, contenido: str) -> None:
    ruta.write_text(contenido, encoding="utf-8")
    os.chmod(ruta, _MODO_SOLO_LECTURA)


def _escribir_parquet(ruta: Path, registros: list[dict[str, Any]]) -> None:
    """Escribe la versión columnar.

    Los payloads crudos son irregulares: un RSS trae listas anidadas y campos
    que aparecen en unas entradas y no en otras, y pyarrow no puede inferir un
    esquema estable de eso. Los valores no escalares se serializan a JSON en
    lugar de perderse — Parquet aquí es para exploración, y el JSON de al lado
    sigue siendo la copia fiel que manda.
    """
    import pandas as pd

    if not registros:
        # Parquet sin columnas no es representable; un archivo vacío deja
        # constancia de que el lote existió y estaba vacío.
        ruta.write_bytes(b"")
        os.chmod(ruta, _MODO_SOLO_LECTURA)
        return

    aplanados = [
        {
            k: (v if isinstance(v, str | int | float | bool | type(None))
                else json.dumps(v, ensure_ascii=False, default=str))
            for k, v in registro.items()
        }
        for registro in registros
    ]
    pd.DataFrame(aplanados).to_parquet(ruta, index=False)
    os.chmod(ruta, _MODO_SOLO_LECTURA)
