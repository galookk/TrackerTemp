"""
Prueba v2: prueba DOS formas de traer la temperatura maxima observada de
ayer para cada ciudad de config.json, y muestra los resultados lado a lado.
No guarda nada, no toca tus datos.

  A = endpoint v3 "dailysummary" con la clave que ya usa tu bot
  B = endpoint v1 "historical" (el que usa la pagina de historial de
      wunderground) con la clave publica de esa pagina
"""

import re
import datetime
import requests

from scrape import get_wu_apikey, WU_API_HEADERS, load_config

KEY_HISTORIAL = "e1f10a1e78da46f5b10a1e78da96f525"

PAISES = {
    "KAUS": "US", "KLGA": "US", "KSEA": "US", "KSFO": "US",
    "SAEZ": "AR", "EHAM": "NL", "EGLC": "GB", "EDDM": "DE",
    "RJTT": "JP", "RKPK": "KR", "WSSS": "SG", "NZWN": "NZ", "EFHK": "FI",
}


def probar_a(key, lat, lon, unidad, fecha_iso):
    url = "https://api.weather.com/v3/wx/conditions/historical/dailysummary/30day"
    params = {
        "apiKey": key,
        "geocode": f"{lat},{lon}",
        "format": "json",
        "units": "e" if unidad == "F" else "m",
        "language": "en-US",
    }
    resp = requests.get(url, params=params, headers=WU_API_HEADERS, timeout=20)
    if resp.status_code != 200:
        return f"error {resp.status_code}: {resp.text[:60]}"
    data = resp.json()
    maximas = data.get("temperatureMax") or data.get("calendarDayTemperatureMax")
    fechas = data.get("validTimeLocal") or []
    if not maximas or not fechas:
        return f"respuesta rara, campos: {list(data.keys())[:8]}"
    for f, t in zip(fechas, maximas):
        if f[:10] == fecha_iso:
            return f"max {t}"
    return f"no estaba la fecha (ultima: {fechas[-1][:10]})"


def probar_b(icao, pais, unidad, fecha_iso):
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
        return f"error {resp.status_code}: {resp.text[:60]}"
    obs = resp.json().get("observations", [])
    temps = [o["temp"] for o in obs if o.get("temp") is not None]
    if not temps:
        return "sin observaciones"
    return f"max {max(temps)} ({len(temps)} obs)"


def main():
    ayer = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat()
    print(f"Fecha consultada: {ayer}\n")
    key = get_wu_apikey()
    print("Clave del bot obtenida OK\n")

    for city in load_config()["cities"]:
        nombre = city["name"]
        unidad = city.get("unidad", "C")
        m = re.search(r"\(([A-Z]{4})", nombre)
        icao = m.group(1) if m else None
        pais = PAISES.get(icao)

        try:
            a = probar_a(key, city["lat"], city["lon"], unidad, ayer)
        except Exception as e:
            a = f"falla: {e}"
        try:
            b = probar_b(icao, pais, unidad, ayer) if icao and pais else "sin codigo de estacion en el nombre"
        except Exception as e:
            b = f"falla: {e}"

        print(f"{nombre} [{unidad}]")
        print(f"   A (v3, clave del bot):   {a}")
        print(f"   B (v1, clave historial): {b}")


if __name__ == "__main__":
    main()
