"""Warehouse behaviour: append, idempotence, and the schema contract.

These are the properties that decide whether a Power BI report keeps working when next
month's file arrives.
"""

import pandas as pd
import pytest

from cardif.store import SchemaContractError, Warehouse, build_date_dimension


def frame(source="BNA 2025/mars.xlsx", period="2025-03", contracts=("C-001", "C-002")):
    n = len(contracts)
    return pd.DataFrame({
        "banque": ["BNA"] * n,
        "mois_reception": [period] * n,
        "num_contrat": list(contracts),
        "nom_client": [f"Client {i}" for i in range(n)],
        "date_effet": pd.to_datetime([f"{period}-0{i + 1}" for i in range(n)]),
        "produit": ["Temporaire Deces"] * n,
        "agence": ["Tunis Centre"] * n,
        "prime_totale": [1000.0 + i for i in range(n)],
        "capital_assure": [50000.0] * n,
        "prime_totale_source": ["reported"] * n,
        "source_file": [source] * n,
        "source_sheet": ["Feuil1"] * n,
        "source_row": list(range(5, 5 + n)),
        "ingested_at": ["2025-04-01T09:00:00+00:00"] * n,
        "row_hash": [f"{source}-{c}" for c in contracts],
    })


@pytest.fixture
def warehouse(config, tmp_path):
    return Warehouse(config, tmp_path / "warehouse")


def _touch(tmp_path, name):
    """A real file on disk, since the manifest hashes file contents."""
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"workbook bytes")
    return path


class TestAppend:
    def test_first_write_creates_the_fact_table(self, warehouse, tmp_path):
        source = _touch(tmp_path, "mars.xlsx")
        summary = warehouse.append([frame(str(source))], [source])
        assert summary["written"] == 2
        assert len(warehouse.read_fact()) == 2

    def test_second_month_appends_rather_than_replacing(self, warehouse, tmp_path):
        mars = _touch(tmp_path, "mars.xlsx")
        avril = _touch(tmp_path, "avril.xlsx")
        warehouse.append([frame(str(mars), "2025-03")], [mars])
        warehouse.append([frame(str(avril), "2025-04", ("C-003", "C-004"))], [avril])

        fact = warehouse.read_fact()
        assert len(fact) == 4
        assert set(fact["mois_reception"]) == {"2025-03", "2025-04"}


class TestIdempotence:
    def test_reingesting_the_same_file_does_not_duplicate(self, warehouse, tmp_path):
        source = _touch(tmp_path, "mars.xlsx")
        warehouse.append([frame(str(source))], [source])
        before = warehouse.read_fact()["row_hash"].tolist()

        warehouse.append([frame(str(source))], [source])
        after = warehouse.read_fact()

        assert len(after) == 2
        assert sorted(after["row_hash"]) == sorted(before)

    def test_corrected_file_replaces_its_own_rows(self, warehouse, tmp_path):
        """A bank resending a fixed March must not leave two Marches behind."""
        source = _touch(tmp_path, "mars.xlsx")
        warehouse.append([frame(str(source), contracts=("C-001", "C-002"))], [source])

        corrected = frame(str(source), contracts=("C-001", "C-002", "C-009"))
        corrected.loc[0, "prime_totale"] = 7777.0
        summary = warehouse.append([corrected], [source])

        fact = warehouse.read_fact()
        assert summary["replaced"] == 2
        assert len(fact) == 3
        assert fact.loc[fact["num_contrat"] == "C-001", "prime_totale"].iloc[0] == 7777.0

    def test_unchanged_file_is_reported_as_such(self, warehouse, tmp_path):
        source = _touch(tmp_path, "mars.xlsx")
        assert warehouse.manifest.status(source) == "new"
        warehouse.append([frame(str(source))], [source])
        assert warehouse.manifest.status(source) == "unchanged"

    def test_edited_file_is_detected_by_content_hash(self, warehouse, tmp_path):
        source = _touch(tmp_path, "mars.xlsx")
        warehouse.append([frame(str(source))], [source])
        source.write_bytes(b"different bytes")
        assert warehouse.manifest.status(source) == "changed"


class TestSchemaContract:
    def test_missing_column_is_refused(self, warehouse, tmp_path):
        """The failure that silently breaks every dashboard weeks later."""
        source = _touch(tmp_path, "mars.xlsx")
        warehouse.append([frame(str(source))], [source])

        narrower = frame(str(source)).drop(columns=["capital_assure"])
        with pytest.raises(SchemaContractError, match="capital_assure"):
            warehouse.append([narrower], [source])

    def test_unexpected_column_is_refused(self, warehouse, tmp_path):
        source = _touch(tmp_path, "mars.xlsx")
        warehouse.append([frame(str(source))], [source])

        wider = frame(str(source))
        wider["surprise"] = 1
        with pytest.raises(SchemaContractError, match="surprise"):
            warehouse.append([wider], [source])

    def test_the_error_says_what_to_do(self, warehouse, tmp_path):
        source = _touch(tmp_path, "mars.xlsx")
        narrower = frame(str(source)).drop(columns=["agence"])
        with pytest.raises(SchemaContractError, match="schema.yaml"):
            warehouse.append([narrower], [source])


class TestDateDimension:
    def test_calendar_is_contiguous(self):
        """Gaps silently break Power BI time intelligence."""
        fact = pd.DataFrame({
            "date_effet": pd.to_datetime(["2025-01-15", "2025-03-20"]),
            "mois_reception": ["2025-01", "2025-03"],
        })
        dimension = build_date_dimension(fact)
        assert len(dimension) == 90                       # Jan 1 to Mar 31
        assert dimension["date"].diff().dropna().eq(pd.Timedelta(days=1)).all()

    def test_french_month_labels(self):
        fact = pd.DataFrame({
            "date_effet": pd.to_datetime(["2025-02-15"]),
            "mois_reception": ["2025-02"],
        })
        assert "février" in set(build_date_dimension(fact)["nom_mois"])

    def test_falls_back_to_reception_month_without_dates(self):
        fact = pd.DataFrame({"mois_reception": ["2025-05"]})
        assert not build_date_dimension(fact).empty


def test_dimensions_and_bank_extracts_are_written(warehouse, tmp_path):
    source = _touch(tmp_path, "mars.xlsx")
    warehouse.append([frame(str(source))], [source])
    root = warehouse.root
    assert (root / "dim_date.parquet").exists()
    assert (root / "dim_banque.parquet").exists()
    assert (root / "par_banque" / "BNA.parquet").exists()
    assert (root / "par_banque" / "BNA.xlsx").exists()
