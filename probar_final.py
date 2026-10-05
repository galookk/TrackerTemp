"""
Prueba: trae la temperatura maxima OBSERVADA de ayer en la estacion de
cada ciudad (WunderGround / weather.com) y la muestra en pantalla.
No guarda nada, no toca tus datos. Solo sirve para comprobar que el
metodo funciona y que coincide con lo que ves en wunderground.com.
"""

import datetime
import requests

# Reutilizamos funciones que ya existen en scrape.py
from scrape import get_wu_apikey, WU_API_HEADERS

# nombre, codigo de estacion, pais, unidad
CIUDADES = [
    ("Austin", "KAUS", "US", "F"),
    ("New York", "KLGA", "US", "F"),
    ("Seattle", "KSEA", "US", "F"),
    ("Buenos Aires", "SAEZ", "AR", "C"),
    ("Amsterdam", "EHAM", "NL", "C"),
    ("Londres", "EGLC", "GB", "C"),
    ("Munich", "EDDM", "DE", "C"),
    ("Tokyo", "RJTT", "JP", "C"),
    ("Busan", "RKPK", "KR", "C"),
]


def maxima_observada(api_key, icao, pais, unidad, fecha_yyyymmdd):
    url = f"https://api.weather.com/v1/location/{icao}:9:{pais}/observations/historical.json"
    params = {
        "apiKey": api_key,
        "units": "e" if unidad == "F" else "m",
        "startDate": fecha_yyyymmdd,
        "endDate": fecha_yyyymmdd,
    }
    resp = requests.get(url, params=params, headers=WU_API_HEADERS, timeout=20)
    if resp.status_code != 200:
        return None, 0, f"status {resp.status_code}: {resp.text[:120]}"
    obs = resp.json().get("observations", [])
    temps = [o["temp"] for o in obs if o.get("temp") is not None]
    if not temps:
        return None, 0, "sin observaciones"
    return max(temps), len(temps), ""


def main():
    ayer = datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)
    fecha = ayer.strftime("%Y%m%d")
    print(f"Fecha consultada: {ayer.isoformat()}")

    key = get_wu_apikey()
    print("Clave obtenida OK\n")

    for nombre, icao, pais, unidad in CIUDADES:
        try:
            tmax, n, error = maxima_observada(key, icao, pais, unidad, fecha)
        except Exception as e:
            tmax, n, error = None, 0, str(e)
        if tmax is None:
            print(f"{nombre:14} ({icao}): ERROR -> {error}")
        else:
            print(f"{nombre:14} ({icao}): max {tmax}°{unidad}  ({n} observaciones)")


if __name__ == "__main__":
    main()
