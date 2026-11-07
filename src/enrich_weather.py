"""
Jointure meteo par match.

Compromis valide avec l'enseignant : valeur horaire au plus proche du coup
d'envoi quand l'heure est connue (saisons >= 2019-2020), sinon agregat
journalier (saisons 2016-2017 a 2018-2019, ou l'heure n'est pas fournie par
la source Ligue 1).
"""


def round_kickoff_to_hour(time_str: str) -> str:
    """'19:45' -> '20:00', '19:10' -> '19:00'. Les minutes sont ignorees dans
    la donnee Open-Meteo horaire (une valeur par heure pleine)."""
    hour, minute = (int(part) for part in time_str.strip().split(":")[:2])
    if minute >= 30:
        hour += 1
    hour = hour % 24
    return f"{hour:02d}:00"


class TeamWeatherLookup:
    """Index en memoire du JSON brut Open-Meteo d'un stade (horaire + journalier)."""

    def __init__(self, payload: dict):
        hourly = payload.get("hourly") or {}
        self._hourly = dict(
            zip(
                hourly.get("time", []),
                zip(
                    hourly.get("temperature_2m", []),
                    hourly.get("precipitation", []),
                    hourly.get("wind_speed_10m", []),
                ),
            )
        )

        daily = payload.get("daily") or {}
        self._daily = dict(
            zip(
                daily.get("time", []),
                zip(
                    daily.get("temperature_2m_mean", []),
                    daily.get("precipitation_sum", []),
                    daily.get("wind_speed_10m_max", []),
                ),
            )
        )

    def lookup(self, date_iso: str, kickoff_time: str | None):
        """Retourne (temperature, precipitation, vent) ou (None, None, None)."""
        return self.lookup_with_source(date_iso, kickoff_time)[:3]

    def lookup_with_source(self, date_iso: str, kickoff_time: str | None):
        """Comme lookup, plus la granularite utilisee : hourly, daily ou none."""
        if kickoff_time:
            key = f"{date_iso}T{round_kickoff_to_hour(kickoff_time)}"
            if key in self._hourly:
                return (*self._hourly[key], "hourly")
        if date_iso in self._daily:
            return (*self._daily[date_iso], "daily")
        return (None, None, None, "none")
