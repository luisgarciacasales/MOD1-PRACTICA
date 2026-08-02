"""Pruebas del escritor de Bronze.

Verifican las dos garantías del PRD §6.1 que el resto del pipeline da por
sentadas: **inmutabilidad** y **trazabilidad por checksum**.
"""

from __future__ import annotations

import json
import os
from datetime import date

import pytest

from src.pipeline.bronze import (
    escribir_lote,
    leer_lote,
    listar_lotes,
    verificar_checksum,
)

FECHA = date(2026, 7, 31)
REGISTROS = [
    {"title": "Banorte reporta utilidades", "tickers": ["GFNORTEO"], "source": "financiero"},
    {"title": "Banxico mantiene la tasa", "tickers": [], "source": "bloomberg"},
]


def escribir(tmp_path, registros=REGISTROS, source="financiero", categoria="news"):
    return escribir_lote(
        registros, source=source, categoria=categoria, fecha=FECHA, raiz_bronze=tmp_path
    )


def test_layout_sigue_el_prd(tmp_path):
    lote = escribir(tmp_path)
    relativa = lote.ruta.relative_to(tmp_path)
    assert relativa.parts[:3] == ("news", "financiero", "2026-07-31")
    assert relativa.parts[3] == lote.batch_uuid.hex


def test_escribe_los_tres_archivos(tmp_path):
    lote = escribir(tmp_path)
    for nombre in ("metadata.json", "raw_payload.json", "raw_payload.parquet"):
        assert (lote.ruta / nombre).exists(), nombre


def test_metadata_completa(tmp_path):
    lote = escribir(tmp_path)
    meta = json.loads((lote.ruta / "metadata.json").read_text())
    assert meta["batch_uuid"] == str(lote.batch_uuid)
    assert meta["source"] == "financiero"
    assert meta["record_count"] == 2
    assert len(meta["checksum_sha256"]) == 64


def test_payload_no_se_transforma(tmp_path):
    """Bronze inmutable: lo que entra es exactamente lo que se lee."""
    lote = escribir(tmp_path)
    _, registros = leer_lote(lote.ruta)
    assert registros == REGISTROS


def test_checksum_detecta_manipulacion(tmp_path):
    lote = escribir(tmp_path)
    assert verificar_checksum(lote.ruta) is True

    destino = lote.ruta / "raw_payload.json"
    os.chmod(destino, 0o644)  # hay que forzar: se escribió como solo-lectura
    destino.write_text('[{"title": "alterado"}]', encoding="utf-8")
    assert verificar_checksum(lote.ruta) is False


def test_archivos_quedan_en_solo_lectura(tmp_path):
    """Primera defensa de la inmutabilidad."""
    lote = escribir(tmp_path)
    for nombre in ("metadata.json", "raw_payload.json"):
        modo = (lote.ruta / nombre).stat().st_mode & 0o777
        assert modo == 0o444, f"{nombre} tiene modo {oct(modo)}"

    with pytest.raises(PermissionError):
        (lote.ruta / "raw_payload.json").write_text("sobrescrito")


def test_dos_lotes_del_mismo_dia_no_colisionan(tmp_path):
    """Segunda defensa: el UUID en la ruta. El PRD §8 pide 2+ lotes diarios."""
    a = escribir(tmp_path)
    b = escribir(tmp_path)
    assert a.ruta != b.ruta
    assert a.batch_uuid != b.batch_uuid
    assert len(listar_lotes(tmp_path)) == 2


def test_checksum_es_estable_ante_el_orden_de_claves(tmp_path):
    """El checksum debe depender del contenido, no de cómo se serializó."""
    a = escribir(tmp_path, [{"b": 2, "a": 1}])
    b = escribir(tmp_path, [{"a": 1, "b": 2}])
    assert a.checksum_sha256 == b.checksum_sha256


def test_listar_filtra_por_fuente_y_categoria(tmp_path):
    escribir(tmp_path, source="financiero", categoria="news")
    escribir(tmp_path, source="banxico", categoria="market")
    assert len(listar_lotes(tmp_path)) == 2
    assert len(listar_lotes(tmp_path, categoria="market")) == 1
    assert len(listar_lotes(tmp_path, source="financiero")) == 1
    assert len(listar_lotes(tmp_path, fecha=date(2020, 1, 1))) == 0


def test_lote_vacio_no_revienta(tmp_path):
    lote = escribir(tmp_path, [])
    assert lote.record_count == 0
    assert (lote.ruta / "raw_payload.parquet").exists()


def test_registros_anidados_sobreviven_al_parquet(tmp_path):
    """Los RSS traen listas y dicts anidados; el JSON debe conservarlos íntegros."""
    anidado = [{"title": "x", "tags": [{"term": "mercados"}], "source": "financiero"}]
    lote = escribir(tmp_path, anidado)
    _, registros = leer_lote(lote.ruta)
    assert registros[0]["tags"] == [{"term": "mercados"}]
