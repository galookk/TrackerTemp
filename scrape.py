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
import time
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

WU_API_HEADERS = dict(BROWSER_HEADERS)
WU_API_HEADERS["Referer"] = "https://www.wunderground.com/"
WU_API_HEADERS["Origin"] = "https://www.wunderground.com"

YR_HEADERS = {
    "User-Agent": "temp-tracker-galo/1.0 github.com/TU_USUARIO/temp-tracker"
}

DIAS_ES = {0: "LUNES", 1: "MARTES", 2: "MIÉRCOLES", 3: "JUEVES", 4: "VIERNES", 5: "SÁBADO", 6: "DOMINGO"}

CHECKPOINT_HORAS_UTC = {4, 11, 18, 23}

# Cuantas veces se intenta conseguir una clave VALIDA de WunderGround, y
# cuanto se espera entre intento e intento. Por experiencia, una clave
# rota suele arreglarse sola despues de unos minutos (WU emite una nueva) --
# por eso la espera es larga (minutos, no segundos).
WU_KEY_INTENTOS = 3
WU_KEY_ESPERA_SEG = 300  # 5 minutos

# Reintentos cortos, por ciudad, una vez que ya tenemos una clave validada
# (para fallas de red puntuales, no para claves rotas).
WU_FORECAST_INTENTOS = 2
WU_FORECAST_ESPERA_SEG = 5
YR_INTENTOS = 2
YR_ESPERA_SEG = 5


def con_reintentos(func, intentos, espera_seg, *args, **kwargs):
    """Ejecuta func(*args, **kwargs), reintentando en caso de excepcion."""
    ultimo_error = None
    for intento in range(1, intentos + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            ultimo_error = e
            if intento < intentos:
                time.sleep(espera_seg)
    raise ultimo_error


def nombre_dia(fecha_iso):
    fecha = datetime.date.fromisoformat(fecha_iso)
    return DIAS_ES[fecha.weekday()]


def formatear_bloque_fuente(emoji, nombre_fuente, forecast, unidad):
    lineas = [f"{emoji} {nombre_fuente}"]
    for fecha, tmax, _tmin in forecast:
        lineas.append(f"- {nombre_dia(fecha)} {fecha}: max {tmax}°{unidad}")
    return "\n".join(lineas)


def celsius_a_fahrenheit(temp_c):
    if temp_c is None:
        return None
    return round(temp_c * 9 / 5 + 32, 1)


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_wu_apikey():
    """Extrae el apiKey publico embebido en el HTML de wunderground.com."""
    resp = requests.get("https://www.wunderground.com/", headers=BROWSER_HEADERS, timeout=20)

    match = re.search(r'"apiKey"\s*:\s*"([a-f0-9]{20,40})"', resp.text)
    if match:
        return match.group(1)

    match = re.search(r'apiKey["\']?\s*[:=]\s*["\']([a-f0-9]{20,40})["\']', resp.text, re.IGNORECASE)
    if match:
        return match.group(1)

    match = re.search(r'[?&]apiKey=([a-f0-9]{20,40})', resp.text, re.IGNORECASE)
    if match:
        return match.group(1)

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


DIAS_EN = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}

# Ultima linea de diagnostico de WU (la usa main() para mandarla por Telegram a las 20h ART).
ULTIMO_WU_DEBUG = ""


def corregir_fecha_por_dia_semana(fecha, nombre_dia):
    """WU manda, para cada dia, su fecha (validTimeLocal) y su nombre de dia (dayOfWeek).
    Si no coinciden (ej. la fecha dice jueves 8 pero el dia dice Wednesday), la fecha esta
    corrida un dia respecto de los valores: se usa el dia de la semana, que viaja junto con
    los valores, y se devuelve la fecha mas cercana con ese dia."""
    try:
        wd = DIAS_EN.get(str(nombre_dia).strip().lower())
        d = datetime.date.fromisoformat(fecha)
    except Exception:
        return fecha
    if wd is None or d.weekday() == wd:
        return fecha
    for k in (-1, 1, -2, 2, -3, 3):
        d2 = d + datetime.timedelta(days=k)
        if d2.weekday() == wd:
            return d2.isoformat()
    return fecha


