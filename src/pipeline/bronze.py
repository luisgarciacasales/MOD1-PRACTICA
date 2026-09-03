"""Escritura de lotes en la capa Bronze (PRD §5.1 y §6.1).

Bronze es **inmutable y sin transformar**: los registros se persisten tal como
los devolvió la fuente, y este módulo es el único que escribe ahí.

Layout, según el skill medallion-pipeline:

    data/bronze/{categoria}/{source}/{YYYY-MM-DD}/{batch_id}/
        metadata.json        batch_uuid, source, ingested_at, record_count, checksum
        raw_payload.json     array de objetos, exactamente como se recibieron
        raw_payload.parquet  mismo contenido en columnar, para exploración

    data/bronze/_fuentes/{ab}/{sha256}.{ext}
        El DOCUMENTO original cuando la fuente no es una API sino un archivo.

Ese último es el almacén de documentos, y existe por una asimetría que se
detectó el 2-sep-2026: para las diez fuentes de API o RSS, Bronze contiene
literalmente lo que devolvió la fuente, pero para las que nacen de un PDF
contenía el resultado de pasarle una expresión regular. El documento —lo
verdaderamente crudo— vivía en `data/manual_dropzone`, escribible y sin
checksum. El día que mejore el extractor, ese Bronze no sirve para reprocesar.

Se direcciona por CONTENIDO: el nombre del archivo es su propio SHA-256. Un
mismo reporte referenciado por cinco lotes se guarda una vez, y un archivo cuyo
contenido cambie es otro archivo, nunca una sobrescritura. Los dos primeros
caracteres del hash forman un subdirectorio para no dejar miles de entradas en
uno solo.

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
    fuentes: tuple[dict[str, Any], ...] = ()


def escribir_lote(
    registros: list[dict[str, Any]],
    *,
    source: str,
    categoria: str,
    fecha: date,
    raiz_bronze: Path,
    documentos: list[Path] | None = None,
) -> LoteBronze:
    """Persiste un lote en Bronze y devuelve su descriptor.

    `categoria` es "news" o "market" — la partición de primer nivel del PRD.

    `documentos` son los archivos de los que se extrajeron los registros, para
    las fuentes que no son una API. Se archivan en el almacén por contenido y el
    metadata guarda su nombre, tamaño y hash, de modo que el lote quede ligado al
    documento exacto que lo produjo y un extractor mejorado pueda reprocesarlo
    sin depender de que alguien conserve el original en su carpeta.
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

    archivadas = tuple(archivar_documento(d, raiz_bronze) for d in (documentos or []))

    metadata = {
        "batch_uuid": str(batch_uuid),
        "source": source,
        "categoria": categoria,
        "fecha_lote": fecha.isoformat(),
        "ingested_at": ingested_at.isoformat(),
        "record_count": len(registros),
        "checksum_sha256": checksum,
    }
    if archivadas:
        metadata["fuentes"] = list(archivadas)

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
        fuentes=archivadas,
    )


ALMACEN_FUENTES = "_fuentes"


def archivar_documento(origen: Path, raiz_bronze: Path) -> dict[str, Any]:
    """Copia un documento al almacén por contenido y devuelve su descriptor.

    Idempotente por construcción: si el archivo ya está —mismo contenido, mismo
    hash, misma ruta— no se vuelve a escribir. Eso permite reejecutar una
    ingesta sin duplicar 77 MB de reportes en cada corrida.
    """
    datos = origen.read_bytes()
    sha = hashlib.sha256(datos).hexdigest()

    destino = raiz_bronze / ALMACEN_FUENTES / sha[:2] / f"{sha}{origen.suffix.lower()}"
    if not destino.exists():
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(datos)
        os.chmod(destino, _MODO_SOLO_LECTURA)

    return {
        "nombre": origen.name,
        "sha256": sha,
        "bytes": len(datos),
        "ruta": str(destino.relative_to(raiz_bronze)),
    }


def ruta_documento(descriptor: dict[str, Any], raiz_bronze: Path) -> Path:
    """Del descriptor guardado en el metadata al archivo del almacén."""
    return raiz_bronze / descriptor["ruta"]


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
