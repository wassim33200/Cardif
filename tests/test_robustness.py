"""Hostile inputs.

The generated corpus proves the pipeline handles files that are messy the way banks are
messy. This file proves it handles files that are broken the way software breaks: not
workbooks at all, empty, truncated mid-copy, formulas with no saved result, a month
delivered twice, headers that collide.

The standard for all of these is the same: **fail loudly and specifically, never
silently and never with a traceback.** A file that cannot be read must produce an
explanation a person can act on, and must not stop the other files from being processed.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest
from openpyxl import Workbook, load_workbook

import gen_hostile
from cardif.config import load_config
from cardif.consolidate import process_file
from cardif.detect import extract_table
from cardif.filemeta import read_meta, scan
from cardif.mapping import Mapper
from cardif.pipeline import commit, run
from cardif.store import Warehouse
from cardif.validate import check_period_coverage, validate


@pytest.fixture(scope="module")
def hostile(tmp_path_factory) -> dict[str, Path]:
    return gen_hostile.build_all(tmp_path_factory.mktemp("hostile"))


def _process(path: Path, config, mapper, profile="detaille"):
    return process_file(read_meta(path, config.banks), config, mapper, profile)


class TestUnreadableFiles:
    """None of these may raise; each must explain itself."""

    @pytest.mark.parametrize(
        "case",
        ["not_a_workbook", "zero_bytes", "csv_with_xlsx_extension", "truncated_zip"],
    )
    def test_rejected_with_an_explanation(self, hostile, config, mapper, case):
        result = _process(hostile[case], config, mapper)
        assert not result.ok
        assert result.error
        assert "workbook" in result.error.lower()

    def test_a_broken_file_does_not_stop_the_others(self, hostile, config):
        """One corrupt file in a folder must not cost you the whole month."""
        root = hostile["valid_baseline"].parent.parent
        outcome = run(root, config, "detaille", use_model=False)
        assert outcome.ok_results, "no file survived a folder containing broken ones"
        assert outcome.skipped
        assert outcome.frame is not None and not outcome.frame.empty


class TestEmptyOrUnusable:
    @pytest.mark.parametrize(
        "case",
        ["empty_workbook", "header_but_no_data", "only_scratch_numbers", "blank_rows_only"],
    )
    def test_reported_not_crashed(self, hostile, config, mapper, case):
        result = _process(hostile[case], config, mapper)
        assert not result.ok
        assert result.error


class TestUncachedFormulas:
    """The failure that would silently lose a premium column."""

    def test_file_is_refused_rather_than_consolidated_without_the_column(
        self, hostile, config, mapper
    ):
        result = _process(hostile["uncached_formulas"], config, mapper)
        assert not result.ok
        assert "formul" in result.error.lower()

    def test_the_error_names_the_affected_column_and_the_remedy(
        self, hostile, config, mapper
    ):
        result = _process(hostile["uncached_formulas"], config, mapper)
        assert "Prime totale" in result.error
        assert "Excel" in result.error

    def test_a_file_with_cached_results_is_processed_normally(
        self, tmp_path, config, mapper
    ):
        """Formulas are fine when Excel has stored their results, which is the norm."""
        path = tmp_path / "BNA 2025" / "Ventes_Mars_2025.xlsx"
        path.parent.mkdir(parents=True)
        gen_hostile.valid_baseline(path)
        result = _process(path, config, mapper)
        assert result.ok
        assert result.frame["prime_totale"].notna().all()


class TestStructurallyAwkward:
    def test_table_not_starting_at_column_a(self, hostile, config, mapper):
        result = _process(hostile["table_offset_from_origin"], config, mapper)
        assert result.ok
        assert result.n_rows == 6
        assert result.frame["num_contrat"].notna().all()

    def test_colliding_headers_go_to_a_human(self, hostile, config, mapper):
        """Never let the second column silently overwrite the first."""
        result = _process(hostile["colliding_headers"], config, mapper)
        assert result.ok
        claims = [m for m in result.mappings if m.canonical == "prime_totale"]
        assert len(claims) == 1
        assert any(m.canonical is None for m in result.mappings)

    def test_orphan_rows_below_a_gap_are_excluded(self, tmp_path, config, mapper):
        path = tmp_path / "BNA 2025" / "Ventes_Mars_2025.xlsx"
        path.parent.mkdir(parents=True)
        gen_hostile.data_resuming_after_a_gap(path)
        result = _process(path, config, mapper)
        assert result.n_rows == 6
        assert not result.frame["num_contrat"].str.startswith("ORPHAN").any()

    def test_merged_cells_down_a_data_column_are_filled(self, tmp_path, config, mapper):
        path = tmp_path / "BNA 2025" / "Ventes_Mars_2025.xlsx"
        path.parent.mkdir(parents=True)
        gen_hostile.merged_cells_across_data(path)
        result = _process(path, config, mapper)
        assert result.n_rows == 6
        assert (result.frame["agence"] == "Tunis Centre").all()

    def test_sales_sheet_is_chosen_over_a_parameters_tab(self, tmp_path, config, mapper):
        path = tmp_path / "BNA 2025" / "Ventes_Mars_2025.xlsx"
        path.parent.mkdir(parents=True)
        gen_hostile.junk_sheet_before_data(path)
        result = _process(path, config, mapper)
        assert result.sheet == "Ventes"
        assert result.n_rows == 12

    def test_single_data_row(self, tmp_path, config, mapper):
        path = tmp_path / "BNA 2025" / "Ventes_Mars_2025.xlsx"
        path.parent.mkdir(parents=True)
        gen_hostile.one_data_row(path)
        result = _process(path, config, mapper)
        assert result.ok and result.n_rows == 1

    def test_unicode_and_overlong_headers(self, tmp_path, config, mapper):
        path = tmp_path / "BNA 2025" / "Ventes_Mars_2025.xlsx"
        path.parent.mkdir(parents=True)
        gen_hostile.unicode_and_long_headers(path)
        result = _process(path, config, mapper)
        assert result.ok
        assert result.frame["num_contrat"].notna().all()

    def test_two_hundred_columns(self, tmp_path, config, mapper):
        path = tmp_path / "BNA 2025" / "Ventes_Mars_2025.xlsx"
        path.parent.mkdir(parents=True)
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.append(
            ["N° Contrat", "Prime totale", "Date d'effet"] + [f"col{i}" for i in range(200)]
        )
        for i in range(10):
            worksheet.append([f"C-{i}", 100.0, dt.date(2025, 3, 5)] + [None] * 200)
        workbook.save(path)
        result = _process(path, config, mapper)
        assert result.ok and result.n_rows == 10


class TestValueEdges:
    def test_extreme_values_survive_without_corruption(self, tmp_path, config, mapper):
        path = tmp_path / "BNA 2025" / "Ventes_Mars_2025.xlsx"
        path.parent.mkdir(parents=True)
        gen_hostile.extreme_values(path)
        result = _process(path, config, mapper)
        assert result.ok and result.n_rows == 5

        premiums = result.frame["prime_totale"].tolist()
        assert 1e15 in premiums          # not overflowed
        assert 0.001 in premiums         # not rounded away
        assert -500.0 in premiums        # negatives preserved, flagged elsewhere
        # The impossible date becomes empty rather than a wrong date.
        assert result.frame["date_effet"].isna().sum() == 1

    def test_mixed_number_formats_in_one_column(self, tmp_path, config, mapper):
        path = tmp_path / "BNA 2025" / "Ventes_Mars_2025.xlsx"
        path.parent.mkdir(parents=True)
        gen_hostile.numbers_as_text_mixed(path)
        result = _process(path, config, mapper)
        assert result.ok
        assert result.frame["prime_totale"].notna().all()
        assert result.frame["prime_totale"].max() == pytest.approx(2500.0)

    def test_identifiers_mangled_into_floats_are_recovered(self, tmp_path, config, mapper):
        path = tmp_path / "BNA 2025" / "Ventes_Mars_2025.xlsx"
        path.parent.mkdir(parents=True)
        gen_hostile.identifiers_mangled_by_excel(path)
        result = _process(path, config, mapper)
        contracts = result.frame["num_contrat"].tolist()
        assert "20250001" in contracts
        assert not any("e+" in c or c.endswith(".0") for c in contracts)


class TestMonthDeliveredTwice:
    """The quietest way to get a wrong number on a dashboard."""

    @pytest.fixture
    def two_files_one_month(self, tmp_path):
        folder = tmp_path / "BNA 2025"
        folder.mkdir(parents=True)
        gen_hostile.valid_baseline(folder / "Ventes_Mars_2025.xlsx", rows=10, month=3)
        gen_hostile.valid_baseline(folder / "Ventes_Mars_2025_corrige.xlsx", rows=10, month=3)
        return tmp_path

    def test_raises_an_error_not_a_footnote(self, two_files_one_month, config):
        outcome = run(two_files_one_month, config, "detaille", use_model=False)
        coverage = [
            f for f in outcome.validation.flags if f.code == "period_covered_twice"
        ]
        assert len(coverage) == 1
        assert coverage[0].severity == "error"
        assert "double" in coverage[0].message.lower()
        assert "Ventes_Mars_2025.xlsx" in coverage[0].message

    def test_stated_once_per_month_not_once_per_contract(self, two_files_one_month, config):
        """Dozens of identical lines is noise, and noise gets ignored."""
        outcome = run(two_files_one_month, config, "detaille", use_model=False)
        assert len(outcome.validation.flags) < 5

    def test_commit_actually_refuses_to_write(self, two_files_one_month, tmp_path):
        """The error must block the write, not merely be recorded somewhere."""
        from cardif.cli import main

        config_dir = Path(__file__).resolve().parent.parent / "config"
        warehouse = tmp_path / "wh"
        code = main([
            "commit", str(two_files_one_month),
            "--config", str(config_dir),
            "--warehouse", str(warehouse),
            "--profile", "detaille",
            "--no-model",
        ])
        assert code != 0
        assert not (warehouse / "fact_ventes.parquet").exists()

    def test_allow_errors_lets_it_through_deliberately(self, two_files_one_month, tmp_path):
        """The override exists, but it has to be asked for."""
        from cardif.cli import main

        config_dir = Path(__file__).resolve().parent.parent / "config"
        warehouse = tmp_path / "wh"
        code = main([
            "commit", str(two_files_one_month),
            "--config", str(config_dir),
            "--warehouse", str(warehouse),
            "--profile", "detaille",
            "--no-model", "--allow-errors",
        ])
        assert code == 0
        assert (warehouse / "fact_ventes.parquet").exists()

    def test_one_file_per_month_raises_nothing(self, tmp_path, config):
        folder = tmp_path / "BNA 2025"
        folder.mkdir(parents=True)
        gen_hostile.valid_baseline(folder / "Ventes_Mars_2025.xlsx", month=3)
        gen_hostile.valid_baseline(folder / "Ventes_Avril_2025.xlsx", month=4)
        outcome = run(tmp_path, config, "detaille", use_model=False)
        assert check_period_coverage(outcome.frame) == []


class TestConfigValidation:
    """A bad config must fail at load, not three steps into a run."""

    @pytest.fixture
    def config_dir(self, tmp_path):
        import shutil

        source = Path(__file__).resolve().parent.parent / "config"
        target = tmp_path / "config"
        target.mkdir()
        for name in ("schema.yaml", "banks.yaml", "settings.yaml", "aliases.yaml"):
            shutil.copy(source / name, target / name)
        return target

    def _edit(self, config_dir: Path, name: str, old: str, new: str) -> None:
        path = config_dir / name
        text = path.read_text(encoding="utf-8")
        assert old in text
        path.write_text(text.replace(old, new), encoding="utf-8")

    def test_unknown_export_profile(self, config_dir):
        self._edit(config_dir, "settings.yaml",
                   'profile: "powerbi_2025"', 'profile: "nexiste_pas"')
        with pytest.raises(Exception, match="nexiste_pas"):
            load_config(config_dir)

    def test_negative_premium_tolerance(self, config_dir):
        self._edit(config_dir, "settings.yaml",
                   "premium_tolerance: 0.01", "premium_tolerance: -5")
        with pytest.raises(Exception):
            load_config(config_dir)

    def test_suggest_floor_above_auto_accept(self, config_dir):
        self._edit(config_dir, "settings.yaml",
                   "fuzzy_suggest_floor: 70", "fuzzy_suggest_floor: 99")
        with pytest.raises(Exception, match="fuzzy_suggest_floor"):
            load_config(config_dir)

    def test_profile_naming_an_unknown_field(self, config_dir):
        self._edit(config_dir, "schema.yaml",
                   "prime_totale, capital_assure]", "prime_totale, chose_inexistante]")
        with pytest.raises(Exception, match="chose_inexistante"):
            load_config(config_dir)

    def test_missing_config_file_says_which(self, config_dir):
        (config_dir / "banks.yaml").unlink()
        with pytest.raises(FileNotFoundError, match="banks.yaml"):
            load_config(config_dir)

    def test_malformed_yaml_is_not_swallowed(self, config_dir):
        (config_dir / "aliases.yaml").write_text("global: {{{ broken", encoding="utf-8")
        with pytest.raises(Exception):
            load_config(config_dir)


class TestFilenameEdges:
    @pytest.mark.parametrize(
        "filename",
        [
            "Ventes.xlsx",                    # no period at all
            "rapport final v2.xlsx",
            "mars avril 2025.xlsx",           # ambiguous: two months
            "Ventes 2025.xlsx",               # year but no month
        ],
    )
    def test_unreadable_filenames_are_reported_not_guessed(
        self, tmp_path, config, mapper, filename
    ):
        folder = tmp_path / "BNA 2025"
        folder.mkdir(parents=True)
        path = folder / filename
        gen_hostile.valid_baseline(path)
        result = _process(path, config, mapper)
        assert not result.ok
        assert "period" in result.error.lower()

    def test_unknown_bank_folder_is_reported(self, tmp_path, config, mapper):
        folder = tmp_path / "BanqueInconnue 2025"
        folder.mkdir(parents=True)
        path = folder / "Ventes_Mars_2025.xlsx"
        gen_hostile.valid_baseline(path)
        result = _process(path, config, mapper)
        assert not result.ok
        assert "bank" in result.error.lower()

    def test_excel_lock_files_are_skipped(self, tmp_path, config):
        folder = tmp_path / "BNA 2025"
        folder.mkdir(parents=True)
        gen_hostile.valid_baseline(folder / "Ventes_Mars_2025.xlsx")
        (folder / "~$Ventes_Mars_2025.xlsx").write_bytes(b"lock")
        assert len(scan(tmp_path, config.banks)) == 1


class TestWarehouseEdges:
    def test_committing_nothing_is_harmless(self, tmp_path, config):
        from cardif.pipeline import RunResult

        summary = commit(RunResult(profile_name="detaille"), config, tmp_path / "wh")
        assert summary["written"] == 0

    def test_reading_an_absent_warehouse_returns_empty(self, tmp_path, config):
        assert Warehouse(config, tmp_path / "nope").read_fact().empty

    def test_manifest_survives_a_file_that_moved(self, tmp_path, config):
        """A path in the manifest that no longer exists must not crash the scan."""
        warehouse = Warehouse(config, tmp_path / "wh", "detaille")
        folder = tmp_path / "BNA 2025"
        folder.mkdir(parents=True)
        path = folder / "Ventes_Mars_2025.xlsx"
        gen_hostile.valid_baseline(path)

        outcome = run(tmp_path, config, "detaille", use_model=False)
        commit(outcome, config, tmp_path / "wh")

        path.unlink()
        reopened = Warehouse(config, tmp_path / "wh", "detaille")
        assert len(reopened.read_fact()) > 0
        assert reopened.pending([])["new"] == []


class TestWarehouseDurability:
    """An interrupted write must not destroy the history.

    This is the only failure in the system with no way back: a parse error costs one
    file, but a truncated fact table costs every month ever ingested.
    """

    @pytest.fixture
    def seeded(self, tmp_path, config):
        folder = tmp_path / "data" / "BNA 2025"
        folder.mkdir(parents=True)
        gen_hostile.valid_baseline(folder / "Ventes_Mars_2025.xlsx", rows=20, month=3)
        warehouse_dir = tmp_path / "wh"
        commit(run(tmp_path / "data", config, "detaille", use_model=False),
               config, warehouse_dir)
        gen_hostile.valid_baseline(folder / "Ventes_Avril_2025.xlsx", rows=15, month=4)
        commit(run(tmp_path / "data", config, "detaille", use_model=False),
               config, warehouse_dir)
        return warehouse_dir

    def test_a_backup_is_kept(self, seeded):
        assert (seeded / "fact_ventes.parquet.bak").exists()

    def test_truncated_fact_table_is_reported_not_raised_raw(self, seeded, config):
        from cardif.store import WarehouseCorrupt

        fact = seeded / "fact_ventes.parquet"
        data = fact.read_bytes()
        fact.write_bytes(data[: len(data) // 2])

        with pytest.raises(WarehouseCorrupt) as caught:
            Warehouse(config, seeded, "detaille").read_fact()
        message = str(caught.value)
        assert "backup" in message
        assert "fact_ventes.parquet.bak" in message

    def test_the_backup_actually_restores(self, seeded, config):
        import shutil

        fact = seeded / "fact_ventes.parquet"
        before = len(Warehouse(config, seeded, "detaille").read_fact())
        fact.write_bytes(b"ruined")
        shutil.copy(seeded / "fact_ventes.parquet.bak", fact)
        recovered = len(Warehouse(config, seeded, "detaille").read_fact())
        assert 0 < recovered <= before

    def test_no_backup_still_gives_a_route_forward(self, tmp_path, config):
        from cardif.store import WarehouseCorrupt

        warehouse_dir = tmp_path / "wh"
        warehouse_dir.mkdir()
        (warehouse_dir / "fact_ventes.parquet").write_bytes(b"not parquet")
        with pytest.raises(WarehouseCorrupt, match="Re-ingest"):
            Warehouse(config, warehouse_dir, "detaille").read_fact()

    def test_no_temporary_files_are_left_behind(self, seeded):
        leftovers = [p.name for p in seeded.rglob("*.tmp")]
        assert leftovers == []

    def test_cli_reports_corruption_instead_of_a_traceback(self, tmp_path, capsys):
        from cardif.cli import main

        warehouse_dir = tmp_path / "wh"
        warehouse_dir.mkdir()
        (warehouse_dir / "fact_ventes.parquet").write_bytes(b"not parquet")
        config_dir = Path(__file__).resolve().parent.parent / "config"

        code = main(["status", "--config", str(config_dir), "--warehouse", str(warehouse_dir)])
        assert code == 4
        assert "unreadable" in capsys.readouterr().out


class TestBankExtracts:
    def test_only_the_banks_in_this_commit_are_rewritten(self, tmp_path, config):
        """Rewriting every bank's whole history on every commit does not scale."""
        data = tmp_path / "data"
        for bank in ("BNA", "BIAT"):
            folder = data / f"{bank} 2025"
            folder.mkdir(parents=True)
            gen_hostile.valid_baseline(folder / "Ventes_Mars_2025.xlsx", rows=10, month=3)

        warehouse_dir = tmp_path / "wh"
        commit(run(data, config, "detaille", use_model=False), config, warehouse_dir)

        biat = warehouse_dir / "par_banque" / "BIAT.parquet"
        untouched_before = biat.stat().st_mtime_ns

        gen_hostile.valid_baseline(
            data / "BNA 2025" / "Ventes_Avril_2025.xlsx", rows=10, month=4
        )
        warehouse = Warehouse(config, warehouse_dir, "detaille")
        todo = warehouse.pending([m.path for m in scan(data, config.banks)])
        outcome = run(data, config, "detaille", use_model=False,
                      only=todo["new"] + todo["changed"])
        commit(outcome, config, warehouse_dir)

        assert biat.stat().st_mtime_ns == untouched_before
        assert (warehouse_dir / "par_banque" / "BNA.parquet").exists()

    def test_an_extract_too_large_for_excel_is_reported(self, tmp_path, config):
        """Silence would leave a stale .xlsx looking current."""
        config.settings.warehouse.xlsx_row_limit = 5
        data = tmp_path / "data" / "BNA 2025"
        data.mkdir(parents=True)
        gen_hostile.valid_baseline(data / "Ventes_Mars_2025.xlsx", rows=20, month=3)

        warehouse_dir = tmp_path / "wh"
        summary = commit(
            run(tmp_path / "data", config, "detaille", use_model=False),
            config, warehouse_dir,
        )
        assert "skipped_xlsx" in summary
        assert "exceeds the Excel limit" in summary["skipped_xlsx"][0]
        assert not (warehouse_dir / "par_banque" / "BNA.xlsx").exists()
        # The full data is still available in the format that has no such limit.
        assert (warehouse_dir / "par_banque" / "BNA.parquet").exists()


