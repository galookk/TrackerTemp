"""
temp-tracker: registra las temperaturas maximas estimadas por WunderGround
(via el endpoint interno que usa weather.com) e Yr.no, para varias ciudades,
en cada corrida programada (GitHub Actions).

IMPORTANTE: la parte de WunderGround usa un endpoint NO oficial y no
documentado. Puede dejar de funcionar si wunderground.com cambia su sitio.
Si eso pasa, el script sigue guardando los datos de Yr.no normalmente y
avisa por Telegram cual fuente fallo, en vez de romperse silenciosamente.
"""

import json
import re
import os
import csv
import datetime
from zoneinfo import ZoneInfo

import requests

CONFIG_FILE = "config.json"
DATA_FILE = "data/temperaturas.csv"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Yr.no / MET Norway EXIGE un User-Agent identificable con forma de contacto.
# Reemplazar el email por uno real antes de usar en produccion.
YR_HEADERS = {
    "User-Agent": "temp-tracker-galo/1.0 github.com/TU_USUARIO/temp-tracker"
}

DIAS_ES = {0: "LUNES", 1: "MARTES", 2: "MIÉRCOLES", 3: "JUEVES", 4: "VIERNES", 5: "SÁBADO", 6: "DOMINGO"}

# Horas UTC (de las 12 corridas diarias) en las que SI se manda Telegram.
# Corresponden a 21hs / 4hs / 10hs / 17hs hora Argentina.
CHECKPOINT_HORAS_UTC = {0, 7, 13, 20}


def nombre_dia(fecha_iso):
    fecha = datetime.date.fromisoformat(fecha_iso)
    return DIAS_ES[fecha.weekday()]


def formatear_bloque_fuente(emoji, nombre_fuente, forecast):
    """forecast: lista de (fecha, temp_max, temp_min). Muestra solo la maxima."""
    lineas = [f"{emoji} {nombre_fuente}"]
    for fecha, tmax, _tmin in forecast:
        lineas.append(f"- {nombre_dia(fecha)} {fecha}: max {tmax}°C")
    return "\n".join(lineas)


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_wu_apikey():
    """Extrae el apiKey publico embebido en el HTML de wunderground.com.

    Este apiKey no es secreto: es el mismo que el navegador de cualquier
    visitante usa para pedirle el pronostico a api.weather.com. Si esto
    deja de funcionar, es la primera pieza a revisar.
    """
    resp = requests.get("https://www.wunderground.com/", headers=BROWSER_HEADERS, timeout=20)

    # Intento 1: el formato clasico "apiKey":"xxxxx"
    match = re.search(r'"apiKey"\s*:\s*"([a-f0-9]{20,40})"', resp.text)
    if match:
        return match.group(1)

    # Intento 2: cualquier variante razonable (comillas simples, sin espacio, etc.)
    match = re.search(r'apiKey["\']?\s*[:=]\s*["\']([a-f0-9]{20,40})["\']', resp.text, re.IGNORECASE)
    if match:
        return match.group(1)

    # Intento 3: apiKey como parametro dentro de una URL, ej ...?apiKey=xxxxx&...
    match = re.search(r'[?&]apiKey=([a-f0-9]{20,40})', resp.text, re.IGNORECASE)
    if match:
        return match.group(1)

    # Si no aparece de ninguna forma, junto contexto alrededor de "apikey"
    # (si existe en cualquier capitalizacion) para diagnosticar por Telegram.
    lower = resp.text.lower()
    idx = lower.find("apikey")
    if idx != -1:
        contexto = resp.text[max(0, idx - 60): idx + 150].replace("\n", " ")
        pista = f'Se encontro la palabra "apikey" pero con un formato distinto al esperado. Contexto: {contexto}'
    else:
        pista = 'La palabra "apikey" no aparece en absoluto en el HTML descargado (puede cargarse via JavaScript despues).'

    raise RuntimeError(
        f"No se pudo extraer el apiKey (status HTTP={resp.status_code}, largo respuesta={len(resp.text)} caracteres). {pista}"
    )


