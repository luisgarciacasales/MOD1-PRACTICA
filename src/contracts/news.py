"""Contrato `SilverNews` — frontera de calidad entre Bronze y Silver.

Doble nivel de validación (PRD §6.2, skill data-contracts):

1. **Tipado estricto** — tipos, longitudes (`title` ≤ 1024, `content` ≤ 8192),
   fecha ISO 8601, URL absoluta.
2. **Integridad semántica** — al menos un Ticker, Sector o Entidad. Sin ninguno
   y sin bypass macro ⇒ `MISSING_ENTITY`.

El módulo expone `validar_noticia()`, que devuelve **o** un `SilverNews` válido
**o** un `DeadLetter`: nunca lanza excepción hacia arriba ni descarta en
silencio. Quien lo llama solo tiene que decidir a qué tabla escribe.
"""

from __future__ import annotations

import hashlib
import unicodedata
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic.networks import HttpUrl

from src.config.macro_lexicon import LEXICO_MACRO, MIN_TERMINOS_MACRO
from src.config.sources import FUENTES_CON_BYPASS_MACRO
from src.contracts.rejections import DeadLetter, RejectionReason

# Solo las fuentes de texto: yahoo_finance y banxico tienen sus propios
# contratos, y finnovista es un diccionario, no una noticia.
SourceNoticias = Literal["bmv_eventos", "financiero", "economista", "bloomberg"]

_URL_ADAPTER = TypeAdapter(HttpUrl)


def normalizar(texto: str) -> str:
    """Minúsculas y sin acentos, para comparar léxico de forma estable."""
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def calcular_guid(source: str, url: str, published_at: datetime) -> str:
    """Clave natural: SHA-256 de source + url + published_at (PRD §6.3).

    Es la base de la idempotencia: el mismo artículo reingerido dos veces
    produce el mismo guid, y el `ON CONFLICT (guid)` lo resuelve sin duplicar.

    `published_at` se normaliza a UTC antes de entrar al hash — si no, el mismo
    instante expresado en dos zonas horarias generaría dos guids distintos y la
    idempotencia se rompería de forma silenciosa.
    """
    instante = published_at.astimezone(UTC).isoformat()
    material = f"{source}|{url}|{instante}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def es_macro(source: str, *textos: str) -> bool:
    """¿Califica la noticia para el bypass macroeconómico?

    Dos señales, según el PRD §6.2: la fuente (Bloomberg Línea publica notas de
    política monetaria sin ticker) o el léxico del propio texto.

    El umbral de MIN_TERMINOS_MACRO términos distintos evita que una mención
    incidental ("el peso mexicano se fortaleció") abra la puerta a cualquier
    registro huérfano.
    """
    if source in FUENTES_CON_BYPASS_MACRO:
        return True
    cuerpo = normalizar(" ".join(t for t in textos if t))
    encontrados = {termino for termino in LEXICO_MACRO if termino in cuerpo}
    return len(encontrados) >= MIN_TERMINOS_MACRO


