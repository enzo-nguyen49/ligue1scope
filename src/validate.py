"""
Validation - regles de qualite automatisees (contrat v2.0).

Chaque regle a un identifiant, une dimension, une condition, un compteur
d'echecs et une action :
- REJETER   : la ligne part dans data/rejected avec son motif (jamais supprimee
              silencieusement) ;
- DEDUPLIQUER : la 1re occurrence est gardee, les copies sont rejetees et comptees ;
- SIGNALER  : la ligne est gardee, la valeur fautive est neutralisee et marquee ;
- ALERTER   : controle sur le jeu complet, avertissement dans le run report ;
- BLOQUER   : controle sur le jeu complet, le run passe en FAIL.

Regles ligne a ligne
  R1 completude_champs_obligatoires  Completude  REJETER
  R2 validite_date                   Validite    REJETER
  R3 validite_buts                   Validite    REJETER
  R4 coherence_saison                Coherence   REJETER
  R5 coherence_resultat              Coherence   REJETER
  R6 coherence_equipes               Coherence   REJETER
  R7 unicite_match                   Unicite     DEDUPLIQUER
  R8 validite_cotes                  Validite    SIGNALER
Regles sur le jeu complet
  R9  fraicheur                      Fraicheur   ALERTER
  R10 volumetrie_saison              Completude  ALERTER
  R11 tracabilite                    Tracabilite BLOQUER
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from common import REQUIRED_SOURCE_COLUMNS

log = logging.getLogger("ligue1scope")

DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y")
ODDS_COLUMNS = ["odds_home_avg", "odds_draw_avg", "odds_away_avg"]

RULES = [
    ("R1", "completude_champs_obligatoires", "Completude", "REJETER",
     "Date, HomeTeam, AwayTeam, FTHG, FTAG et FTR sont renseignes"),
    ("R2", "validite_date", "Validite", "REJETER",
     "Date est une date reelle au format JJ/MM/AA ou JJ/MM/AAAA"),
    ("R3", "validite_buts", "Validite", "REJETER",
     "FTHG et FTAG sont des entiers entre goals_min et goals_max"),
    ("R4", "coherence_saison", "Coherence", "REJETER",
     "la date du match tombe entre le 1er juillet et le 30 juin de la saison du fichier"),
    ("R5", "coherence_resultat", "Coherence", "REJETER",
     "FTR vaut H, D ou A et correspond au score final"),
    ("R6", "coherence_equipes", "Coherence", "REJETER",
     "l'equipe a domicile est differente de l'equipe a l'exterieur"),
    ("R7", "unicite_match", "Unicite", "DEDUPLIQUER",
     "la cle metier (saison, date, domicile, exterieur) est unique ; 1re occurrence gardee"),
    ("R8", "validite_cotes", "Validite", "SIGNALER",
     "les cotes moyennes sont numeriques et entre odds_min et odds_max (sinon mises a NULL)"),
    ("R9", "fraicheur", "Fraicheur", "ALERTER",
     "le match le plus recent date de moins de freshness_max_days jours"),
    ("R10", "volumetrie_saison", "Completude", "ALERTER",
     "chaque saison contient au moins 70 % des n*(n-1) matchs attendus"),
    ("R11", "tracabilite", "Tracabilite", "BLOQUER",
     "chaque ligne acceptee conserve source_file, raw_sha256 et source_row"),
]


@dataclass
class ValidationResult:
    accepted: pd.DataFrame
    rejected: pd.DataFrame
    rules: list[dict]
    duplicates_removed: int
    alerts: list[str] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    freshness: dict = field(default_factory=dict)

    @property
    def counters(self) -> dict[str, int]:
        return {rule["rule"]: rule["failures"] for rule in self.rules}


def parse_match_date(value) -> pd.Timestamp:
    if not isinstance(value, str):
        return pd.NaT
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
        # "%d/%m/%Y" accepte "12/08/16" comme l'an 16 : on l'ecarte.
        if parsed.year >= 1990:
            return pd.Timestamp(parsed)
    return pd.NaT


def build_match_id(season: str, match_date: pd.Timestamp, home: str, away: str) -> str:
    date_str = match_date.strftime("%Y%m%d") if pd.notna(match_date) else "INVALIDDATE"
    return f"{season}_{date_str}_{home}_{away}"


def _season_bounds(season: pd.Series) -> tuple[pd.Series, pd.Series]:
    start_year = season.str.slice(0, 4).astype(int)
    start = pd.to_datetime(start_year.astype(str) + "-07-01")
    end = pd.to_datetime((start_year + 1).astype(str) + "-06-30")
    return start, end


def apply_quality_rules(
    raw: pd.DataFrame,
    quality: dict | None = None,
    reference_date: datetime | None = None,
) -> ValidationResult:
    quality = quality or {}
    goals_min = int(quality.get("goals_min", 0))
    goals_max = int(quality.get("goals_max", 15))
    odds_min = float(quality.get("odds_min", 1.01))
    odds_max = float(quality.get("odds_max", 100.0))
    freshness_max_days = int(quality.get("freshness_max_days", 30))
    reference_date = reference_date or datetime.now(timezone.utc)

    df = raw.copy()
    for col in REQUIRED_SOURCE_COLUMNS + ODDS_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    for col in ("HomeTeam", "AwayTeam", "FTR"):
        df[col] = df[col].astype("string").str.strip()

    df["match_date"] = df["Date"].map(parse_match_date)
    df["home_goals"] = pd.to_numeric(df["FTHG"], errors="coerce")
    df["away_goals"] = pd.to_numeric(df["FTAG"], errors="coerce")
    df["match_id"] = [
        build_match_id(s, d, h, a)
        for s, d, h, a in zip(df["season"], df["match_date"], df["HomeTeam"], df["AwayTeam"])
    ]

    failures: dict[str, pd.Series] = {}

    # R1 - completude
    failures["completude_champs_obligatoires"] = df[REQUIRED_SOURCE_COLUMNS].isna().any(axis=1) | (
        df[["HomeTeam", "AwayTeam", "FTR"]].fillna("").eq("").any(axis=1)
    )
    # R2 - validite de la date (seulement si la valeur existe : sinon c'est R1)
    failures["validite_date"] = df["Date"].notna() & df["match_date"].isna()
    # R3 - validite des buts : entiers dans la plage
    goals_ok = (
        df["home_goals"].between(goals_min, goals_max)
        & df["away_goals"].between(goals_min, goals_max)
        & (df["home_goals"] % 1 == 0)
        & (df["away_goals"] % 1 == 0)
    )
    failures["validite_buts"] = df["FTHG"].notna() & df["FTAG"].notna() & ~goals_ok
    # R4 - coherence date / saison
    start, end = _season_bounds(df["season"])
    failures["coherence_saison"] = df["match_date"].notna() & ~df["match_date"].between(start, end)
    # R5 - coherence resultat / score
    expected = pd.Series(pd.NA, index=df.index, dtype="string")
    expected[df["home_goals"] > df["away_goals"]] = "H"
    expected[df["home_goals"] < df["away_goals"]] = "A"
    expected[df["home_goals"] == df["away_goals"]] = "D"
    failures["coherence_resultat"] = df["FTR"].notna() & goals_ok & (df["FTR"] != expected).fillna(True)
    # R6 - coherence equipes
    failures["coherence_equipes"] = df["HomeTeam"].notna() & (df["HomeTeam"] == df["AwayTeam"]).fillna(False)

    reasons = pd.Series("", index=df.index, dtype="object")
    for rule_name, mask in failures.items():
        mask = mask.fillna(False).astype(bool)
        failures[rule_name] = mask
        reasons[mask] = reasons[mask] + rule_name + ";"

    # R7 - unicite : evaluee seulement entre lignes valides, pour qu'une ligne
    # invalide ne "vole" jamais la cle d'une ligne correcte.
    valid_so_far = reasons == ""
    dup_mask = pd.Series(False, index=df.index)
    dup_mask[valid_so_far] = df.loc[valid_so_far].duplicated(subset="match_id", keep="first")
    failures["unicite_match"] = dup_mask
    reasons[dup_mask] = reasons[dup_mask] + "unicite_match;"

    # R8 - cotes : signalees et neutralisees, la ligne reste exploitable
    odds_invalid = pd.Series(False, index=df.index)
    for col in ODDS_COLUMNS:
        values = pd.to_numeric(df[col], errors="coerce")
        bad = df[col].notna() & ~values.between(odds_min, odds_max)
        odds_invalid |= bad.fillna(False)
        df[col] = values.where(~bad)
    df["odds_valid"] = ~odds_invalid & df[ODDS_COLUMNS].notna().all(axis=1)
    failures["validite_cotes"] = odds_invalid

    reasons = reasons.str.rstrip(";")
    is_rejected = reasons != ""

    rejected = raw.loc[is_rejected].copy()
    rejected["match_id"] = df.loc[is_rejected, "match_id"]
    rejected["rejection_reasons"] = reasons[is_rejected]

    accepted = df.loc[~is_rejected].copy()
    accepted["home_goals"] = accepted["home_goals"].astype(int)
    accepted["away_goals"] = accepted["away_goals"].astype(int)

    alerts: list[str] = []
    blocking: list[str] = []
    dataset_failures: dict[str, int] = {}

    # R9 - fraicheur
    latest = accepted["match_date"].max() if not accepted.empty else pd.NaT
    ref = pd.Timestamp(reference_date).tz_localize(None) if pd.Timestamp(reference_date).tzinfo else pd.Timestamp(reference_date)
    age_days = int((ref.normalize() - latest).days) if pd.notna(latest) else None
    stale = age_days is None or age_days > freshness_max_days
    dataset_failures["fraicheur"] = int(stale)
    freshness = {
        "latest_match_date": latest.strftime("%Y-%m-%d") if pd.notna(latest) else None,
        "reference_date": ref.strftime("%Y-%m-%d"),
        "age_days": age_days,
        "threshold_days": freshness_max_days,
        "status": "STALE" if stale else "FRESH",
    }
    if stale:
        alerts.append(
            f"fraicheur : dernier match le {freshness['latest_match_date']} "
            f"({age_days} jours > seuil {freshness_max_days}) - saison en cours non collectee ?"
        )

    # R10 - volumetrie par saison
    low_seasons = []
    for season, group in accepted.groupby("season"):
        n_teams = pd.concat([group["HomeTeam"], group["AwayTeam"]]).nunique()
        expected_matches = n_teams * (n_teams - 1)
        if expected_matches and len(group) < 0.7 * expected_matches:
            low_seasons.append(f"{season} ({len(group)}/{expected_matches})")
    dataset_failures["volumetrie_saison"] = len(low_seasons)
    if low_seasons:
        alerts.append(f"volumetrie_saison : saisons incompletes {low_seasons}")

    # R11 - tracabilite
    lineage_cols = ["source_file", "raw_sha256", "source_row"]
    untraceable = int(accepted[lineage_cols].isna().any(axis=1).sum()) if not accepted.empty else 0
    dataset_failures["tracabilite"] = untraceable
    if untraceable:
        blocking.append(f"tracabilite : {untraceable} ligne(s) sans origine identifiable")

    rules = []
    for rule_id, name, dimension, action, description in RULES:
        count = int(failures[name].sum()) if name in failures else dataset_failures.get(name, 0)
        if count == 0:
            status = "PASS"
        elif action == "BLOQUER":
            status = "FAIL"
        else:
            status = "WARN"
        rules.append({
            "id": rule_id, "rule": name, "dimension": dimension, "action": action,
            "description": description, "failures": count, "status": status,
        })

    return ValidationResult(
        accepted=accepted,
        rejected=rejected,
        rules=rules,
        duplicates_removed=int(dup_mask.sum()),
        alerts=alerts,
        blocking=blocking,
        freshness=freshness,
    )
