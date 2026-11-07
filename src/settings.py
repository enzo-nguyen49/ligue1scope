"""
Chargement de la configuration (config/pipeline.yaml) et du contrat de
donnees (config/data_contract.yaml).

Tous les chemins sont resolus par rapport a une racine de projet `root`,
ce qui permet de rejouer le pipeline dans un bac a sable (tests, exercice
d'incident) sans toucher aux donnees reelles.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    root: Path
    config: dict[str, Any]
    contract: dict[str, Any]

    # -- chemins -----------------------------------------------------------
    def path(self, key: str) -> Path:
        return self.root / self.config["paths"][key]

    @property
    def raw_ligue1_dir(self) -> Path:
        return self.path("raw_ligue1")

    @property
    def raw_weather_dir(self) -> Path:
        return self.path("raw_weather")

    @property
    def quarantine_dir(self) -> Path:
        return self.path("quarantine")

    @property
    def curated_dir(self) -> Path:
        return self.path("curated")

    @property
    def rejected_dir(self) -> Path:
        return self.path("rejected")

    @property
    def reports_dir(self) -> Path:
        return self.path("reports")

    @property
    def stadiums_file(self) -> Path:
        return self.path("stadiums")

    # -- raccourcis ----------------------------------------------------------
    @property
    def pipeline_name(self) -> str:
        return self.config.get("pipeline", "ligue1scope")

    @property
    def contract_version(self) -> str:
        return str(self.contract.get("version", "unknown"))

    @property
    def seasons(self) -> list[str]:
        return list(self.config["source"]["ligue1"]["seasons"])

    @property
    def quality(self) -> dict[str, Any]:
        return self.config["quality"]

    @property
    def elo(self) -> dict[str, Any]:
        return self.config["elo"]

    @property
    def source(self) -> dict[str, Any]:
        return self.config["source"]["ligue1"]


def load_settings(root: Path | str | None = None) -> Settings:
    root = Path(root).resolve() if root else PROJECT_ROOT
    config_path = root / "config" / "pipeline.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration introuvable : {config_path}")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    contract_path = root / config.get("contract_file", "config/data_contract.yaml")
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8")) if contract_path.exists() else {}
    return Settings(root=root, config=config, contract=contract)
