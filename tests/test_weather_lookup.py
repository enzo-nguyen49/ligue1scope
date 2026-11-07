import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import collect_weather  # noqa: E402
from enrich_weather import TeamWeatherLookup, round_kickoff_to_hour  # noqa: E402


def make_sample_payload() -> dict:
    """Structure identique a celle confirmee en reel (hourly_units / daily_units
    avec temperature_2m, precipitation, wind_speed_10m / _mean / _sum / _max)."""
    return {
        "hourly": {
            "time": ["2024-08-10T18:00", "2024-08-10T19:00", "2024-08-10T20:00"],
            "temperature_2m": [22.0, 21.0, 19.5],
            "precipitation": [0.0, 0.0, 0.2],
            "wind_speed_10m": [10.0, 12.0, 15.0],
        },
        "daily": {
            "time": ["2024-08-10", "2024-08-11"],
            "temperature_2m_mean": [20.5, 21.0],
            "precipitation_sum": [0.5, 0.0],
            "wind_speed_10m_max": [18.0, 20.0],
        },
    }


def test_round_kickoff_to_hour_arrondit_dans_les_deux_sens():
    assert round_kickoff_to_hour("19:45") == "20:00"
    assert round_kickoff_to_hour("19:10") == "19:00"
    assert round_kickoff_to_hour("20:00") == "20:00"


def test_lookup_utilise_l_heure_quand_le_coup_d_envoi_est_connu():
    lookup = TeamWeatherLookup(make_sample_payload())
    temp, precip, wind = lookup.lookup("2024-08-10", "19:45")  # arrondi -> 20:00
    assert (temp, precip, wind) == (19.5, 0.2, 15.0)


def test_lookup_retombe_sur_le_journalier_si_heure_inconnue():
    lookup = TeamWeatherLookup(make_sample_payload())
    temp, precip, wind = lookup.lookup("2024-08-10", None)
    assert (temp, precip, wind) == (20.5, 0.5, 18.0)


def test_lookup_renvoie_none_si_date_absente_des_deux_index():
    lookup = TeamWeatherLookup(make_sample_payload())
    assert lookup.lookup("2030-01-01", None) == (None, None, None)


def test_collect_one_stadium_reessaie_sur_429(monkeypatch, tmp_path):
    calls = []

    class FakeResponse:
        def __init__(self, status_code, payload=None, headers=None):
            self.status_code = status_code
            self._payload = payload or {}
            self.headers = headers or {}
            self.text = "{}"

        def json(self):
            return self._payload

    def fake_get(url, params, headers, timeout):
        calls.append(1)
        if len(calls) == 1:
            return FakeResponse(
                429,
                {"error": True, "reason": "Minutely API request limit exceeded."},
                {"Retry-After": "1"},
            )
        return FakeResponse(200, {
            "hourly": {"time": ["2024-08-10T18:00"], "temperature_2m": [22.0], "precipitation": [0.0], "wind_speed_10m": [12.0]},
            "daily": {"time": ["2024-08-10"], "temperature_2m_mean": [22.0], "precipitation_sum": [0.0], "wind_speed_10m_max": [12.0]},
        })

    monkeypatch.setattr(collect_weather.requests, "get", fake_get)
    monkeypatch.setattr(collect_weather, "RAW_WEATHER_DIR", tmp_path)
    monkeypatch.setattr(collect_weather.time, "sleep", lambda *_args, **_kwargs: None)

    result = collect_weather.collect_one_stadium(
        team="Lyon",
        latitude="45.76",
        longitude="4.84",
        start_date="2024-08-10",
        end_date="2024-08-11",
        stamp="20240925T000000Z",
    )

    assert result["status"] == "ok"
    assert len(calls) == 2


def test_split_date_range_by_season_decoupe_par_saison():
    ranges = collect_weather.split_date_range_by_season(
        datetime(2016, 8, 20),
        datetime(2026, 5, 10),
    )

    assert len(ranges) == 10
    assert ranges[0][0] == "2016-2017"
    assert ranges[0][1].strftime("%Y-%m-%d") == "2016-08-20"
    assert ranges[0][2].strftime("%Y-%m-%d") == "2017-06-30"
    assert ranges[-1][0] == "2025-2026"
    assert ranges[-1][2].strftime("%Y-%m-%d") == "2026-05-10"
