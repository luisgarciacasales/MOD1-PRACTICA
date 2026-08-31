"""Backfill de fundamentales trimestrales desde los PDF de resultados.

Yahoo tope en ~4 años de serie anual y 7 trimestres, lo que dejaba la valuación
de cada emisora en unos 886 días frente a 2.513 de precio. Los reportes
trimestrales en PDF llegan mucho más atrás y son la **fuente primaria** — el
documento que la propia emisora publica.

    docker compose exec -T app python -m src.pipeline.backfill_fundamentales \\
        --dir /app/data/manual_dropzone/banorte_historico --ticker GFNORTEO.MX
    ... --dry-run     # extrae y muestra sin escribir

**Este módulo es un `ingest`, no una carga.** Escribe un lote en Bronze y ahí
termina; a Silver se llega por `validate`, como cualquier otra fuente. Antes
escribía directo a Silver, y eso dejaba los reportes fuera de la cadena que hace
reproducible el pipeline: al reconstruir la base, `validate` regenera Silver
desde Bronze y los trimestres del PDF no habrían vuelto — había que acordarse de
reejecutar esto a mano, justo el día peor. Ahora vuelven solos.

El lote queda en `bronze/fundamentals/reportes_pdf/{fecha}/{uuid}/` y lo recoge
el siguiente `validate` (o el siguiente `make batch`, que lo incluye).

Dos convenciones de nombre, según convenga:

    1T18.pdf             una carpeta por emisora, el ticker va en --ticker
    1T25_BBAJIOO.pdf     varias emisoras juntas; el sufijo manda sobre --ticker

Piloto sobre Banorte (28-ago-2026): 29 PDF ya descargados a mano cubriendo
2018-Q1 a 2025-Q1. Escalar al resto de emisoras exigiría ~460 PDF con el mismo
procedimiento manual, así que esto es una prueba de concepto sobre el caso de
estudio del proyecto, no un mecanismo general.

**Por qué el PDF gana a Yahoo cuando ambos existen.** Validación cruzada entre
los propios PDF: cada reporte incluye el mismo trimestre del año anterior, y en
24 de 24 pares comparables el valor coincide **exactamente** con el que declara
el PDF de ese trimestre. Frente a eso, Yahoo devuelve la UPA del primer
trimestre **redondeada a entero** en cuatro emisoras financieras (GFNORTEO 5.0
donde el reporte dice 5.435, y lo mismo en BBAJIOO, GENTERA y GFINBURO en 2025
y 2026). Así que en el solapamiento el PDF sobrescribe, y no al revés.

**Formato de las cifras.** Cada fila del reporte trae tres valores —mismo
trimestre del año anterior, trimestre anterior, y actual— seguidos de dos
variaciones porcentuales. El que interesa es **el tercero**. Se comprobó contra
las variaciones que el propio PDF publica: en 1T25, 5.435/4.878−1 = 11% y
5.435/4.927−1 = 10%, que son exactamente los dos porcentajes impresos.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from datetime import date
from pathlib import Path

from src.config import get_settings
from src.config.tiempo import hoy_mercado
from src.pipeline.bronze import escribir_lote

# El nombre con el que `validate` reconoce estos lotes y les asigna la
# precedencia `reporte_pdf` sobre los datos del agregador.
FUENTE = "reportes_pdf"

# 1T→31 de marzo, y así. El nombre del archivo es la única fuente de la fecha:
# el texto del PDF trae el corte del periodo en varias formas y ninguna es
# fiable de parsear (mismo problema que motivó `fecha_aproximada_de_trimestre`
# en reportes_ir.py).
FIN_DE_TRIMESTRE = {"1": (3, 31), "2": (6, 30), "3": (9, 30), "4": (12, 31)}

# Las cifras de balance vienen en MILLONES de pesos en el reporte; el contrato
# las guarda en pesos, igual que las de Yahoo.
MILLONES = 1_000_000

CAMPOS = {
    # nombre del contrato → (patrón de la etiqueta, multiplicador)
    "utilidad_por_accion": (r"Utilidad por Acci[oó]n\s*\(Pesos\)(?:\s*\(\d\))?", 1),
    "capital_contable": (r"Capital Contable", MILLONES),
    "utilidad_neta": (r"Utilidad Neta\s*(?:\(1\))?\s*(?=[\d,])", MILLONES),
}


# Tolerancia entre el ROE que publica el reporte y el que implican los campos
# extraídos. El desvío normal es de 0,3-1,0 pp porque la emisora promedia el
# capital y excluye el interés minoritario, mientras que aquí se usa el capital
# de cierre; 1,5 pp deja holgura para eso sin tapar un error de extracción, que
# es de otro orden de magnitud (el 1T26 fallaba por 5,9 pp).
TOLERANCIA_ROE_PP = 1.5


def _roe_publicado(texto: str) -> float | None:
    """El ROE del trimestre según la tabla de Rentabilidad del propio reporte.

    Tercer valor de la fila, como en el resto del documento: las columnas son
    [mismo trimestre del año anterior, trimestre anterior, ACTUAL, acumulado
    anterior, acumulado actual, doce meses].
    """
    m = re.search(r"\nROE[^\n]{0,90}", texto)
    if not m:
        return None
    numeros = re.findall(r"(\d+\.\d)%", m.group(0))
    return float(numeros[2]) if len(numeros) >= 3 else None


def _texto(pdf: bytes) -> str:
    from pypdf import PdfReader

    return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf)).pages)


def _valor_actual(texto: str, patron: str) -> float | None:
    """Tercer número de la fila: el del trimestre que reporta el documento."""
    m = re.search(patron + r"[^\n\d]{0,40}((?:[\d,]+\.?\d*\s+){2,4})", texto, re.I)
    if not m:
        return None
    numeros = [float(x.replace(",", "")) for x in m.group(1).split()]
    return numeros[2] if len(numeros) >= 3 else None


def _periodo_y_emisora(nombre: str) -> tuple[date | None, str | None]:
    """Interpreta el nombre del archivo. Dos convenciones admitidas:

        1T18.pdf              → (2018-03-31, None)   una carpeta por emisora
        1T25_BBAJIOO.pdf      → (2025-03-31, "BBAJIOO")  varias en la misma

    La segunda permite mezclar emisoras en un directorio, y entonces el sufijo
    manda sobre el `--ticker` de la línea de comandos: con archivos de varias
    emisoras juntos, un ticker único sería justo lo que no se quiere.
    """
    m = re.fullmatch(r"([1-4])T(\d{2})(?:[_-](.+))?", nombre, re.I)
    if not m:
        return None, None
    trimestre, anio, emisora = m.groups()
    mes, dia = FIN_DE_TRIMESTRE[trimestre]
    return date(2000 + int(anio), mes, dia), (emisora.strip() if emisora else None)


def _resolver_ticker(sufijo: str | None, por_defecto: str | None) -> str | None:
    """Sufijo del archivo → ticker del universo.

    Se resuelve contra `ALIAS_EMISORAS` en lugar de exigir el símbolo exacto,
    porque al renombrar a mano nadie escribe la clave de pizarra literal: los
    archivos llegaron como `BBAJIO` (el ticker es BBAJIOO.MX, con doble O) y
    `GFINBURSA` (es GFINBURO.MX). Ese diccionario existe justamente para saber
    con qué nombres se llama a cada emisora, así que reusarlo evita mantener
    una segunda tabla que se desincronizaría.

    Tres intentos, del más estricto al más laxo:
      1. el símbolo tal cual (`BBAJIOO.MX`, o `BBAJIOO` al que se añade `.MX`),
      2. coincidencia exacta con un alias (`gentera`, `inbursa`),
      3. un alias que sea PREFIJO del sufijo (`gfinbur` ⊂ `gfinbursa`).

    El tercero es el que resuelve las abreviaturas parciales. No se hace al
    revés —el sufijo como prefijo del alias— porque `q` casaría con `qualitas`
    y cualquier letra suelta traería una emisora al azar.
    """
    if not sufijo:
        return por_defecto

    from src.config.emisoras import ALIAS_EMISORAS
    from src.config.tickers import TICKERS_PRIORITARIOS

    limpio = sufijo.strip().upper()
    for candidato in (limpio, limpio if limpio.endswith(".MX") else f"{limpio}.MX"):
        if candidato in TICKERS_PRIORITARIOS:
            return candidato

    normalizado = limpio.removesuffix(".MX").lower()
    for ticker, alias in ALIAS_EMISORAS.items():
        if normalizado in alias:
            return ticker
    for ticker, alias in ALIAS_EMISORAS.items():
        if any(normalizado.startswith(a) for a in alias if len(a) >= 4):
            return ticker
    return None


def extraer(directorio: Path, ticker: str) -> tuple[list[dict], list[str]]:
    """Devuelve (registros crudos, incidencias). No escribe nada ni valida:
    Bronze guarda lo que dijo la fuente, y juzgarlo es tarea del contrato."""
    filas, incidencias = [], []

    from src.config.tickers import TICKERS_PRIORITARIOS

    for archivo in sorted(directorio.glob("*.pdf")):
        periodo, sufijo = _periodo_y_emisora(archivo.stem)
        if periodo is None:
            incidencias.append(
                f"{archivo.name}: nombre fuera de los patrones {{n}}T{{aa}} "
                f"o {{n}}T{{aa}}_{{EMISORA}}"
            )
            continue

        del_archivo = _resolver_ticker(sufijo, ticker)
        if del_archivo is None or del_archivo not in TICKERS_PRIORITARIOS:
            incidencias.append(
                f"{archivo.name}: no se pudo resolver '{sufijo or ticker}' a una "
                "emisora del universo — se omite en vez de cargarlo bajo un "
                "ticker que no le toca"
            )
            continue

        texto = _texto(archivo.read_bytes())
        crudo = {"ticker": del_archivo,
                 "period_end": periodo.isoformat(),
                 "source": FUENTE}
        for campo, (patron, factor) in CAMPOS.items():
            valor = _valor_actual(texto, patron)
            if valor is not None:
                crudo[campo] = valor * factor

        # Ya no se exige UPA. Se exigía cuando el único destino era el P/U, y
        # costaba caro: 3T24 tiene capital contable y utilidad neta perfectamente
        # extraíbles y se tiraba entero por no encontrar la UPA (el texto del PDF
        # sale con espacios espurios). Desde que existe el ROE —que no necesita
        # UPA— esa fila vale. Quién decide qué es aceptable es el contrato, en
        # `validate`, no este extractor: aquí solo se lee el papel.
        if len(crudo) == 3:
            incidencias.append(f"{archivo.name}: ningún campo extraíble")
            continue

        # El reporte valida su propia extracción. `Utilidad Neta` y `Capital
        # Contable` aparecen decenas de veces en el documento —Grupo, Banco,
        # subsidiarias— y tomar la primera coincidencia acierta casi siempre,
        # pero no siempre: en 1T26 la primera es de otra entidad (11,912 mdp
        # cuando la UPA de 5.495 implica unos 15,200) y el ROE salía 18,0 por
        # ciento contra el 23,9 que el propio PDF imprime.
        #
        # Se descartan los dos campos en vez de la fila entera porque la UPA se
        # extrae de una etiqueta que casi no se repite y es fiable de forma
        # independiente: quedarse sin ROE ese trimestre cuesta menos que
        # quedarse además sin P/U, y mucho menos que publicar un ROE falso.
        publicado = _roe_publicado(texto)
        implicado = (
            100.0 * crudo["utilidad_neta"] * 4 / crudo["capital_contable"]
            if crudo.get("utilidad_neta") and crudo.get("capital_contable")
            else None
        )
        if publicado is not None and implicado is not None:
            desvio = implicado - publicado
            if abs(desvio) > TOLERANCIA_ROE_PP:
                incidencias.append(
                    f"{archivo.name}: utilidad neta y capital descartados — implican "
                    f"un ROE de {implicado:.1f} y el reporte publica {publicado:.1f} "
                    f"({desvio:+.1f} pp); la etiqueta debió casar con otra entidad"
                )
                crudo.pop("utilidad_neta", None)
                crudo.pop("capital_contable", None)
                if len(crudo) == 3:
                    continue

        filas.append(crudo)

    return filas, incidencias


def descargar_faltantes(destino: Path) -> tuple[list[str], list[str]]:
    """Baja al dropzone los reportes de Banorte que no estén ya ahí.

    Devuelve `(descargados, incidencias)`. El nombre se **normaliza** a
    `{n}T{aa}.pdf`: el sitio los publica con sufijos que cambian de un trimestre
    a otro (`2T25_vc_.pdf`, `4T25_v.pdf`, `2T26.pdf`) y `_periodo_y_emisora`
    leería ese sufijo como si fuera el nombre de una emisora.

    Solo Banorte, y solo hacia adelante: su página lista los cinco trimestres
    más recientes, no el histórico. Esto no reabre el pasado —el de 2018-2024 se
    reunió a mano en su día—, sirve para que la serie no vuelva a quedarse atrás,
    que era el problema recurrente.

    Nunca sobrescribe: un archivo ya presente se respeta, porque puede haberlo
    puesto una persona a mano y el sitio republica con nombres cambiantes.
    """
    import requests

    from src.sources.reportes_ir import _CABECERAS, TIMEOUT_PDF, urls_trimestrales_banorte

    destino.mkdir(parents=True, exist_ok=True)
    descargados, incidencias = [], []

    try:
        urls = urls_trimestrales_banorte()
    except Exception as exc:  # noqa: BLE001
        return [], [f"no se pudo consultar el listado de RI: {type(exc).__name__}: {exc}"]

    for (anio, qnum), url in sorted(urls.items()):
        nombre = f"{qnum}T{anio % 100:02d}.pdf"
        archivo = destino / nombre
        if archivo.exists():
            continue
        try:
            resp = requests.get(url, headers=_CABECERAS, timeout=TIMEOUT_PDF)
            resp.raise_for_status()
            if not resp.content.startswith(b"%PDF"):
                incidencias.append(f"{nombre}: la respuesta no es un PDF ({url})")
                continue
            archivo.write_bytes(resp.content)
            descargados.append(f"{nombre} ({len(resp.content) // 1024} KB)")
        except Exception as exc:  # noqa: BLE001
            incidencias.append(f"{nombre}: {type(exc).__name__} al descargar {url}")

    return descargados, incidencias


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="src.pipeline.backfill_fundamentales",
        description="Carga fundamentales trimestrales desde PDF de resultados.",
    )
    parser.add_argument("--dir", required=True, help="Directorio con los PDF ({n}T{aa}.pdf).")
    parser.add_argument("--ticker", default=None,
                        help="Emisora por defecto. Opcional si TODOS los archivos\n"
                             "llevan sufijo {n}T{aa}_{EMISORA}.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Extrae y muestra sin escribir en Silver.")
    parser.add_argument("--descargar", action="store_true",
                        help="Antes de extraer, baja de la página de RI de Banorte\n"
                             "los trimestres que falten en --dir. No sobrescribe.")
    args = parser.parse_args(argv)

    directorio = Path(args.dir)
    if args.descargar:
        nuevos, fallos = descargar_faltantes(directorio)
        for f in fallos:
            print(f"[backfill] descarga: {f}", file=sys.stderr)
        print(f"[backfill] descargados: {', '.join(nuevos) if nuevos else 'ninguno (ya estaban)'}")

    if not directorio.is_dir():
        print(f"[backfill] {directorio} no es un directorio", file=sys.stderr)
        return 1

    filas, incidencias = extraer(directorio, args.ticker)
    if not filas:
        print(f"[backfill] ningún PDF utilizable en {directorio}", file=sys.stderr)
        for i in incidencias:
            print(f"  {i}", file=sys.stderr)
        return 1

    emisoras = sorted({f["ticker"] for f in filas})
    periodos = sorted(f["period_end"] for f in filas)
    print(f"[backfill] {', '.join(emisoras)} · {len(filas)} trimestres extraídos "
          f"({periodos[0]} → {periodos[-1]})")
    for i in incidencias:
        print(f"[backfill] incidencia: {i}")

    if args.dry_run:
        print(f"\n{'EMISORA':<14}{'PERIODO':<13}{'UPA':>9}{'CAPITAL (mdp)':>16}")
        for f in sorted(filas, key=lambda x: (x["ticker"], x["period_end"])):
            cap = (f.get("capital_contable") or 0) / MILLONES
            upa = f.get("utilidad_por_accion")
            print(f"{f['ticker']:<14}{f['period_end']:<13}"
                  f"{upa if upa is None else round(upa, 3)!s:>9}{cap:>16,.0f}")
        print("\n[backfill] DRY RUN — no se escribió nada.")
        return 0

    lote = escribir_lote(
        filas,
        source=FUENTE,
        categoria="fundamentals",
        fecha=hoy_mercado(),
        raiz_bronze=Path(get_settings().bronze_path),
    )
    print(f"[backfill] lote {lote.batch_uuid} · {lote.record_count} registros")
    print(f"[backfill] Bronze: {lote.ruta}")
    print("[backfill] a Silver se llega por `validate`, que es la única puerta: "
          "córrelo ahora o deja que lo recoja el próximo `make batch`.")
    print("[backfill] después, `transform` para recalcular gold_valuation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
