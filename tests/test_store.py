"""Warehouse behaviour: one table per product, append, idempotence, contracts.

These are the properties that decide whether a Power BI report keeps working when next
month's files arrive -- and, now that each product has its own table, whether a change
to one product can disturb another.
"""

import pandas as pd
import pytest

from cardif.store import SchemaContractError, Warehouse, build_date_dimension, nom_table

PRODUIT = "ade_immobilier"


@pytest.fixture
def warehouse(config, tmp_path):
    return Warehouse(config, tmp_path / "warehouse", PRODUIT)


def _touch(tmp_path, name):
    """A real file on disk, since the manifest hashes file contents."""
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"workbook bytes")
    return path


class TestPerProductTables:
    def test_first_write_creates_the_product_table(self, warehouse, tmp_path, make_frame):
        source = _touch(tmp_path, "ADE_Immobilier_Mars_2025.xlsx")
        summary = warehouse.append([make_frame(source=str(source))], [source], [PRODUIT])
        assert summary["written"] == 2
        assert len(warehouse.read_fact(PRODUIT)) == 2
        assert warehouse.fact_path(PRODUIT).name == f"{nom_table(PRODUIT)}.parquet"

    def test_each_product_gets_its_own_table(self, warehouse, tmp_path, make_frame):
        """An ADE file and a SAHTI file have almost no columns in common."""
        ade = _touch(tmp_path, "ADE_Immobilier_Mars_2025.xlsx")
        sahti = _touch(tmp_path, "SAHTI_Mars_2025.xlsx")
        warehouse.append(
            [make_frame("ade_immobilier", source=str(ade)),
             make_frame("sahti", source=str(sahti))],
            [ade, sahti],
            ["ade_immobilier", "sahti"],
        )
        assert warehouse.produits_presents() == ["ade_immobilier", "sahti"]

        colonnes_ade = set(warehouse.read_fact("ade_immobilier").columns)
        colonnes_sahti = set(warehouse.read_fact("sahti").columns)
        assert "capital_restant_du" in colonnes_ade
        assert "capital_restant_du" not in colonnes_sahti
        assert "nb_assures" in colonnes_sahti
        assert "nb_assures" not in colonnes_ade

    def test_writing_one_product_leaves_the_others_untouched(
        self, warehouse, tmp_path, make_frame
    ):
        ade = _touch(tmp_path, "ADE_Immobilier_Mars_2025.xlsx")
        warehouse.append([make_frame("ade_immobilier", source=str(ade))], [ade],
                         ["ade_immobilier"])
        before = warehouse.fact_path("ade_immobilier").stat().st_mtime_ns

        sahti = _touch(tmp_path, "SAHTI_Mars_2025.xlsx")
        warehouse.append([make_frame("sahti", source=str(sahti))], [sahti], ["sahti"])
        assert warehouse.fact_path("ade_immobilier").stat().st_mtime_ns == before

    def test_reading_everything_stacks_the_tables(self, warehouse, tmp_path, make_frame):
        ade = _touch(tmp_path, "ADE_Immobilier_Mars_2025.xlsx")
        sahti = _touch(tmp_path, "SAHTI_Mars_2025.xlsx")
        warehouse.append(
            [make_frame("ade_immobilier", source=str(ade)),
             make_frame("sahti", source=str(sahti))],
            [ade, sahti], ["ade_immobilier", "sahti"],
        )
        tout = warehouse.read_fact()
        assert len(tout) == 4
        assert set(tout["produit_code"]) == {"ade_immobilier", "sahti"}


class TestAppend:
    def test_second_month_appends_rather_than_replacing(
        self, warehouse, tmp_path, make_frame
    ):
        mars = _touch(tmp_path, "ADE_Immobilier_Mars_2025.xlsx")
        avril = _touch(tmp_path, "ADE_Immobilier_Avril_2025.xlsx")
        warehouse.append([make_frame(source=str(mars), period="2025-03")], [mars],
                         [PRODUIT])
        warehouse.append(
            [make_frame(source=str(avril), period="2025-04",
                        contracts=["C-003", "C-004"])],
            [avril], [PRODUIT],
        )
        fact = warehouse.read_fact(PRODUIT)
        assert len(fact) == 4
        assert set(fact["mois_reception"]) == {"2025-03", "2025-04"}

    def test_committing_nothing_is_harmless(self, warehouse):
        assert warehouse.append([], [], [])["written"] == 0


