"""Validation checks, driven by deliberately corrupted data.

The clean corpus raises no flags, which is correct but proves nothing. Each check here
is given the exact defect it exists to catch.
"""

import pandas as pd
import pytest

from cardif.validate import (
    check_declaration_lag, check_duplicates, check_period_agreement,
    check_premium_reconciliation, check_required, reconciliation_table, validate,
)


def frame(**overrides) -> pd.DataFrame:
    """A small, valid consolidated frame, before any defect is injected."""
    base = {
        "banque": ["CNEP", "CNEP", "CNEP"],
        "mois_reception": ["2025-03", "2025-03", "2025-03"],
        "num_contrat": ["C-001", "C-002", "C-003"],
        "date_effet": pd.to_datetime(["2025-03-04", "2025-03-11", "2025-03-19"]),
        "prime_nette": [1000.0, 2000.0, 1500.0],
        "frais": [100.0, 200.0, 150.0],
        "prime_totale": [1100.0, 2200.0, 1650.0],
        "prime_totale_source": ["reported"] * 3,
        "capital_assure": [50000.0, 80000.0, 60000.0],
        "source_file": ["CNEP 2025/mars.xlsx"] * 3,
        "source_sheet": ["Feuil1"] * 3,
        "source_row": [5, 6, 7],
        "ingested_at": ["2025-04-01T09:00:00+00:00"] * 3,
        "row_hash": ["h1", "h2", "h3"],
    }
    base.update(overrides)
    return pd.DataFrame(base)


def test_clean_frame_raises_nothing(config):
    assert validate(frame(), config, "ade_immobilier").flags == []


class TestRequiredFields:
    def test_missing_required_value_is_an_error(self, config):
        bad = frame(prime_totale=[1100.0, None, 1650.0])
        flags = check_required(bad, config, "ade_immobilier")
        assert len(flags) == 1
        assert flags[0].severity == "error"
        assert flags[0].field == "prime_totale"
        # The flag must point back to the exact source cell.
        assert flags[0].source_row == 6
        assert flags[0].source_file.endswith("mars.xlsx")


class TestPremiumReconciliation:
    def test_breakdown_that_does_not_add_up_is_flagged(self, config):
        bad = frame(prime_totale=[1100.0, 9999.0, 1650.0])
        flags = check_premium_reconciliation(bad, config)
        assert len(flags) == 1
        assert flags[0].code == "premium_mismatch"
        assert "9999.00" in flags[0].message

    def test_rounding_within_tolerance_is_accepted(self, config):
        ok = frame(prime_totale=[1100.005, 2200.0, 1650.0])
        assert check_premium_reconciliation(ok, config) == []

    def test_derived_totals_are_not_checked_against_themselves(self, config):
        # A derived total is the sum by construction; checking it would be circular.
        derived = frame(
            prime_totale=[1100.0, 2200.0, 1650.0],
            prime_totale_source=["derived"] * 3,
        )
        assert check_premium_reconciliation(derived, config) == []


class TestPeriodAgreement:
    def test_file_named_for_the_wrong_month_is_caught(self, config):
        """A file named "Mars" full of April business would silently corrupt every
        month-over-month figure downstream."""
        mislabelled = frame(
            date_effet=pd.to_datetime(["2025-04-04", "2025-04-11", "2025-04-19"])
        )
        flags = check_period_agreement(mislabelled, config)
        assert len(flags) == 1
        assert flags[0].severity == "error"
        assert "2025-04" in flags[0].message

    def test_a_few_late_declarations_do_not_trip_it(self, config):
        mostly_right = frame(
            date_effet=pd.to_datetime(["2025-03-04", "2025-03-11", "2025-01-19"])
        )
        assert check_period_agreement(mostly_right, config) == []


class TestDeclarationLag:
    def test_long_lag_is_flagged_but_the_row_survives(self, config):
        """This is the reason date_effet and mois_reception are separate columns."""
        late = frame(
            date_effet=pd.to_datetime(["2025-03-04", "2025-03-11", "2024-06-19"])
        )
        flags = check_declaration_lag(late, config)
        assert len(flags) == 1
        assert flags[0].code == "late_declaration"
        assert flags[0].severity == "warning"      # a warning, never a deletion

    def test_normal_lag_is_not_flagged(self, config):
        normal = frame(
            date_effet=pd.to_datetime(["2025-03-04", "2025-02-11", "2025-01-19"])
        )
        assert check_declaration_lag(normal, config) == []


class TestDuplicates:
    def test_repeat_within_one_file_is_an_error(self):
        dup = frame(
            num_contrat=["C-001", "C-001", "C-003"],
            date_effet=pd.to_datetime(["2025-03-04", "2025-03-04", "2025-03-19"]),
            prime_totale=[1100.0, 1100.0, 1650.0],
            prime_nette=[1000.0, 1000.0, 1500.0],
            frais=[100.0, 100.0, 150.0],
        )
        flags = [f for f in check_duplicates(dup) if f.code == "duplicate_in_file"]
        assert len(flags) == 1
        assert flags[0].severity == "error"

    def test_same_contract_in_two_files_is_only_informational(self):
        # A renewal or a resent correction is legitimate; the user decides.
        across = frame(
            source_file=["CNEP 2025/mars.xlsx", "CNEP 2025/avril.xlsx", "CNEP 2025/mars.xlsx"],
            num_contrat=["C-001", "C-001", "C-003"],
        )
        flags = [f for f in check_duplicates(across) if f.code == "duplicate_across_files"]
        assert len(flags) == 1
        assert flags[0].severity == "info"


class TestReconciliationTable:
    def test_totals_per_bank_and_month(self):
        table = reconciliation_table(frame())
        assert len(table) == 1
        row = table.iloc[0]
        assert row["banque"] == "CNEP"
        assert row["mois_reception"] == "2025-03"
        assert row["lignes"] == 3
        assert row["prime_totale_total"] == pytest.approx(4950.0)

    def test_counts_derived_premiums_separately(self):
        mixed = frame(prime_totale_source=["reported", "derived", "derived"])
        assert reconciliation_table(mixed).iloc[0]["primes_calculees"] == 2


def test_flagged_rows_are_never_removed(config):
    """The contract the whole design rests on: flags mark, they do not delete."""
    bad = frame(prime_totale=[1100.0, 9999.0, None])
    report = validate(bad, config, "ade_immobilier")
    assert report.errors > 0
    assert len(bad) == 3
