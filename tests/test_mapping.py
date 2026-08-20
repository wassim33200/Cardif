from openpyxl import load_workbook

from cardif.detect import extract_table
from cardif.mapping import Mapper, ColumnProfile, profile_column, verify_against_content


def _resolve_corpus(corpus, config, mapper):
    root, truth = corpus
    vocabulary = set(config.seed_aliases_from_schema())
    for entry in truth:
        workbook = load_workbook(entry["path"], data_only=True)
        table = extract_table(workbook[entry["sheet"]], vocabulary, config.settings.detection)
        workbook.close()
        results = mapper.resolve_table(
            table.headers, table.rows, entry["bank"], table.header_parts
        )
        yield entry, table, results


def test_deterministic_tiers_resolve_almost_everything(corpus, config, mapper):
    """No model, no human: the rules alone must carry the overwhelming majority."""
    total = resolved = 0
    for _, _, results in _resolve_corpus(corpus, config, mapper):
        for result in results:
            total += 1
            if result.canonical:
                resolved += 1
    assert resolved / total > 0.95


def test_nothing_is_mis_mapped(corpus, config, mapper):
    """A wrong mapping corrupts the database silently; there must be none."""
    wrong = []
    for entry, _, results in _resolve_corpus(corpus, config, mapper):
        expected = {header: canonical for canonical, header in entry["headers"].items()}
        if entry["two_row_header"]:
            expected.update({
                "Prime nette": "prime_nette",
                "Prime frais": "frais",
                "Prime totale": "prime_totale",
            })
        for result in results:
            want = expected.get(result.header)
            if want and result.canonical and result.canonical != want:
                wrong.append((entry["path"], result.header, result.canonical, want))
    assert wrong == []


def test_merged_header_leaf_resolves_frais(corpus, config, mapper):
    """"Prime frais" must map to frais, not lose a tie to prime_nette."""
    checked = 0
    for entry, _, results in _resolve_corpus(corpus, config, mapper):
        if not entry["two_row_header"]:
            continue
        by_header = {r.header: r for r in results}
        assert by_header["Prime frais"].canonical == "frais"
        assert by_header["Prime nette"].canonical == "prime_nette"
        checked += 1
    assert checked > 0


def test_duplicate_claims_are_not_silently_resolved(config, mapper):
    """Two columns claiming one field must go to a human, not let the last one win."""
    headers = ["Prime totale", "Montant prime"]
    rows = [[100.0, 100.0], [200.0, 200.0]]
    results = mapper.resolve_table(headers, rows, "BNA")
    claimed = [r for r in results if r.canonical == "prime_totale"]
    assert len(claimed) == 1
    loser = next(r for r in results if r.canonical is None)
    assert "duplicate claim" in loser.reason


def test_learned_alias_closes_an_unresolved_header(config, mapper):
    """One confirmation must remove the question permanently."""
    rows = [[1500.0], [2300.0]]
    before = mapper.resolve_table(["Mtt Glob"], rows, "BNA")[0]
    assert before.canonical is None

    config.aliases.learn("mtt glob", "prime_totale")
    after = mapper.resolve_table(["Mtt Glob"], rows, "BNA")[0]
    assert after.canonical == "prime_totale"
    assert after.method == "exact"


def test_bank_specific_alias_does_not_leak_to_other_banks(config, mapper):
    config.aliases.learn("montant", "capital_assure", bank="BNA")
    rows = [[50000.0], [60000.0]]
    assert mapper.resolve_table(["Montant"], rows, "BNA")[0].canonical == "capital_assure"
    other = mapper.resolve_table(["Montant"], rows, "BIAT")[0]
    assert other.canonical != "capital_assure"


class TestContentVerification:
    """A perfect-looking name over the wrong data is the dangerous failure."""

    def test_date_field_holding_numbers_is_flagged(self, config):
        profile = ColumnProfile(
            total=100, non_blank=100, numeric_ratio=0.94, date_ratio=0.0,
            unique_ratio=0.9, min_value=1000, max_value=50000, mean_length=5,
        )
        warnings = verify_against_content("date_effet", profile, config)
        assert any("dates" in w for w in warnings)

    def test_money_field_holding_text_is_flagged(self, config):
        profile = ColumnProfile(
            total=100, non_blank=100, numeric_ratio=0.05, date_ratio=0.0,
            unique_ratio=0.4, mean_length=14,
        )
        warnings = verify_against_content("prime_totale", profile, config)
        assert any("numbers" in w for w in warnings)

    def test_identifier_with_few_distinct_values_is_flagged(self, config):
        profile = ColumnProfile(
            total=100, non_blank=100, numeric_ratio=0.0, date_ratio=0.0,
            unique_ratio=0.05, mean_length=8,
        )
        warnings = verify_against_content("num_contrat", profile, config)
        assert any("distinct" in w for w in warnings)

    def test_correct_mapping_produces_no_warnings(self, config):
        profile = ColumnProfile(
            total=100, non_blank=100, numeric_ratio=1.0, date_ratio=0.0,
            unique_ratio=0.95, min_value=80.0, max_value=4000.0, mean_length=7,
        )
        assert verify_against_content("prime_totale", profile, config) == []


def test_profile_column_measures_contents():
    profile = profile_column(["2025-01-05", "2025-02-11", None, "2025-03-02"])
    assert profile.non_blank == 3
    assert profile.date_ratio == 1.0