def get_wu_forecast(lat, lon, api_key, days, unidad="C", modo_diurno=False):
    url = "https://api.weather.com/v3/wx/forecast/daily/5day"
    params = {
        "apiKey": api_key,
        "geocode": f"{lat},{lon}",
        "format": "json",
        "units": "e" if unidad == "F" else "m",
        "language": "en-US",
    }
    resp = requests.get(url, params=params, headers=WU_API_HEADERS, timeout=20)

    if resp.status_code != 200:
        clave_enmascarada = f"{api_key[:6]}...{api_key[-4:]}" if len(api_key) > 12 else "(clave corta)"
        cuerpo = resp.text[:250].replace("\n", " ")
        raise RuntimeError(
            f"weather.com respondio {resp.status_code} (clave usada: {clave_enmascarada}). Cuerpo: {cuerpo}"
        )

    data = resp.json()

    max_temps = data.get("calendarDayTemperatureMax")
    min_temps = data.get("calendarDayTemperatureMin")
    valid_dates = data.get("validTimeLocal")
    if not max_temps or not valid_dates:
        raise RuntimeError(f"Respuesta inesperada de weather.com: {str(data)[:300]}")

    min_temps = min_temps or [None] * len(max_temps)
    dias_semana = data.get("dayOfWeek") or []

    # modo_diurno: justo despues de la medianoche local, el campo "calendarDayTemperatureMax" de WU
    # viene desfasado un dia en algunas ciudades (trae el valor del dia anterior). En ese horario se
    # usa "temperatureMax", la maxima diurna: es la misma que muestra la web de Wunderground.
    cal_original = list(max_temps)
    if modo_diurno:
        tmax_dia = data.get("temperatureMax") or []
        max_temps = [tmax_dia[i] if i < len(tmax_dia) else None for i in range(len(valid_dates))]

    resultado = []
    hubo_ajuste = False
    for i, (fecha_iso, tmax, tmin) in enumerate(list(zip(valid_dates, max_temps, min_temps))[:days]):
        if modo_diurno and tmax is None:
            continue  # dia ya terminado (la maxima diurna ya paso): no hay pronostico que guardar
        fecha = fecha_iso[:10]
        if i < len(dias_semana):
            fecha_ok = corregir_fecha_por_dia_semana(fecha, dias_semana[i])
            if fecha_ok != fecha:
                hubo_ajuste = True
            fecha = fecha_ok
        resultado.append((fecha, tmax, tmin))

    # DIAGNOSTICO TEMPORAL: datos "crudos" de WU (log de GitHub y, a las 20h ART, Telegram).
    global ULTIMO_WU_DEBUG
    try:
        dp = ((data.get("daypart") or [{}])[0] or {}).get("temperature") or []
        ahora = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M")
        ULTIMO_WU_DEBUG = (
            f"{ahora}UTC fechas={[str(d)[5:10] for d in valid_dates[:3]]} "
            f"dia={[str(x)[:3] for x in dias_semana[:3]]} "
            f"cal={cal_original[:3]} tmax={(data.get('temperatureMax') or [])[:3]} dp={dp[:4]}"
            + (f" CORREGIDO={[r[0][5:] for r in resultado[:3]]}" if hubo_ajuste else "")
            + (" MODO=diurna" if modo_diurno else "")
        )
        print(f"[WU-DEBUG] {lat},{lon} {ULTIMO_WU_DEBUG}")
    except Exception as e:
        ULTIMO_WU_DEBUG = f"(no se pudo armar: {e})"
        print(f"[WU-DEBUG] {ULTIMO_WU_DEBUG}")

    return resultado