class TestScaling:
    """Checks that must stay linear: a portfolio has thousands of renewals."""

    def _repeated(self, contracts: int) -> pd.DataFrame:
        rows = []
        for name in ("mars.xlsx", "mars_corrige.xlsx"):
            for i in range(contracts):
                rows.append({
                    "banque": "BNA", "mois_reception": "2025-03",
                    "num_contrat": f"C-{i:06d}",
                    "date_effet": pd.Timestamp("2025-03-05"),
                    "prime_totale": 100.0,
                    "source_file": f"BNA 2025/{name}",
                    "source_row": i + 2, "row_hash": f"{name}-{i}",
                })
        return pd.DataFrame(rows)

    def test_duplicates_across_files_reported_once_per_pair(self):
        from cardif.validate import check_duplicates

        flags = [f for f in check_duplicates(self._repeated(2000))
                 if f.code == "duplicate_across_files"]
        assert len(flags) == 1
        assert "2000 contract(s)" in flags[0].message

    def test_duplicate_check_scales_linearly(self):
        """Scanning the table once per repeated contract makes this quadratic."""
        import time

        from cardif.validate import check_duplicates

        start = time.perf_counter()
        check_duplicates(self._repeated(500))
        small = time.perf_counter() - start

        start = time.perf_counter()
        check_duplicates(self._repeated(4000))
        large = time.perf_counter() - start

        # 8x the rows must not cost anything like 64x the time.
        assert large < max(small * 24, 3.0), f"{small:.3f}s -> {large:.3f}s"

    def test_validation_of_clean_data_does_not_walk_every_row(self, config):
        """The common case is no flags at all, and it must cost close to nothing."""
        import time

        import numpy as np

        n = 40_000
        rng = np.random.default_rng(0)
        frame = pd.DataFrame({
            "banque": ["BNA"] * n, "mois_reception": ["2025-03"] * n,
            "num_contrat": [f"C-{i:07d}" for i in range(n)],
            "date_effet": pd.to_datetime(["2025-03-05"] * n),
            "prime_nette": rng.uniform(80, 4000, n).round(2),
            "frais": rng.uniform(5, 300, n).round(2),
            "capital_assure": rng.uniform(1e4, 5e5, n).round(2),
            "source_file": ["BNA 2025/mars.xlsx"] * n, "source_sheet": ["F"] * n,
            "source_row": range(2, n + 2), "ingested_at": ["x"] * n,
            "row_hash": [f"h{i}" for i in range(n)],
        })
        frame["prime_totale"] = (frame.prime_nette + frame.frais).round(2)
        frame["prime_totale_source"] = "reported"

        start = time.perf_counter()
        report = validate(frame, config, "detaille")
        elapsed = time.perf_counter() - start

        assert report.flags == []
        assert elapsed < 6.0, f"validating {n} clean rows took {elapsed:.1f}s"

    def test_broken_rows_are_still_all_flagged(self, config):
        """Speed must not come from checking fewer rows."""
        n = 2_000
        frame = pd.DataFrame({
            "banque": ["BNA"] * n, "mois_reception": ["2025-03"] * n,
            "num_contrat": [f"C-{i:05d}" for i in range(n)],
            "date_effet": pd.to_datetime(["2025-03-05"] * n),
            "prime_nette": [100.0] * n, "frais": [10.0] * n,
            "prime_totale": [110.0] * n, "prime_totale_source": ["reported"] * n,
            "source_file": ["BNA 2025/mars.xlsx"] * n, "source_sheet": ["F"] * n,
            "source_row": range(2, n + 2), "ingested_at": ["x"] * n,
            "row_hash": [f"h{i}" for i in range(n)],
        })
        frame.loc[frame.index[:137], "prime_totale"] = 9999.99
        report = validate(frame, config, "detaille")
        mismatches = [f for f in report.flags if f.code == "premium_mismatch"]
        assert len(mismatches) == 137
