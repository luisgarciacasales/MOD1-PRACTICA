"""Pruebas de la normalización de embeddings y del contrato de dimensión."""

from __future__ import annotations

import numpy as np
import pytest

from src.pipeline.embeddings import (
    PREFIJO_CONSULTA,
    PREFIJO_DOCUMENTO,
    _normalizar,
    verificar_dimension,
)


def test_normaliza_a_norma_unitaria():
    """Con vectores de norma 1, el producto interno de FAISS ES el coseno."""
    v = _normalizar(np.array([[3.0, 4.0], [1.0, 0.0]], dtype=np.float32))
    assert np.allclose(np.linalg.norm(v, axis=1), 1.0)


def test_vector_nulo_no_divide_por_cero():
    v = _normalizar(np.array([[0.0, 0.0]], dtype=np.float32))
    assert np.all(np.isfinite(v))


def test_devuelve_float32_para_faiss():
    """FAISS rechaza float64 con un error poco descriptivo."""
    assert _normalizar(np.array([[1.0, 2.0]], dtype=np.float64)).dtype == np.float32


def test_producto_interno_de_normalizados_es_coseno():
    a, b = np.array([[1.0, 0.0]]), np.array([[1.0, 1.0]])
    na, nb = _normalizar(a.astype(np.float32)), _normalizar(b.astype(np.float32))
    assert float(na @ nb.T) == pytest.approx(np.cos(np.pi / 4), abs=1e-6)


def test_los_prefijos_de_e5_son_distintos():
    """e5 fue entrenado distinguiendo consulta de documento; confundirlos
    degrada la recuperación."""
    assert PREFIJO_CONSULTA != PREFIJO_DOCUMENTO
    assert PREFIJO_CONSULTA.startswith("query")
    assert PREFIJO_DOCUMENTO.startswith("passage")


def test_dimension_incorrecta_falla_pronto():
    """Sin este chequeo el error llega desde psycopg a mitad de la carga, con
    la mitad de los embeddings escritos."""
    with pytest.raises(ValueError, match="dimensiones"):
        verificar_dimension(np.zeros((2, 768), dtype=np.float32))


def test_dimension_correcta_pasa():
    verificar_dimension(np.zeros((2, 1024), dtype=np.float32))