def obtener_wu_key_validada(lat_prueba, lon_prueba):
    """Consigue un apiKey de WU y lo VALIDA haciendo un pedido de prueba real
    contra el endpoint de pronostico. Si la clave esta rota/vencida, la
    descarta por completo, espera WU_KEY_ESPERA_SEG, y va a buscar una
    clave NUEVA desde cero (no reintenta con la misma) -- hasta
    WU_KEY_INTENTOS veces en total."""
    ultimo_error = None
    for intento in range(1, WU_KEY_INTENTOS + 1):
        try:
            key = get_wu_apikey()
            get_wu_forecast(lat_prueba, lon_prueba, key, 1, "C")  # pedido de prueba, descartamos el resultado
            return key
        except Exception as e:
            ultimo_error = e
            if intento < WU_KEY_INTENTOS:
                time.sleep(WU_KEY_ESPERA_SEG)
    raise ultimo_error


def get_yr_forecast(lat, lon, tz_name, days, unidad="C"):
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
    resultado = []
    for fecha in fechas_ordenadas:
        tmax_c = max(temps_por_dia[fecha])
        tmin_c = min(temps_por_dia[fecha])
        if unidad == "F":
            resultado.append((fecha, celsius_a_fahrenheit(tmax_c), celsius_a_fahrenheit(tmin_c)))
        else:
            resultado.append((fecha, tmax_c, tmin_c))
    return resultado


