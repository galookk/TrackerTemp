# temp-tracker — piloto (Buenos Aires, Tokyo, Munich)

## Qué hace
4 veces por día (21h / 4h / 10h / 17h hora Argentina) consulta la temperatura
máxima estimada por WunderGround (vía el endpoint interno que usa weather.com)
e Yr.no, para los próximos 5 días, en las 3 ciudades piloto. Guarda todo en
`data/temperaturas.csv` (queda versionado en el repo) y te manda un resumen
por Telegram.

## Puesta en marcha (una sola vez)

1. **Crear el repo en GitHub**
   - Subí esta carpeta a un repo nuevo (puede ser privado).

2. **Crear el bot de Telegram**
   - Hablale a `@BotFather` en Telegram → `/newbot` → seguí los pasos.
   - Te va a dar un `TELEGRAM_BOT_TOKEN` (algo como `123456:ABC-...`).
   - Mandale cualquier mensaje a tu bot recién creado (para "activar" el chat).
   - Andá a `https://api.telegram.org/bot<TU_TOKEN>/getUpdates` en el navegador
     y buscá `"chat":{"id": ...}` → ese número es tu `TELEGRAM_CHAT_ID`.

3. **Cargar los secrets en GitHub**
   - En el repo: Settings → Secrets and variables → Actions → New repository secret
   - Cargá `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` con los valores de arriba.

4. **Probarlo manualmente ANTES de confiar en el cron**
   - Pestaña "Actions" del repo → workflow "Registrar pronosticos" → "Run workflow".
   - Revisá los logs de la corrida y si te llegó el mensaje de Telegram.
   - Fijate especialmente si la parte de WunderGround dio error — es la parte
     más frágil de todo el sistema (ver abajo).

## Si WunderGround falla

El truco que usa el script (leer el `apiKey` público del HTML de
wunderground.com y pegarle directo a `api.weather.com`) es un endpoint NO
oficial. Puede fallar por dos motivos típicos:

- **El sitio cambió y ya no aparece `"apiKey":"..."` en el HTML** → hay que
  actualizar la regex en `get_wu_apikey()`.
- **El apiKey que devuelve no sirve para geocodes fuera de un país/región** →
  si esto pasa, avisame con el mensaje de error exacto y lo ajustamos (por
  ejemplo, buscando el apiKey en una página de wunderground.com específica de
  cada país en vez de la home).

Mientras tanto, el script sigue guardando los datos de Yr.no sin problema —
no se cae todo por un solo error de fuente.

## Ajustar horarios más adelante

El cron está en `.github/workflows/scrape.yml`. Los horarios están en UTC;
Argentina es siempre UTC-3 (no tiene horario de verano), así que restale 3
horas a la hora que quieras en ART para poner el cron.

## Después del piloto

Cuando confirmes que anda bien con estas 3 ciudades, escalar a las 8 del
portfolio es solo agregar entradas a `config.json` (nombre, lat, lon, tz) —
no hay que tocar el resto del código.
