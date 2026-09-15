"""Avisos a Telegram cuando algo requiere tu atención.

    docker compose exec -T app python -m src.pipeline.avisos --probar

**La regla es el silencio.** Solo se avisa de lo accionable: una franja del
scheduler que falla, `calidad` saliendo de OK, el gasto del brief acercándose a
su tope. Nunca "todo correcto". Un sistema que manda tres confirmaciones
diarias deja de leerse en una semana, y entonces el aviso que importa pasa
desapercibido — que es exactamente lo que este módulo existe para evitar. Es la
misma lección que costó calibrar dos veces los checks de `calidad.py`.

**Nunca rompe la corrida.** Si el token falta, si Telegram no responde o si la
red se cae, se anota y se sigue. Un pipeline que revienta porque no pudo avisar
de un fallo habría convertido la vigilancia en una causa de caídas.

El token se monta como secreto de Docker desde fuera del árbol del repositorio,
igual que la clave de Anthropic (ver `compose.yaml`). El `chat_id` va en el
`.env` porque no es un secreto: identifica una conversación, no autoriza nada.
"""

from __future__ import annotations

import argparse
import sys

from src.config import get_settings

API = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT = 15
LIMITE_TELEGRAM = 4096   # límite duro de la API; se recorta antes de enviar


def configurado() -> bool:
    """¿Hay token y destinatario? Sin ambos, los avisos están apagados."""
    s = get_settings()
    return bool(s.token_telegram and s.telegram_chat_id)


def enviar(titulo: str, cuerpo: str = "", *, urgente: bool = False) -> bool:
    """Manda un aviso. Devuelve si se entregó, sin lanzar nunca.

    El formato es deliberadamente sobrio: una línea de asunto que se lea entera
    en la notificación del móvil, y el detalle debajo para quien abra el
    mensaje. Lo importante tiene que caber en la previsualización.
    """
    if not configurado():
        return False

    s = get_settings()
    marca = "🔴" if urgente else "🟡"
    texto = f"{marca} *{titulo}*"
    if cuerpo:
        texto += f"\n```\n{cuerpo.strip()[:LIMITE_TELEGRAM - len(texto) - 20]}\n```"

    try:
        import requests

        r = requests.post(
            API.format(token=s.token_telegram),
            json={
                "chat_id": s.telegram_chat_id,
                "text": texto,
                "parse_mode": "Markdown",
                # Los avisos no llevan enlaces que valga la pena previsualizar,
                # y la vista previa ocupa media pantalla del móvil.
                "disable_web_page_preview": True,
            },
            timeout=TIMEOUT,
        )
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="src.pipeline.avisos",
        description="Avisos a Telegram. Sin argumentos, informa del estado.",
    )
    parser.add_argument("--probar", action="store_true",
                        help="Envía un mensaje de prueba para verificar la configuración.")
    parser.add_argument("--titulo", default="Prueba de avisos")
    parser.add_argument("--cuerpo", default="Si lees esto, el canal funciona.")
    parser.add_argument("--urgente", action="store_true")
    args = parser.parse_args(argv)

    s = get_settings()
    if not configurado():
        print("[avisos] sin configurar:", file=sys.stderr)
        print(f"  token: {'presente' if s.token_telegram else 'FALTA'}", file=sys.stderr)
        print(f"  chat_id: {s.telegram_chat_id or 'FALTA'}", file=sys.stderr)
        return 1

    print(f"[avisos] configurado · chat {s.telegram_chat_id}")
    if not args.probar:
        return 0

    if enviar(args.titulo, args.cuerpo, urgente=args.urgente):
        print("[avisos] enviado")
        return 0
    print("[avisos] no se pudo entregar", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
