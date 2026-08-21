import pytest

from cardif.filemeta import resolve_bank, resolve_period, scan


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("Ventes_Février_2025.xlsx", (2025, 2)),
        ("ventes fevrier 25.xlsx", (2025, 2)),
        ("BNA_03_2025.xlsx", (2025, 3)),
        ("production-2025-04.xlsx", (2025, 4)),
        ("202512 ventes BNA.xlsx", (2025, 12)),
        ("VENTES JUILLET 2025.xlsx", (2025, 7)),
        ("Etat juin 25.xlsx", (2025, 6)),
        ("Etat juil 25.xlsx", (2025, 7)),
        ("BNA août 2025.xlsx", (2025, 8)),
    ],
)
def test_period_patterns(filename, expected):
    year, month, method, _ = resolve_period(filename, 2025)
    assert (year, month) == expected
    assert method != "unresolved"


@pytest.mark.parametrize(
    "filename", ["ventes.xlsx", "Ventes 2025.xlsx", "rapport final.xlsx"]
)
def test_period_unresolved_rather_than_guessed(filename):
    _, _, method, _ = resolve_period(filename, 2025)
    assert method == "unresolved"


def test_two_month_names_is_ambiguous_not_first_wins():
    # Picking the first would silently file the sales under the wrong month.
    _, month, method, _ = resolve_period("mars avril 2025.xlsx", 2025)
    assert month is None
    assert method == "unresolved"


def test_bank_from_folder(config):
    assert resolve_bank("CNEP 2025", config.banks)[0] == "CNEP"
    assert resolve_bank("cnep_2024", config.banks)[0] == "CNEP"
    assert resolve_bank("BNP Paribas El Djazair 2025", config.banks)[0] == "BNPPED"
    assert resolve_bank("BNPPED 2025", config.banks)[0] == "BNPPED"


def test_bank_token_must_be_a_whole_word(config):
    # "CNEPX" must not resolve to "CNEP" just because it starts with it.
    assert resolve_bank("CNEPX 2025", config.banks)[0] is None
    assert resolve_bank("XYZ 2025", config.banks)[0] is None


def test_scan_resolves_every_file_in_the_corpus(corpus, config):
    root, truth = corpus
    metas = scan(root, config.banks)
    assert len(metas) == len(truth)

    by_path = {str(m.path): m for m in metas}
    for entry in truth:
        meta = by_path[entry["path"]]
        assert meta.bank_code == entry["bank"]
        assert (meta.year, meta.month) == (entry["year"], entry["month"])
        assert meta.period_method != "unresolved"
