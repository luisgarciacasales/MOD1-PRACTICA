"""Pruebas de integración de la carga de noticias, contra PostgreSQL real.

El defecto que las motiva (21-sep-2026): `guid` es SHA-256 de
(source, url, published_at), así que identifica a una URL, no a un artículo.
Cuando la fuente reemite el mismo artículo con otra URL —google_news rota los
ids de su blob de redirección, El Economista corrigió un slug— el
`ON CONFLICT (guid)` no ve nada que colapsar y la noticia entra dos veces.

Se prueba contra la base, no leyendo el SQL: lo que fallaba era el
comportamiento del UPSERT, que ninguna comprobación de texto detecta.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from src.contracts import validar_noticia
from src.contracts.news import SilverNews
from src.pipeline import db
from src.pipeline.validate import normalizar_noticia

PUBLICADO = datetime(2026, 8, 31, 20, 21, 8, tzinfo=UTC)
TITULO = "Asaltan Banregio en Tampico - MILENIO"

# Las dos URLs reales del caso. El primer segmento de la segunda ES el segundo
# segmento de la primera: Google devolvió el artículo con el id primario y el
# alterno intercambiados.
URL_A = "https://news.google.com/rss/articles/CBMiaTE?oc=5"
URL_B = "https://news.google.com/rss/articles/CBMiaTI?oc=5"


def _noticia(url: str, *, titulo: str = TITULO, publicado: datetime = PUBLICADO):
    """Recorre el camino real: crudo del feed → `normalizar_noticia` (que hace
    la extracción léxica) → contrato. Construir el dict normalizado a mano
    saltaría justo la parte que rellena `tickers`, y con ella el caso que
    motiva el test de los casts."""
    crudo = {
        "source": "google_news",
        "title": titulo,
        "summary": "Sujetos armados asaltaron una sucursal de Banregio.",
        "link": url,
        "published": publicado.isoformat(),
    }
    noticia = validar_noticia(normalizar_noticia(crudo, fintechs=()), uuid4())
    assert isinstance(noticia, SilverNews), getattr(noticia, "rejection_detail", noticia)
    return noticia


def _guids(cur) -> list[str]:
    cur.execute("SELECT guid FROM silver_news ORDER BY guid")
    return [f[0] for f in cur.fetchall()]


def test_el_mismo_articulo_con_otra_url_no_entra_dos_veces(cur):
    """El defecto. Dos URLs, un artículo: la segunda se descarta."""
    primera = db.cargar_noticias(cur, [_noticia(URL_A)])
    segunda = db.cargar_noticias(cur, [_noticia(URL_B)])

    assert (primera.nuevas, primera.duplicadas) == (1, 0)
    assert (segunda.nuevas, segunda.duplicadas) == (0, 1)
    assert len(_guids(cur)) == 1


def test_la_duplicada_no_cuenta_como_actualizada(cur):
    """`_cargar` lee "sin fila devuelta" como actualización. Si `cargar_noticias`
    delegara en él, el duplicado se contaría como actualizado y desaparecería
    del resumen — que es justo donde tiene que verse."""
    db.cargar_noticias(cur, [_noticia(URL_A)])
    segunda = db.cargar_noticias(cur, [_noticia(URL_B)])

    assert segunda.actualizadas == 0
    assert segunda.duplicadas == 1
    assert segunda.total == 0


def test_reingerir_la_misma_url_sigue_actualizando(cur):
    """La idempotencia que el guard viene a defender no puede romperla él mismo.

    Sin el `n.guid <> %(guid)s` del NOT EXISTS, la misma URL reingerida se
    descartaría como duplicado en vez de actualizar, y `validate --todo`
    dejaría de refrescar nada."""
    db.cargar_noticias(cur, [_noticia(URL_A)])
    otra_vez = db.cargar_noticias(cur, [_noticia(URL_A)])

    assert (otra_vez.nuevas, otra_vez.actualizadas, otra_vez.duplicadas) == (0, 1, 0)


def test_un_titular_editado_en_la_misma_url_actualiza_no_duplica(cur):
    """Mismo guid, titular distinto: el NOT EXISTS no encuentra pareja y la
    fila se actualiza por la vía normal del ON CONFLICT."""
    db.cargar_noticias(cur, [_noticia(URL_A)])
    editada = db.cargar_noticias(
        cur, [_noticia(URL_A, titulo="Asaltan Banregio en Tampico - MILENIO (actualizado)")]
    )

    assert (editada.actualizadas, editada.duplicadas) == (1, 0)
    cur.execute("SELECT title FROM silver_news")
    assert cur.fetchone()[0].endswith("(actualizado)")


def test_dos_articulos_distintos_del_mismo_medio_conviven(cur):
    """El guard es conservador a propósito: colapsar de más fundiría dos
    artículos reales, que es un daño peor y silencioso que dejar un duplicado."""
    db.cargar_noticias(cur, [_noticia(URL_A)])
    otro = db.cargar_noticias(
        cur, [_noticia(URL_B, titulo="Banregio amplía su red en Tampico - MILENIO")]
    )

    assert otro.nuevas == 1
    assert len(_guids(cur)) == 2


def test_misma_nota_publicada_otro_dia_es_otra_noticia(cur):
    """Las columnas recurrentes ("las bolsas cerraron con caídas") repiten
    titular cada jornada y son notas distintas. `published_at` entra en la
    clave justamente para no fundirlas."""
    db.cargar_noticias(cur, [_noticia(URL_A)])
    manana = db.cargar_noticias(
        cur, [_noticia(URL_B, publicado=datetime(2026, 9, 1, 20, 21, 8, tzinfo=UTC))]
    )

    assert manana.nuevas == 1
    assert len(_guids(cur)) == 2


def test_una_noticia_sin_tickers_se_carga(cur):
    """Con `VALUES`, Postgres deducía el tipo de cada parámetro de la columna
    destino; el `SELECT` del guard se resuelve antes de mirar el destino. Sin
    los casts explícitos, un `tickers` en NULL viaja como TEXT y la carga
    revienta con "column tickers is of type text[]" — en la mayoría del corpus.
    """
    sin_ticker = _noticia(
        URL_A,
        titulo="El Banco de México mantiene la tasa de referencia sin cambios",
    )
    assert sin_ticker.tickers is None

    carga = db.cargar_noticias(cur, [sin_ticker])
    assert carga.nuevas == 1