class TestIdempotence:
    def test_reingesting_the_same_file_does_not_duplicate(
        self, warehouse, tmp_path, make_frame
    ):
        source = _touch(tmp_path, "ADE_Immobilier_Mars_2025.xlsx")
        warehouse.append([make_frame(source=str(source))], [source], [PRODUIT])
        before = warehouse.read_fact(PRODUIT)["row_hash"].tolist()

        warehouse.append([make_frame(source=str(source))], [source], [PRODUIT])
        after = warehouse.read_fact(PRODUIT)

        assert len(after) == 2
        assert sorted(after["row_hash"]) == sorted(before)

    def test_corrected_file_replaces_its_own_rows(self, warehouse, tmp_path, make_frame):
        """A bank resending a fixed March must not leave two Marches behind."""
        source = _touch(tmp_path, "ADE_Immobilier_Mars_2025.xlsx")
        warehouse.append(
            [make_frame(source=str(source), contracts=["C-001", "C-002"])],
            [source], [PRODUIT],
        )
        corrected = make_frame(source=str(source),
                               contracts=["C-001", "C-002", "C-009"])
        corrected.loc[0, "prime_totale"] = 7777.0
        summary = warehouse.append([corrected], [source], [PRODUIT])

        fact = warehouse.read_fact(PRODUIT)
        assert summary["replaced"] == 2
        assert len(fact) == 3
        assert fact.loc[fact["num_contrat"] == "C-001", "prime_totale"].iloc[0] == 7777.0

    def test_unchanged_file_is_reported_as_such(self, warehouse, tmp_path, make_frame):
        source = _touch(tmp_path, "ADE_Immobilier_Mars_2025.xlsx")
        assert warehouse.manifest.status(source) == "new"
        warehouse.append([make_frame(source=str(source))], [source], [PRODUIT])
        assert warehouse.manifest.status(source) == "unchanged"

    def test_edited_file_is_detected_by_content_hash(
        self, warehouse, tmp_path, make_frame
    ):
        source = _touch(tmp_path, "ADE_Immobilier_Mars_2025.xlsx")
        warehouse.append([make_frame(source=str(source))], [source], [PRODUIT])
        source.write_bytes(b"different bytes")
        assert warehouse.manifest.status(source) == "changed"

    def test_the_manifest_records_the_product(self, warehouse, tmp_path, make_frame):
        source = _touch(tmp_path, "SAHTI_Mars_2025.xlsx")
        warehouse.append([make_frame("sahti", source=str(source))], [source], ["sahti"])
        assert warehouse.manifest.files[str(source)].produit == "sahti"


class TestSchemaContract:
    def test_missing_column_is_refused(self, warehouse, tmp_path, make_frame):
        """The failure that silently breaks every dashboard weeks later."""
        source = _touch(tmp_path, "ADE_Immobilier_Mars_2025.xlsx")
        warehouse.append([make_frame(source=str(source))], [source], [PRODUIT])

        narrower = make_frame(source=str(source)).drop(columns=["capital_restant_du"])
        with pytest.raises(SchemaContractError, match="capital_restant_du"):
            warehouse.append([narrower], [source], [PRODUIT])

    def test_unexpected_column_is_refused(self, warehouse, tmp_path, make_frame):
        source = _touch(tmp_path, "ADE_Immobilier_Mars_2025.xlsx")
        warehouse.append([make_frame(source=str(source))], [source], [PRODUIT])

        wider = make_frame(source=str(source))
        wider["surprise"] = 1
        with pytest.raises(SchemaContractError, match="surprise"):
            warehouse.append([wider], [source], [PRODUIT])

    def test_the_error_names_the_product_and_the_remedy(
        self, warehouse, tmp_path, make_frame
    ):
        source = _touch(tmp_path, "ADE_Immobilier_Mars_2025.xlsx")
        narrower = make_frame(source=str(source)).drop(columns=["agence"])
        with pytest.raises(SchemaContractError) as caught:
            warehouse.append([narrower], [source], [PRODUIT])
        message = str(caught.value)
        assert "ade_immobilier" in message
        assert "produits.yaml" in message

    def test_one_product_contract_does_not_constrain_another(
        self, warehouse, tmp_path, make_frame
    ):
        """SAHTI has no CRD; that must not read as a broken ADE contract."""
        ade = _touch(tmp_path, "ADE_Immobilier_Mars_2025.xlsx")
        sahti = _touch(tmp_path, "SAHTI_Mars_2025.xlsx")
        warehouse.append([make_frame("ade_immobilier", source=str(ade))], [ade],
                         ["ade_immobilier"])
        warehouse.append([make_frame("sahti", source=str(sahti))], [sahti], ["sahti"])
        assert len(warehouse.read_fact("sahti")) == 2


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


def test_dimensions_and_bank_extracts_are_written(warehouse, tmp_path, make_frame):
    source = _touch(tmp_path, "ADE_Immobilier_Mars_2025.xlsx")
    warehouse.append([make_frame(source=str(source))], [source], [PRODUIT])
    root = warehouse.root
    assert (root / "dim_date.parquet").exists()
    assert (root / "dim_banque.parquet").exists()
    assert (root / "dim_produit.parquet").exists()
    # Extracts are per bank AND per product: a bank's ADE and SAHTI are separate files.
    assert (root / "par_banque" / f"CNEP_{PRODUIT}.parquet").exists()
    assert (root / "par_banque" / f"CNEP_{PRODUIT}.xlsx").exists()


def test_product_dimension_lists_every_product_from_the_catalogue(
    warehouse, tmp_path, make_frame, config
):
    """A Power BI slicer should list every product, not only those already sold."""
    source = _touch(tmp_path, "ADE_Immobilier_Mars_2025.xlsx")
    warehouse.append([make_frame(source=str(source))], [source], [PRODUIT])
    dimension = pd.read_parquet(warehouse.root / "dim_produit.parquet")
    assert len(dimension) == len(config.schema_.profiles)
    assert {"produit_code", "produit", "famille", "table"} <= set(dimension.columns)
