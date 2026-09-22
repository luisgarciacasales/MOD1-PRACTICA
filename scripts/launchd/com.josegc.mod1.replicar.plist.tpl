<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!--
  Plantilla del LaunchAgent de la réplica. `__REPO__` y `__HOME__` los sustituye
  scripts/replicar_instalar.sh al instalar: la ruta del repositorio en el Mac no
  se puede fijar aquí porque este fichero SÍ se versiona y la del clon no tiene
  por qué coincidir.

  Es un LaunchAgent (sesión del usuario), no un LaunchDaemon (arranque del
  sistema), y esa elección es forzosa: la clave SSH tiene passphrase y se
  resuelve contra el agente y el llavero del usuario. Un demonio corriendo sin
  sesión no tendría acceso a ninguno de los dos y la réplica fallaría siempre.
  El precio es que no corre con la sesión cerrada, que es aceptable: el portátil
  sin sesión tampoco está replicando nada hoy.
-->
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.josegc.mod1.replicar</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>__REPO__/scripts/replicar_auto.sh</string>
    </array>

    <!-- Dos pasadas al día. La de 20:30 va después del respaldo del servidor
         (20:02) y antes de su ventana de apagado; la de 09:00 recoge lo que
         aquella no pudo si el Mac estaba dormido o fuera del tailnet.
         launchd ejecuta una cita vencida al despertar, así que dormir no la
         pierde — a diferencia de cron, que la descarta. -->
    <key>StartCalendarInterval</key>
    <array>
        <dict>
            <key>Hour</key><integer>20</integer>
            <key>Minute</key><integer>30</integer>
        </dict>
        <dict>
            <key>Hour</key><integer>9</integer>
            <key>Minute</key><integer>0</integer>
        </dict>
    </array>

    <!-- Al iniciar sesión, ponerse al día. El envoltorio es idempotente y
         cuesta ~1 s cuando no hay nada nuevo, así que sobra margen. -->
    <key>RunAtLoad</key>
    <true/>

    <!-- El script lleva su propio registro; esto captura lo que falle ANTES de
         que el script arranque (un bash inexistente, un permiso), que es
         justamente lo que de otro modo se perdería sin dejar rastro. -->
    <key>StandardOutPath</key>
    <string>__HOME__/augmented/logs/replicar.launchd.log</string>
    <key>StandardErrorPath</key>
    <string>__HOME__/augmented/logs/replicar.launchd.log</string>

    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