class SilverNews(BaseModel):
    """Fila validada de `silver_news`."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
        validate_assignment=True,
    )

    guid: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    source: SourceNoticias
    title: str = Field(min_length=1, max_length=1024)
    content: str = Field(min_length=1, max_length=8192)
    url: str = Field(min_length=1, max_length=2048)
    published_at: datetime
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    tickers: list[str] | None = None
    sector: str | None = Field(default=None, max_length=64)
    entities: list[str] | None = None

    enriched: bool = False
    macro_bypass: bool = False
    raw_batch_uuid: UUID

    @field_validator("url")
    @classmethod
    def _url_absoluta(cls, v: str) -> str:
        # Validamos con HttpUrl pero persistimos str: la columna es TEXT y no
        # queremos que pydantic normalice la URL (añadir '/' final, por ejemplo)
        # y cambie el material del guid entre ejecuciones.
        _URL_ADAPTER.validate_python(v)
        return v

    @field_validator("published_at", "ingested_at")
    @classmethod
    def _a_utc(cls, v: datetime) -> datetime:
        # Una fecha naive se interpreta como UTC en vez de rechazarse: los RSS
        # mexicanos a menudo omiten el offset. Asumirlo explícitamente aquí es
        # mejor que dejar que cada etapa improvise su propia suposición.
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    @field_validator("tickers", "entities")
    @classmethod
    def _limpiar_lista(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        # dict.fromkeys en vez de set: deduplica preservando el orden, que es
        # lo que hace reproducible el contenido de la columna entre corridas.
        limpios = list(dict.fromkeys(x.strip().upper() for x in v if x and x.strip()))
        return limpios or None

    @model_validator(mode="after")
    def _integridad_semantica(self) -> SilverNews:
        """Segundo nivel: al menos un Ticker, Sector o Entidad.

        El bypass macroeconómico es la única excepción admitida.
        """
        if self.macro_bypass:
            return self
        if self.tickers or self.sector or self.entities:
            return self
        raise ValueError(RejectionReason.MISSING_ENTITY.value)


def validar_noticia(
    crudo: dict[str, Any],
    batch_uuid: UUID,
) -> SilverNews | DeadLetter:
    """Aplica el contrato a un registro de Bronze.

    Devuelve `SilverNews` si pasa, `DeadLetter` si no. No lanza: enrutar el
    rechazo es parte del contrato, no un caso excepcional.
    """
    source = str(crudo.get("source", "")).strip()

    def rechazo(motivo: RejectionReason, detalle: str | None = None) -> DeadLetter:
        return DeadLetter(
            guid=_guid_best_effort(crudo, source),
            source=source or "desconocida",
            raw_payload=crudo,
            rejection_reason=motivo,
            rejection_detail=detalle,
            batch_uuid=batch_uuid,
        )

    # --- Precondiciones que impiden siquiera calcular la clave natural --------
    if source not in {"bmv_eventos", "financiero", "economista", "bloomberg"}:
        return rechazo(RejectionReason.UNKNOWN_SOURCE, f"source={source!r}")

    url = str(crudo.get("url", "")).strip()
    if not url:
        return rechazo(RejectionReason.INVALID_URL, "url ausente o vacía")

    publicado = _parsear_fecha(crudo.get("published_at"))
    if publicado is None:
        return rechazo(
            RejectionReason.INVALID_DATE,
            f"published_at={crudo.get('published_at')!r}",
        )

    # --- Bypass macro: se decide ANTES de construir el modelo, porque es lo que
    # determina si la ausencia de entidades es aceptable o es un rechazo -------
    titulo = str(crudo.get("title", ""))
    contenido = str(crudo.get("content", ""))
    bypass = es_macro(source, titulo, contenido)

    try:
        return SilverNews(
            guid=calcular_guid(source, url, publicado),
            source=source,  # type: ignore[arg-type]
            title=titulo,
            content=contenido,
            url=url,
            published_at=publicado,
            tickers=crudo.get("tickers"),
            sector=crudo.get("sector"),
            entities=crudo.get("entities"),
            macro_bypass=bypass,
            raw_batch_uuid=batch_uuid,
        )
    except ValidationError as exc:
        return rechazo(_clasificar(exc), _resumir(exc))


# --- Auxiliares ------------------------------------------------------------


def _clasificar(exc: ValidationError) -> RejectionReason:
    """Traduce el error de Pydantic al motivo tipado que se guarda en la DLQ."""
    for error in exc.errors():
        mensaje = str(error.get("msg", ""))
        if RejectionReason.MISSING_ENTITY.value in mensaje:
            return RejectionReason.MISSING_ENTITY
        if error.get("type") == "missing":
            return RejectionReason.MISSING_FIELD
        if "url" in error.get("loc", ()):  # type: ignore[operator]
            return RejectionReason.INVALID_URL
    return RejectionReason.TYPE_MISMATCH


def _resumir(exc: ValidationError) -> str:
    partes = [
        f"{'.'.join(str(x) for x in e.get('loc', ()))}: {e.get('msg')}"
        for e in exc.errors()
    ]
    return "; ".join(partes)[:2048]


def _parsear_fecha(valor: Any) -> datetime | None:
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=UTC)
    if isinstance(valor, str) and valor.strip():
        try:
            # fromisoformat de 3.11+ acepta el sufijo 'Z'.
            fecha = datetime.fromisoformat(valor.strip())
        except ValueError:
            return None
        return fecha if fecha.tzinfo else fecha.replace(tzinfo=UTC)
    return None


def _guid_best_effort(crudo: dict[str, Any], source: str) -> str | None:
    """Intenta calcular el guid para poder rastrear el rechazo. None si no se puede."""
    url = str(crudo.get("url", "")).strip()
    publicado = _parsear_fecha(crudo.get("published_at"))
    if not source or not url or publicado is None:
        return None
    return calcular_guid(source, url, publicado)
