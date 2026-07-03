"""
Open-Meteo ECMWF ensemble forecast pulls.

Free API, no key required. Pulls the 51-member ECMWF IFS 0.25° ensemble at
the exact lat/lon of each NWS settlement station.
"""

from datetime import datetime, timedelta

import requests

from weather.stations import KALSHI_STATIONS

ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"


def get_ensemble_forecast(city_key: str, forecast_days: int = 3) -> dict:
    """
    Returns the raw 51-member hourly temperature forecast for the NWS
    settlement station of the given city. Temperatures come back in
    Celsius from the API.
    """
    station = KALSHI_STATIONS[city_key]
    params = {
        "latitude": station["lat"],
        "longitude": station["lon"],
        "hourly": "temperature_2m",
        "models": "ecmwf_ifs025",
        "forecast_days": forecast_days,
        "timezone": station["timezone"],
    }
    r = requests.get(ENSEMBLE_URL, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def extract_daily_high_per_member(forecast_data: dict) -> dict:
    """
    From the raw ensemble response, extract the daily maximum temperature
    (in Fahrenheit) for each ensemble member for today and tomorrow.

    Returns:
        {
            "today":    [member_0_max_F, member_1_max_F, ...],
            "tomorrow": [member_0_max_F, member_1_max_F, ...],
        }
    """
    hourly = forecast_data.get("hourly", {})
    times = hourly.get("time", [])

    member_keys = sorted(k for k in hourly if k.startswith("temperature_2m_member"))

    today_str = datetime.now().strftime("%Y-%m-%d")
    tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    result = {"today": [], "tomorrow": []}
    for key in member_keys:
        temps_c = hourly[key]
        today_temps = [
            t for t, ts in zip(temps_c, times) if ts.startswith(today_str) and t is not None
        ]
        tomorrow_temps = [
            t for t, ts in zip(temps_c, times) if ts.startswith(tomorrow_str) and t is not None
        ]

        today_max_f = max(t * 9 / 5 + 32 for t in today_temps) if today_temps else None
        tomorrow_max_f = max(t * 9 / 5 + 32 for t in tomorrow_temps) if tomorrow_temps else None

        result["today"].append(today_max_f)
        result["tomorrow"].append(tomorrow_max_f)

    return result
