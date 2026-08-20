from openpyxl import load_workbook

from cardif.detect import extract_table


def _tables(corpus, config):
    root, truth = corpus
    vocabulary = set(config.seed_aliases_from_schema())
    for entry in truth:
        workbook = load_workbook(entry["path"], data_only=True)
        table = extract_table(
            workbook[entry["sheet"]], vocabulary, config.settings.detection
        )
        workbook.close()
        yield entry, table


def test_finds_the_header_row_in_every_workbook(corpus, config):
    """The table starts anywhere from row 1 to row 12 across the corpus."""
    wrong = [
        (entry["path"], entry["header_row"], table.header_row)
        for entry, table in _tables(corpus, config)
        if table.header_row != entry["header_row"]
    ]
    assert wrong == []


def test_keeps_exactly_the_real_data_rows(corpus, config):
    """Junk above and below the table is removed; no real row is lost with it."""
    wrong = [
        (entry["path"], entry["n_rows"], table.n_rows)
        for entry, table in _tables(corpus, config)
        if table.n_rows != entry["n_rows"]
    ]
    assert wrong == []


def test_every_removed_row_is_accounted_for(corpus, config):
    """Nothing is discarded silently: each drop carries a row number and a reason."""
    for entry, table in _tables(corpus, config):
        for dropped in table.dropped_rows:
            assert dropped.row >= 1
            assert dropped.reason


def test_trailing_total_lines_are_removed(corpus, config):
    """The TOTAL row is what poisons grouping in Excel; it must never reach the data."""
    checked = 0
    for entry, table in _tables(corpus, config):
        if not entry["trailing_junk_rows"]:
            continue
        checked += 1
        assert table.data_end <= min(entry["trailing_junk_rows"]) - 1
    assert checked > 0, "corpus contains no trailing-junk files to check"


def test_merged_two_row_headers_are_composed(corpus, config):
    """"Prime" merged over "nette"/"frais"/"totale" must yield three distinct headers."""
    checked = 0
    for entry, table in _tables(corpus, config):
        if not entry["two_row_header"]:
            continue
        checked += 1
        assert table.header_row_end == table.header_row + 1
        assert "Prime nette" in table.headers
        assert "Prime frais" in table.headers
        assert "Prime totale" in table.headers
        # The leaf label is kept separately: a merged group label is shared by its
        # sibling columns and cannot identify any of them on its own.
        leaves = [parts[-1] for parts in table.header_parts if len(parts) > 1]
        assert "frais" in leaves
    assert checked > 0, "corpus contains no two-row-header files to check"


def test_header_row_count_and_column_count_agree(corpus, config):
    for entry, table in _tables(corpus, config):
        assert len(table.headers) == len(table.columns)
        assert len(table.header_parts) == len(table.headers)
        for row in table.rows:
            assert len(row) == len(table.headers)