def append_rows(rows):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    file_exists = os.path.isfile(DATA_FILE)
    with open(DATA_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(
                ["timestamp_utc", "hora_consulta_utc", "ciudad", "fuente", "fecha_objetivo", "temp_max_c", "temp_min_c", "unidad", "dias_antes"]
            )
        writer.writerows(rows)


def send_to_sheets(rows):
    webhook_url = os.environ.get("GOOGLE_SHEETS_WEBHOOK_URL")
    if not webhook_url:
        print("Google Sheets no configurado (falta GOOGLE_SHEETS_WEBHOOK_URL), salteando")
        return
    columnas = ["timestamp_utc", "hora_consulta_utc", "ciudad", "fuente", "fecha_objetivo", "temp_max_c", "temp_min_c", "unidad", "dias_antes"]
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


def obtener_hora_objetivo(now):
    cron_str = os.environ.get("CRON_PROGRAMADO", "").strip()
    if cron_str:
        partes = cron_str.split()
        if len(partes) >= 2:
            try:
                hora = int(partes[1])
                return f"{hora:02d}:00"
            except ValueError:
                pass
    return now.strftime("%H:00")


def main():
    config = load_config()
    days = config.get("dias_a_registrar", 3)
    # "maxima_web": true  -> WU se guarda con la maxima diurna (la misma que muestra la pestaña de 10 dias de la web)
    maxima_web = bool(config.get("maxima_web", False))
    now = datetime.datetime.now(datetime.timezone.utc)
    hora_consulta = obtener_hora_objetivo(now)
    hora_num = int(hora_consulta[:2])
    es_checkpoint = hora_num in CHECKPOINT_HORAS_UTC or os.environ.get("FORZAR_TELEGRAM") == "true"

    rows = []
    bloques_mensaje = [f"Registro de pronosticos - {now.strftime('%Y-%m-%d %H:%M UTC')}"]

    cities = config["cities"]
    debug_europa = []

    # Conseguimos y VALIDAMOS la clave de WU una sola vez, usando la primera
    # ciudad de la lista como prueba. Si esta rota, reintenta con clave
    # NUEVA cada WU_KEY_ESPERA_SEG segundos, hasta WU_KEY_INTENTOS veces.
    wu_key = None
    if cities:
        primera = cities[0]
        try:
            wu_key = obtener_wu_key_validada(primera["lat"], primera["lon"])
        except Exception as e:
            minutos_totales = (WU_KEY_INTENTOS - 1) * WU_KEY_ESPERA_SEG // 60
            bloques_mensaje.append(
                f"⚠️ WunderGround no disponible esta corrida (tras {WU_KEY_INTENTOS} intentos en ~{minutos_totales} min): {e}"
            )

    for city in cities:
        name = city["name"]
        flag = city.get("flag", "")
        unidad = city.get("unidad", "C")
        lat, lon, tz_name = city["lat"], city["lon"], city["tz"]

        fecha_local_ciudad = now.astimezone(ZoneInfo(tz_name)).date()

        partes_ciudad = [f"{flag} <b>{name}</b>".strip()]

        if wu_key:
            try:
                # Pedimos 1 dia de mas porque, de madrugada (hora local), WU todavia lista como
                # primer dia el dia que YA TERMINO (ej. a las 01:00 del 8 sigue mostrando el 7).
                # Ese dia viejo se descarta: ya no es un pronostico.
                hora_local = now.astimezone(ZoneInfo(tz_name)).hour
                diurna = maxima_web or (tz_name.startswith("Europe/") and hora_local < 2)
                lat_wu = city.get("lat_wu", lat)
                lon_wu = city.get("lon_wu", lon)
                wu_forecast = con_reintentos(get_wu_forecast, WU_FORECAST_INTENTOS, WU_FORECAST_ESPERA_SEG, lat_wu, lon_wu, wu_key, days + 1, unidad, modo_diurno=diurna)
                wu_forecast = [f for f in wu_forecast if datetime.date.fromisoformat(f[0]) >= fecha_local_ciudad][:days]
                if tz_name.startswith("Europe/"):
                    debug_europa.append(f"{name.split(' (')[0]}: {ULTIMO_WU_DEBUG}")
                for fecha, tmax, tmin in wu_forecast:
                    dias_antes = (datetime.date.fromisoformat(fecha) - fecha_local_ciudad).days
                    rows.append([now.isoformat(), hora_consulta, name, "wunderground", fecha, tmax, tmin, unidad, dias_antes])
                partes_ciudad.append(formatear_bloque_fuente("2️⃣", "WU", wu_forecast, unidad))
            except Exception as e:
                partes_ciudad.append(f"⚠️ WU error: {e}")

        # Si la ciudad tiene "usar_yr": false en config.json, se saltea Yr.no
        if city.get("usar_yr", True):
            try:
                yr_forecast = con_reintentos(get_yr_forecast, YR_INTENTOS, YR_ESPERA_SEG, lat, lon, tz_name, days, unidad)
                for fecha, tmax, tmin in yr_forecast:
                    dias_antes = (datetime.date.fromisoformat(fecha) - fecha_local_ciudad).days
                    rows.append([now.isoformat(), hora_consulta, name, "yr.no", fecha, tmax, tmin, unidad, dias_antes])
                partes_ciudad.append(formatear_bloque_fuente("3️⃣", "YR", yr_forecast, unidad))
            except Exception as e:
                partes_ciudad.append(f"⚠️ Yr error: {e}")

        bloques_mensaje.append("\n".join(partes_ciudad))

    if rows:
        append_rows(rows)
        send_to_sheets(rows)

    mensaje_final = "\n\n".join(bloques_mensaje)
    if es_checkpoint:
        send_telegram(mensaje_final)
        # DIAGNOSTICO TEMPORAL: a las 20h ART (23 UTC) y en pruebas manuales, segundo mensaje con los datos crudos de WU en Europa
        if debug_europa and (hora_num == 23 or os.environ.get("FORZAR_TELEGRAM") == "true"):
            send_telegram("🔧 Diagnostico WU Europa\n" + "\n".join(l.replace("<", "").replace(">", "") for l in debug_europa))
    else:
        print("(corrida silenciosa, no es horario de aviso -> no se manda Telegram)")
    print(mensaje_final)


if __name__ == "__main__":
    main()
