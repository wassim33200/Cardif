"""Shared fixtures. The generated corpus is built once per session and reused."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

from cardif.config import load_config          # noqa: E402
from cardif.mapping import Mapper              # noqa: E402


@pytest.fixture(scope="session")
def corpus(tmp_path_factory) -> tuple[Path, list[dict]]:
    """Generate the synthetic bank workbooks once, returning the root and answer key."""
    from gen_fixtures import generate

    out = tmp_path_factory.mktemp("corpus")
    truth = generate(out, year=2025, seed=42)
    return out, truth


@pytest.fixture
def config():
    """A fresh config for each test, so learned aliases never leak between tests."""
    return load_config(ROOT / "config")


@pytest.fixture
def mapper(config):
    return Mapper(config)


@pytest.fixture
def make_frame(config):
    """Build a frame that conforms to a product's contract.

    Tests should never hand-maintain a column list: the products define them, so a
    change to produits.yaml must not mean editing a dozen fixtures.
    """
    import pandas as pd

    from cardif.consolidate import PROVENANCE_COLUMNS

    def build(
        produit: str = "ade_immobilier",
        rows: int = 2,
        source: str = "CNEP 2025/ADE_Immobilier_Mars_2025.xlsx",
        bank: str = "CNEP",
        period: str = "2025-03",
        contracts: list[str] | None = None,
        **overrides,
    ) -> pd.DataFrame:
        profile = config.produit(produit)
        contracts = contracts or [f"C-{i:03d}" for i in range(rows)]
        n = len(contracts)

        defaults = {
            "banque": [bank] * n,
            "produit_code": [produit] * n,
            "mois_reception": [period] * n,
            "num_contrat": list(contracts),
            "date_effet": pd.to_datetime([f"{period}-0{(i % 9) + 1}" for i in range(n)]),
            "prime_nette": [1000.0] * n,
            "frais": [100.0] * n,
            "prime_totale": [1100.0] * n,
            "capital_assure": [50000.0] * n,
            "montant_credit": [500000.0] * n,
            "capital_restant_du": [400000.0] * n,
            "duree": [120] * n,
            "taux_prime": [0.0045] * n,
            "nb_assures": [1] * n,
        }

        data = {}
        for column in profile.columns:
            if column in defaults:
                data[column] = list(defaults[column])
            elif column in ("nom_client",):
                data[column] = [f"Client {i}" for i in range(n)]
            elif column in ("agence",):
                data[column] = ["Alger Centre"] * n
            elif column in ("date_fin", "date_naissance"):
                data[column] = pd.to_datetime([f"{period}-01"] * n)
            else:
                data[column] = ["X"] * n

        # Source markers for every field that has a derivation rule.
        for column in profile.columns:
            field_def = config.schema_.fields.get(column)
            if field_def and field_def.derive:
                data[f"{column}_source"] = ["reported"] * n

        data["source_file"] = [source] * n
        data["source_sheet"] = ["Feuil1"] * n
        data["source_row"] = list(range(5, 5 + n))
        data["ingested_at"] = ["2025-04-01T09:00:00+00:00"] * n
        data["row_hash"] = [f"{source}-{c}" for c in contracts]

        frame = pd.DataFrame(data)
        for key, value in overrides.items():
            frame[key] = value
        # Column order must match the contract exactly.
        expected = [c for c in profile.columns]
        for column in profile.columns:
            field_def = config.schema_.fields.get(column)
            if field_def and field_def.derive:
                expected.append(f"{column}_source")
        return frame[expected + PROVENANCE_COLUMNS]

    return build
