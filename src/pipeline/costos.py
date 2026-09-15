"""Latencia y coste del pipeline, medidos sobre las corridas reales.

    docker compose exec -T app python -m src.pipeline.costos

Responde la pregunta de viabilidad en producción: cuánto tarda cada etapa,
cuánto cuesta procesar una noticia, y qué pasaría si el corpus creciera. No
instrumenta nada nuevo — lee `historial.log`, que el batch viene escribiendo
desde agosto de 2026.

**Sobre el coste monetario.** Es cero para todo el procesamiento de texto: NER,
sentimiento, detección de M&A y embeddings corren en local contra la RTX 5080
(política FinOps de CLAUDE.md). La única llamada a un modelo de pago es el brief
ejecutivo semanal, con tope propio. Lo que sí tiene coste es el TIEMPO de GPU,
que es un recurso compartido con los demás servicios del laboratorio, y por eso
se mide aquí en lugar de darlo por gratis.
"""

from __future__ import annotations

import re
import statistics as st
from dataclasses import dataclass
from pathlib import Path

from src.config import get_settings

ETAPAS = ("ingest", "validate", "enrich", "transform", "correlate", "index")

# Consumo típico de la RTX 5080 bajo carga de inferencia, menos el reposo.
# Es una estimación de catálogo, no una medición: sirve para dar orden de
# magnitud del coste energético, no para facturar.
VATIOS_GPU_CARGA = 250
VATIOS_GPU_REPOSO = 20


@dataclass
class Corrida:
    fecha: str
    estado: str
    total: int
    etapas: dict[str, int]
    noticias: int | None


def _leer(ruta: Path) -> list[Corrida]:
    corridas: list[Corrida] = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if " total=" not in linea:
            continue
        fecha = linea.split()[0]
        estado = "OK" if " OK " in linea else "FALLO"
        total = int(re.search(r"total=(\d+)s", linea).group(1))
        etapas = {
            e: int(m.group(1))
            for e in ETAPAS
            if (m := re.search(rf"{e}=(\d+)s", linea))
        }
        noticias = int(m.group(1)) if (m := re.search(r"news=(\d+)", linea)) else None
        corridas.append(Corrida(fecha, estado, total, etapas, noticias))
    return corridas


def _noticias_nuevas(corridas: list[Corrida]) -> list[tuple[Corrida, int]]:
    """Cuántas noticias trajo cada corrida, por diferencia con la anterior.

    El historial guarda el acumulado, no el incremento. Los saltos negativos
    —que los hay: un reproceso o una carga histórica mueven el total— se
    descartan en lugar de contarse como cero, porque promediarlos hundiría el
    coste por noticia y daría una cifra optimista y falsa.
    """
    pares = []
    previo = None
    for c in corridas:
        if c.noticias is not None:
            if previo is not None and c.noticias > previo:
                pares.append((c, c.noticias - previo))
            previo = c.noticias
    return pares


def informe() -> str:
    ruta = Path(get_settings().bronze_path).parent / "logs" / "historial.log"
    if not ruta.exists():
        return f"[costos] no hay historial en {ruta}"

    corridas = _leer(ruta)
    ok = [c for c in corridas if c.estado == "OK"]
    if not ok:
        return "[costos] sin corridas completadas en el historial"

    L: list[str] = []
    L.append(f"Latencia y coste del pipeline — {len(ok)} corridas completadas")
    L.append(f"De {ok[0].fecha[:10]} a {ok[-1].fecha[:10]}")
    L.append("=" * 74)

    L.append("")
    L.append(f"{'ETAPA':<12}{'MEDIA':>9}{'MEDIANA':>10}{'MIN':>7}{'MAX':>7}{'% DEL TOTAL':>14}")
    L.append("-" * 74)
    medias_totales = st.mean(c.total for c in ok)
    for e in ETAPAS:
        vals = [c.etapas[e] for c in ok if e in c.etapas]
        if not vals:
            continue
        m = st.mean(vals)
        L.append(f"{e:<12}{m:>8.0f}s{st.median(vals):>9.0f}s"
                 f"{min(vals):>6}s{max(vals):>6}s{100 * m / medias_totales:>13.0f}%")
    L.append("-" * 74)
    L.append(f"{'TOTAL':<12}{medias_totales:>8.0f}s{st.median([c.total for c in ok]):>9.0f}s"
             f"{min(c.total for c in ok):>6}s{max(c.total for c in ok):>6}s")

    # --- Coste por noticia --------------------------------------------------
    pares = _noticias_nuevas(ok)
    L.append("")
    L.append("COSTE POR NOTICIA")
    L.append("-" * 74)
    if pares:
        seg_por_noticia = [
            c.etapas["enrich"] / n for c, n in pares if n > 0 and "enrich" in c.etapas
        ]
        nuevas = [n for _, n in pares]
        L.append(f"  noticias por corrida     mediana {st.median(nuevas):.0f}"
                 f"  ·  rango {min(nuevas)}-{max(nuevas)}")
        L.append(f"  enriquecimiento          mediana {st.median(seg_por_noticia):.2f} s/noticia")
        L.append(f"  coste monetario          0.00 USD  (inferencia local, política FinOps)")
        wh = st.median(seg_por_noticia) * (VATIOS_GPU_CARGA - VATIOS_GPU_REPOSO) / 3600
        L.append(f"  energía                  ~{wh:.3f} Wh/noticia"
                 f"  (estimado sobre {VATIOS_GPU_CARGA} W de carga)")
    else:
        L.append("  (sin incrementos medibles en el historial)")

    # --- Escalado -----------------------------------------------------------
    L.append("")
    L.append("SI EL CORPUS CRECIERA")
    L.append("-" * 74)
    if pares:
        s_n = st.median(seg_por_noticia)
        L.append("  Reprocesarlo entero, que es el peor caso —cambiar un prompt obliga a ello—:")
        for n in (2_500, 10_000, 50_000, 200_000):
            horas = n * s_n / 3600
            L.append(f"    {n:>7,} noticias  →  {horas:>6.1f} h de GPU")
        L.append("")
        L.append("  Las corridas diarias no escalan con el corpus sino con lo NUEVO,")
        L.append("  así que su duración se mantiene mientras el ritmo de publicación no cambie.")

    L.append("")
    L.append("LO QUE ESTAS CIFRAS NO DICEN")
    L.append("-" * 74)
    L.append("  · La GPU es compartida con los demás servicios del laboratorio: el tiempo")
    L.append("    de enrich no es solo coste propio, es indisponibilidad para los otros.")
    L.append("  · El consumo energético es una estimación de catálogo, no una medición.")
    L.append("  · `ingest` depende de fuentes externas: su latencia mide la red y los")
    L.append("    servidores ajenos tanto como el pipeline.")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    print(informe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
