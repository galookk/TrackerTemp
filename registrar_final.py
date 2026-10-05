"""
Registra la temperatura maxima OBSERVADA (la que despues usa Polymarket
para resolver) de cada ciudad de config.json, para el dia local que ya
termino, y la manda a Google Sheets (mismo webhook que el bot de pronosticos)
y a data/finales.csv como respaldo.

Usa el endpoint de historial de wunderground (el mismo que la pagina web).
Si falla, avisa por Telegram en vez de fallar en silencio.
"""

import os
import re
import csv
import html
import datetime
from zoneinfo import ZoneInfo

import requests

from scrape import load_config, send_to_sheets, send_telegram, con_reintentos, WU_API_HEADERS

KEY_HISTORIAL = "e1f10a1e78da46f5b10a1e78da96f525"
ARCHIVO = "data/finales.csv"
MIN_OBSERVACIONES = 10  # si hay menos, el dia probablemente esta incompleto

PAISES = {
    "KAUS": "US", "KLGA": "US", "KSEA": "US", "KSFO": "US",
    "SAEZ": "AR", "EHAM": "NL", "EGLC": "GB", "EDDM": "DE",
    "RJTT": "JP", "RKPK": "KR", "WSSS": "SG", "NZWN": "NZ", "EFHK": "FI",
}

COLUMNAS = ["timestamp_utc", "hora_consulta_utc", "ciudad", "fuente",
            "fecha_objetivo", "temp_max_c", "temp_min_c", "unidad", "dias_antes"]


def maxima_observada(icao, pais, unidad, fecha_iso):
    fecha = fecha_iso.replace("-", "")
    url = f"https://api.weather.com/v1/location/{icao}:9:{pais}/observations/historical.json"
    params = {
        "apiKey": KEY_HISTORIAL,
        "units": "e" if unidad == "F" else "m",
        "startDate": fecha,
        "endDate": fecha,
    }
    resp = requests.get(url, params=params, headers=WU_API_HEADERS, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"status {resp.status_code}: {resp.text[:80]}")
    obs = resp.json().get("observations", [])
    temps = [o["temp"] for o in obs if o.get("temp") is not None]
    if not temps:
        raise RuntimeError("sin observaciones")
    return max(temps), min(temps), len(temps)


def guardar_csv(rows):
    os.makedirs(os.path.dirname(ARCHIVO), exist_ok=True)
    existe = os.path.isfile(ARCHIVO)
    with open(ARCHIVO, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not existe:
            w.writerow(COLUMNAS)
        w.writerows(rows)


def main():
    dias_atras = int(os.environ.get("DIAS_ATRAS") or 1)
    now = datetime.datetime.now(datetime.timezone.utc)
    rows = []
    avisos = []

    for city in load_config()["cities"]:
        nombre = city["name"]
        unidad = city.get("unidad", "C")
        tz = ZoneInfo(city["tz"])
        fecha = (now.astimezone(tz).date() - datetime.timedelta(days=dias_atras)).isoformat()

        m = re.search(r"\(([A-Z]{4})", nombre)
        icao = m.group(1) if m else None
        pais = PAISES.get(icao)
        if not icao or not pais:
            avisos.append(f"{nombre}: no se encontro el codigo de estacion o el pais")
            continue

        try:
            tmax, tmin, n = con_reintentos(maxima_observada, 3, 10, icao, pais, unidad, fecha)
        except Exception as e:
            avisos.append(f"{nombre} ({fecha}): {e}")
            continue

        if n < MIN_OBSERVACIONES:
            avisos.append(f"{nombre} ({fecha}): solo {n} observaciones, no se guardo")
            continue

        rows.append([now.isoformat(), "final", nombre, "observado", fecha, tmax, tmin, unidad, 0])
        print(f"{nombre} {fecha}: max {tmax}°{unidad} ({n} observaciones)")

    if rows:
        guardar_csv(rows)
        send_to_sheets(rows)

    if avisos:
        texto = "⚠️ Registro de maximas observadas - problemas:\n" + "\n".join(avisos)
        print(texto)
        send_telegram(html.escape(texto))


if __name__ == "__main__":
    main()