def get_wu_forecast(lat, lon, api_key, days):
    """Devuelve lista de (fecha_local, temp_max_c, temp_min_c) segun WunderGround/weather.com."""
    url = "https://api.weather.com/v3/wx/forecast/daily/5day"
    params = {
        "apiKey": api_key,
        "geocode": f"{lat},{lon}",
        "format": "json",
        "units": "m",
        "language": "en-US",
    }
    resp = requests.get(url, params=params, headers=BROWSER_HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    max_temps = data.get("calendarDayTemperatureMax")
    min_temps = data.get("calendarDayTemperatureMin")
    valid_dates = data.get("validTimeLocal")
    if not max_temps or not valid_dates:
        raise RuntimeError(f"Respuesta inesperada de weather.com: {str(data)[:300]}")

    min_temps = min_temps or [None] * len(max_temps)

    resultado = []
    for fecha_iso, tmax, tmin in list(zip(valid_dates, max_temps, min_temps))[:days]:
        fecha = fecha_iso[:10]  # "2026-08-12T07:00:00-0300" -> "2026-08-12"
        resultado.append((fecha, tmax, tmin))
    return resultado


def get_yr_forecast(lat, lon, tz_name, days):
    """Devuelve lista de (fecha_local, temp_max_c, temp_min_c) segun Yr.no,
    agrupando los datos horarios por dia CALENDARIO LOCAL de la ciudad (no UTC)."""
    url = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
    params = {"lat": lat, "lon": lon}
    resp = requests.get(url, params=params, headers=YR_HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    tz = ZoneInfo(tz_name)
    temps_por_dia = {}
    for entry in data["properties"]["timeseries"]:
        ts_utc = datetime.datetime.fromisoformat(entry["time"].replace("Z", "+00:00"))
        ts_local = ts_utc.astimezone(tz)
        fecha_local = ts_local.strftime("%Y-%m-%d")
        temp = entry["data"]["instant"]["details"].get("air_temperature")
        if temp is not None:
            temps_por_dia.setdefault(fecha_local, []).append(temp)

    fechas_ordenadas = sorted(temps_por_dia.keys())[:days]
    return [(fecha, max(temps_por_dia[fecha]), min(temps_por_dia[fecha])) for fecha in fechas_ordenadas]


def append_rows(rows):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    file_exists = os.path.isfile(DATA_FILE)
    with open(DATA_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(
                ["timestamp_utc", "hora_consulta_utc", "ciudad", "fuente", "fecha_objetivo", "temp_max_c", "temp_min_c"]
            )
        writer.writerows(rows)


def send_to_sheets(rows):
    """Manda las filas a la Google Sheet via el Web App de Apps Script (ver apps_script.gs)."""
    webhook_url = os.environ.get("GOOGLE_SHEETS_WEBHOOK_URL")
    if not webhook_url:
        print("Google Sheets no configurado (falta GOOGLE_SHEETS_WEBHOOK_URL), salteando")
        return
    columnas = ["timestamp_utc", "hora_consulta_utc", "ciudad", "fuente", "fecha_objetivo", "temp_max_c", "temp_min_c"]
    payload = {"rows": [dict(zip(columnas, row)) for row in rows]}
    try:
        resp = requests.post(webhook_url, json=payload, timeout=30)
        print(f"Google Sheets respondio status={resp.status_code}")
    except Exception as e:
        print(f"No se pudo mandar los datos a Google Sheets: {e}")


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram no configurado (faltan TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID), salteando notificacion")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(
            url,
            data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=20,
        )
    except Exception as e:
        print(f"No se pudo enviar el mensaje de Telegram: {e}")


def main():
    config = load_config()
    days = config.get("dias_a_registrar", 3)
    now = datetime.datetime.now(datetime.timezone.utc)
    hora_consulta = now.strftime("%H:00")
    es_checkpoint = now.hour in CHECKPOINT_HORAS_UTC

    rows = []
    bloques_mensaje = [f"Registro de pronosticos - {now.strftime('%Y-%m-%d %H:%M UTC')}"]

    try:
        wu_key = get_wu_apikey()
    except Exception as e:
        wu_key = None
        bloques_mensaje.append(f"⚠️ WunderGround no disponible esta corrida: {e}")

    for city in config["cities"]:
        name = city["name"]
        flag = city.get("flag", "")
        lat, lon, tz_name = city["lat"], city["lon"], city["tz"]

        partes_ciudad = [f"{flag} <b>{name}</b>".strip()]

        if wu_key:
            try:
                wu_forecast = get_wu_forecast(lat, lon, wu_key, days)
                for fecha, tmax, tmin in wu_forecast:
                    rows.append([now.isoformat(), hora_consulta, name, "wunderground", fecha, tmax, tmin])
                partes_ciudad.append(formatear_bloque_fuente("2️⃣", "WU", wu_forecast))
            except Exception as e:
                partes_ciudad.append(f"⚠️ WU error: {e}")

        try:
            yr_forecast = get_yr_forecast(lat, lon, tz_name, days)
            for fecha, tmax, tmin in yr_forecast:
                rows.append([now.isoformat(), hora_consulta, name, "yr.no", fecha, tmax, tmin])
            partes_ciudad.append(formatear_bloque_fuente("3️⃣", "YR", yr_forecast))
        except Exception as e:
            partes_ciudad.append(f"⚠️ Yr error: {e}")

        bloques_mensaje.append("\n".join(partes_ciudad))

    if rows:
        append_rows(rows)
        send_to_sheets(rows)

    mensaje_final = "\n\n".join(bloques_mensaje)
    if es_checkpoint:
        send_telegram(mensaje_final)
    else:
        print("(corrida silenciosa, no es horario de aviso -> no se manda Telegram)")
    print(mensaje_final)


if __name__ == "__main__":
    main()
