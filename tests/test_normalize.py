from cardif.normalize import is_blank, normalize_header, normalize_loose


def test_folds_accents_case_and_punctuation():
    assert normalize_header("  N° Contrat ") == "n contrat"
    assert normalize_header("Capital assuré") == "capital assure"
    assert normalize_header("Date d'effet") == "date d effet"


def test_underscore_is_a_separator_not_a_word_character():
    # Folder and file names rely on this: "biat_2024" must not read as one token.
    assert normalize_header("biat_2024") == "biat 2024"
    assert normalize_header("N_contrat") == "n contrat"


def test_exotic_unicode_spaces_are_spaces():
    # Excel emits non-breaking and narrow no-break spaces inside headers and amounts.
    assert normalize_header("MONTANT GLOBAL") == "montant global"
    assert normalize_header("MONTANT GLOBAL") == "montant global"
    assert is_blank(" ")


def test_loose_ignores_spacing_entirely():
    assert normalize_loose("N° Contrat") == "ncontrat"


def test_blank_detection():
    assert is_blank(None) and is_blank("") and is_blank("   ")
    assert not is_blank(0) and not is_blank("x")
