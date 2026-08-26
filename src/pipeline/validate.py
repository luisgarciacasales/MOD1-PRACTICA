"""Etapas 2-3 — Validación y carga idempotente (Bronze → Silver). PRD §4.4.

Lee los lotes de Bronze, aplica los contratos de `src/contracts/` y carga con
`ON CONFLICT`. Todo registro acaba en algún sitio: o en su tabla Silver, o en
`silver_dead_letters` con un motivo tipado. Nunca se descarta en silencio.

Bronze no se toca: esta etapa solo lee. Cualquier corrección se hace aquí o
aguas abajo, jamás reescribiendo el lote original (PRD §6.1).

    docker compose exec -T app python -m src.pipeline.validate
    docker compose exec -T app python -m src.pipeline.validate --date 2026-08-02
    docker compose exec -T app python -m src.pipeline.validate --source financiero
    docker compose exec -T app python -m src.pipeline.validate --todo

Por defecto solo procesa lotes que no estén en `bronze_lotes_procesados`: Bronze
solo crece, y revalidar lo ya cargado no cambia Silver — solo cuesta. `--todo`
ignora ese registro y rehace la pasada completa, que es lo que hay que correr
tras cambiar un contrato (Silver se reconstruye desde Bronze) y lo que usa el
check de idempotencia de `verify`.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from src.config import get_settings
from src.config.banxico_series import SERIES_POR_ID
from src.config.inegi_series import INDICADORES_POR_ID
from src.contracts import (
    DeadLetter,
    FintechDictEntry,
    RejectionReason,
    validar_fundamental,
    validar_macro,
    validar_noticia,
    validar_precio,
)
from src.pipeline import db
from src.pipeline.bronze import leer_lote, leer_metadata, listar_lotes
from src.pipeline.extraccion import extraer_entidades, extraer_sector, extraer_tickers

FUENTES_NOTICIAS = {
    "eventos_relevantes", "financiero", "bloomberg", "google_news", "reportes_ir",
}

# Límites del contrato (PRD §5.2). Se aplican aquí, antes de validar, porque
# superarlos no significa que el dato sea inválido sino que trae markup.
LIMITE_CONTENIDO = 8192
LIMITE_TITULO = 1024


# --- Normalización Bronze → forma que espera el contrato -------------------


def texto_plano(html: str) -> str:
    """Quita el markup del cuerpo del artículo.

    Los feeds entregan el artículo completo en HTML: 10 500 caracteres de media
    y hasta 25 000, contra el límite de 8 192 del contrato. Sin esto, la mayor
    parte de las noticias se iría a cuarentena con TYPE_MISMATCH por longitud —
    un rechazo por formato disfrazado de rechazo por calidad.

    Limpiar aquí es correcto: Bronze conserva el HTML original intacto, y esto
    es la frontera Bronze→Silver, donde la normalización sí está permitida.
    Como efecto secundario mejora la extracción léxica, que ya no puede
    encontrar nombres de emisora dentro de URLs o atributos.
    """
    if "<" not in html:
        return html
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "lxml").get_text(" ", strip=True)


def acotar(texto: str, limite: int) -> str:
    """Recorta al límite del contrato por la última frontera de palabra.

    Se conserva el principio, que es donde el periodismo pone la información
    (pirámide invertida) y lo que más pesa para el NER y el sentimiento. El
    texto íntegro sigue disponible en Bronze, así que no se pierde nada:
    solo deja de viajar a Silver.
    """
    if len(texto) <= limite:
        return texto
    corte = texto.rfind(" ", 0, limite - 1)
    return texto[: corte if corte > limite // 2 else limite - 1].rstrip() + "…"


def _texto_de_entrada(crudo: dict[str, Any]) -> str:
    """El cuerpo de la noticia según lo que traiga el feed.

    Los feeds no coinciden en dónde ponen el texto: unos usan `summary`, otros
    `content[].value`, otros solo `description`. Se toma el más largo
    disponible para no perder señal en la extracción léxica.
    """
    candidatos: list[str] = []
    for clave in ("summary", "description", "subtitle"):
        valor = crudo.get(clave)
        if isinstance(valor, str):
            candidatos.append(valor)

    contenido = crudo.get("content")
    if isinstance(contenido, list):
        candidatos.extend(
            c["value"] for c in contenido if isinstance(c, dict) and isinstance(c.get("value"), str)
        )

    return max(candidatos, key=len, default="")


def _fecha_de_entrada(crudo: dict[str, Any]) -> str | None:
    """`published_parsed` ya viene en ISO desde el serializador de rss.py; si
    falta, se prueban las variantes textuales del feed."""
    for clave in ("published_parsed", "updated_parsed", "published", "updated", "created"):
        valor = crudo.get(clave)
        if isinstance(valor, str) and valor.strip():
            return valor
    return None


def normalizar_noticia(
    crudo: dict[str, Any], *, fintechs: tuple[str, ...]
) -> dict[str, Any]:
    """Traduce una entrada cruda de Bronze a la forma del contrato SilverNews.

    Aquí ocurre la extracción léxica: el feed no trae tickers etiquetados, así
    que se identifican sobre el texto (ver `src/pipeline/extraccion.py`). El
    contenido original no se modifica — solo se derivan campos nuevos.
    """
    titulo = acotar(texto_plano(str(crudo.get("title") or "")), LIMITE_TITULO)
    cuerpo = acotar(texto_plano(_texto_de_entrada(crudo)), LIMITE_CONTENIDO)

    tickers = extraer_tickers(titulo, cuerpo)
    entidades = extraer_entidades(titulo, cuerpo, fintechs=fintechs)
    sector = extraer_sector(titulo, cuerpo)

    return {
        "source": crudo.get("source"),
        "title": titulo,
        # Si el feed no trae cuerpo, el titular hace de contenido: el contrato
        # exige `content` no vacío y descartar por eso sería perder una noticia
        # que sí es identificable.
        "content": cuerpo or titulo,
        "url": crudo.get("link") or crudo.get("href") or crudo.get("id") or "",
        "published_at": _fecha_de_entrada(crudo),
        "tickers": tickers or None,
        "sector": sector,
        "entities": entidades or None,
    }


def serie_vigente(series_id: str | None) -> bool:
    """¿Sigue configurada esta serie de BANXICO?

    Bronze es inmutable, así que sus lotes antiguos conservan series que después
    se retiraron de la configuración. Sin este filtro, retirar una serie sería
    imposible: cada reproceso la resucitaría desde Bronze, y como el dato es
    numéricamente válido ningún contrato lo rechazaría — reaparecería en Gold sin
    nombre y contaminaría el `macro_context` de las correlaciones.

    No es un rechazo de calidad, es un dato fuera de alcance, así que no va a
    cuarentena. Se omite y se informa del conteo en el resumen.
    """
    return series_id in SERIES_POR_ID


def indicador_vigente(indicador_id: str | None) -> bool:
    """¿Sigue configurado este indicador del INEGI? Mismo motivo que
    `serie_vigente`: Bronze es inmutable y conserva lo que se retiró."""
    return indicador_id in INDICADORES_POR_ID


def normalizar_inegi(crudo: dict[str, Any]) -> dict[str, Any] | None:
    """Observación del INEGI → contrato MacroIndicator.

    Van a la MISMA tabla que las series de BANXICO en lugar de a una propia: son
    el mismo tipo de dato —serie temporal macro— y compartir tabla les da gratis
    el `yoy_change_pct` de `transform` y la inclusión en el `macro_context` de
    cada correlación. No hay colisión de claves: los `series_id` de BANXICO
    empiezan por letra (`SF`, `SP`) y los del INEGI son numéricos.

    Formatos de `TIME_PERIOD` según la frecuencia:

        anual       `2020`         → 1 de enero
        mensual     `2026/05`      → día 1 del mes
        quincenal   `2026/06/02`   → tercer segmento = QUINCENA (01 o 02)

    **La quincena importa.** Ignorar el tercer segmento hace que las dos
    quincenas de un mes caigan en el mismo día, y con la clave única
    `(series_id, date)` la segunda sobrescribe a la primera **sin error**: el
    INPC quincenal habría perdido la mitad de sus 925 observaciones en silencio.
    Se mapea la quincena 01 al día 1 y la 02 al día 16, convención habitual que
    preserva orden y unicidad.
    """
    periodo = str(crudo.get("periodo") or "").strip()
    texto_valor = str(crudo.get("valor") or "").replace(",", "").strip()
    try:
        partes = periodo.split("/")
        anio = int(partes[0])
        mes = int(partes[1]) if len(partes) > 1 else 1
        if len(partes) > 2:
            quincena = int(partes[2])
            if quincena not in (1, 2):
                return None
            dia = 1 if quincena == 1 else 16
        else:
            dia = 1
        fecha = date(anio, mes, dia)
        valor = float(texto_valor)
    except (ValueError, IndexError, AttributeError):
        return None
    return {"series_id": crudo.get("indicador_id"), "date": fecha, "value": valor}


def normalizar_macro(crudo: dict[str, Any]) -> dict[str, Any] | None:
    """Serie del SIE → contrato MacroIndicator.

    BANXICO entrega la fecha como dd/mm/aaaa y el valor como cadena, usando
    "N/E" para los no disponibles. Bronze lo guardó tal cual (correcto); la
    conversión ocurre aquí. Un "N/E" devuelve None y el llamador lo enruta a
    cuarentena con motivo, en vez de convertirlo en un 0.0 que mentiría.
    """
    texto_fecha = str(crudo.get("fecha") or "")
    texto_valor = str(crudo.get("dato") or "").replace(",", "").strip()
    try:
        dia, mes, anio = texto_fecha.split("/")
        fecha = date(int(anio), int(mes), int(dia))
        valor = float(texto_valor)
    except (ValueError, AttributeError):
        return None
    return {"series_id": crudo.get("series_id"), "date": fecha, "value": valor}


# --- Procesamiento por lote -------------------------------------------------


def _cargar_fintechs(cur, lotes: list[Path]) -> tuple[int, tuple[str, ...]]:
    """Carga el diccionario Finnovista y devuelve los nombres comerciales.

    Se procesa primero porque las noticias lo necesitan para reconocer fintechs
    como entidades.
    """
    nombres: list[str] = []
    total = db.Carga()
    for ruta in lotes:
        meta, registros = leer_lote(ruta)
        entradas = []
        for crudo in registros:
            datos = {k: v for k, v in crudo.items() if k != "source"}
            try:
                entrada = FintechDictEntry(**datos)
            except Exception:  # noqa: BLE001 — el diccionario es semilla revisada
                continue
            entradas.append(entrada)
            nombres.append(entrada.commercial_name)
        total += db.cargar_fintech(cur, entradas)
    return total.nuevas, tuple(nombres)


def procesar_lote(
    cur, ruta: Path, *, fintechs: tuple[str, ...]
) -> tuple[db.Carga, int, Counter, int]:
    """Valida y carga un lote. Devuelve (carga, rechazos, motivos, omitidas)."""
    meta, registros = leer_lote(ruta)
    source = meta["source"]
    batch_uuid = UUID(meta["batch_uuid"])

    validos: list[Any] = []
    rechazos: list[DeadLetter] = []
    motivos: Counter = Counter()
    omitidas = 0  # series de BANXICO ya retiradas de la configuración

    for crudo in registros:
        # Marcador de éxito parcial que inserta el adaptador de mercado; no es
        # un registro de datos.
        if crudo.get("_parciales"):
            continue

        if source in FUENTES_NOTICIAS:
            resultado = validar_noticia(normalizar_noticia(crudo, fintechs=fintechs), batch_uuid)
        elif source == "yahoo_finance":
            resultado = validar_precio(
                {k: v for k, v in crudo.items() if k != "source"}, batch_uuid
            )
        elif source in ("yahoo_fundamentals", "yahoo_fundamentals_anual"):
            resultado = validar_fundamental(
                {k: v for k, v in crudo.items() if k != "source"}, batch_uuid, source=source
            )
        elif source == "inegi":
            if not indicador_vigente(crudo.get("indicador_id")):
                omitidas += 1
                continue
            normalizado = normalizar_inegi(crudo)
            resultado = (
                validar_macro(normalizado, batch_uuid)
                if normalizado
                else DeadLetter(
                    source=source,
                    raw_payload=crudo,
                    rejection_reason=RejectionReason.TYPE_MISMATCH,
                    rejection_detail=(
                        f"periodo o valor no interpretable: "
                        f"{crudo.get('periodo')!r} / {crudo.get('valor')!r}"
                    ),
                    batch_uuid=batch_uuid,
                )
            )
        elif source == "banxico":
            if not serie_vigente(crudo.get("series_id")):
                omitidas += 1
                continue
            normalizado = normalizar_macro(crudo)
            resultado = (
                validar_macro(normalizado, batch_uuid)
                if normalizado
                else DeadLetter(
                    source=source,
                    raw_payload=crudo,
                    rejection_reason=RejectionReason.TYPE_MISMATCH,
                    rejection_detail=f"fecha o dato no numérico: {crudo.get('dato')!r}",
                    batch_uuid=batch_uuid,
                )
            )
        else:
            continue

        if isinstance(resultado, DeadLetter):
            rechazos.append(resultado)
            motivos[resultado.rejection_reason.value] += 1
        else:
            validos.append(resultado)

    carga = db.Carga()
    if validos:
        if source in FUENTES_NOTICIAS:
            carga += db.cargar_noticias(cur, validos)
        elif source == "yahoo_finance":
            carga += db.cargar_precios(cur, validos)
        elif source == "yahoo_fundamentals":
            carga += db.cargar_fundamentales(cur, validos)
        elif source == "yahoo_fundamentals_anual":
            carga += db.cargar_fundamentales_anual(cur, validos)
        elif source in ("banxico", "inegi"):
            carga += db.cargar_macro(cur, validos)

    db.cargar_dead_letters(cur, rechazos)
    return carga, len(rechazos), motivos, omitidas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="src.pipeline.validate",
        description="Valida Bronze contra los contratos y carga Silver de forma idempotente.",
    )
    parser.add_argument("--date", dest="fecha", default=None,
                        help="Solo los lotes de esta fecha (YYYY-MM-DD).")
    parser.add_argument("--source", dest="fuentes", action="append",
                        help="Solo estas fuentes; repetible.")
    parser.add_argument("--todo", action="store_true",
                        help="Revalida también los lotes ya procesados "
                             "(reconstrucción de Silver tras un cambio de contrato).")
    args = parser.parse_args(argv)

    fecha = date.fromisoformat(args.fecha) if args.fecha else None
    raiz = Path(get_settings().bronze_path)

    lotes = listar_lotes(raiz, fecha=fecha)
    if args.fuentes:
        lotes = [l for l in lotes if leer_metadata(l)["source"] in set(args.fuentes)]
    if not lotes:
        print("[validate] no hay lotes que procesar")
        return 1

    print(f"[validate] {len(lotes)} lotes · inicio {datetime.now(UTC).isoformat(timespec='seconds')}")

    resumen: dict[str, db.Carga] = {}
    rechazos_por_fuente: Counter = Counter()
    motivos_totales: Counter = Counter()
    omitidas_total = 0

    with db.conectar() as conexion:
        with conexion.cursor() as cur:
            # El diccionario Finnovista va primero: las noticias lo necesitan
            # para reconocer fintechs como entidades identificables.
            metadatos = {ruta: leer_metadata(ruta) for ruta in lotes}

            # El diccionario Finnovista queda FUERA del salto: no se carga por
            # sus filas sino por los nombres que devuelve, que las noticias
            # necesitan para reconocer fintechs como entidades. Saltarlo dejaría
            # `fintechs` vacío y las noticias nuevas dejarían de etiquetarlas.
            lotes_fintech = [r for r in lotes if metadatos[r]["source"] == "finnovista"]
            nuevas_fintech, fintechs = _cargar_fintechs(cur, lotes_fintech)
            if lotes_fintech:
                print(f"[validate] finnovista: {len(fintechs)} entradas ({nuevas_fintech} nuevas)")

            ya_procesados = set() if args.todo else db.lotes_procesados(cur)
            saltados = 0

            for ruta in lotes:
                meta = metadatos[ruta]
                source = meta["source"]
                if source == "finnovista":
                    continue

                batch_uuid = UUID(meta["batch_uuid"])
                if batch_uuid in ya_procesados:
                    saltados += 1
                    continue

                carga, rechazos, motivos, omitidas = procesar_lote(cur, ruta, fintechs=fintechs)
                db.marcar_lote_procesado(
                    cur, batch_uuid, source=source, ruta=str(ruta),
                    carga=carga, rechazos=rechazos,
                )
                omitidas_total += omitidas
                resumen.setdefault(source, db.Carga())
                resumen[source] += carga
                rechazos_por_fuente[source] += rechazos
                motivos_totales.update(motivos)
                print(
                    f"[validate] {source}: {carga.nuevas} nuevas, "
                    f"{carga.actualizadas} actualizadas, {rechazos} a cuarentena",
                    flush=True,
                )
        conexion.commit()

    if saltados:
        print(f"[validate] {saltados} lotes ya procesados, saltados "
              f"(usa --todo para revalidarlos)")

    # --- Resumen ------------------------------------------------------------
    print()
    print(f"{'FUENTE':<16} {'NUEVAS':>8} {'ACTUALIZ':>9} {'CUARENTENA':>11}")
    print("-" * 48)
    for source in sorted(resumen):
        c = resumen[source]
        print(f"{source:<16} {c.nuevas:>8} {c.actualizadas:>9} {rechazos_por_fuente[source]:>11}")
    print("-" * 48)
    total_nuevas = sum(c.nuevas for c in resumen.values())
    print(f"{'TOTAL':<16} {total_nuevas:>8} "
          f"{sum(c.actualizadas for c in resumen.values()):>9} "
          f"{sum(rechazos_por_fuente.values()):>11}")

    if omitidas_total:
        print()
        print(f"omitidas por serie retirada de la configuración: {omitidas_total}")
        print("  (dato de Bronze fuera de alcance, no un rechazo de calidad)")

    if motivos_totales:
        print()
        print("motivos de rechazo:")
        for motivo, n in motivos_totales.most_common():
            print(f"  {motivo:<18} {n}")

    print()
    if args.todo:
        print(f"[validate] filas_nuevas = {total_nuevas}  "
              f"(reprocesar el mismo lote debe dar 0 — criterio PRD §8)")
    else:
        # Sin --todo este número NO demuestra idempotencia: los lotes ya
        # cargados ni siquiera se tocaron, así que un 0 aquí puede significar
        # "el UPSERT no duplicó" o "no se procesó nada". El criterio del §8 se
        # comprueba con --todo, que es lo que invoca `verify`.
        print(f"[validate] filas_nuevas = {total_nuevas} sobre lotes nuevos  "
              f"(la idempotencia del §8 se comprueba con --todo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
