# Cardif — offline consolidation of monthly bank sales

Every month the partner banks send Excel files of life-insurance sales. Each bank uses
its own format, fills it by hand, and changes it between months: the table does not start
at row 1, there are leftover scratch numbers above and below it, column names differ per
bank and per month, some banks give `prime nette + frais` and others only the global
premium, and there is usually a `TOTAL` row at the bottom that poisons any grouping.

This tool turns that into a single analytical database per bank, ready for Power BI —
**entirely offline**. No cloud API, no telemetry, no data leaving the machine.

## The design in one paragraph

A deterministic pipeline does the work; a local model is consulted only for the header
strings the rules cannot resolve, and every answer it gives is cached to a file you own.
Re-running a past month therefore costs zero model calls and produces byte-identical
output. That is what makes the numbers defensible: every column traces to a rule or to a
recorded human decision, not to a model's mood on a given day.

## Install

```bash
pip install -e ".[dev]"          # add ".[xls]" if any bank still sends legacy .xls
```

Offline install, for a machine with no internet:

```bash
pip download -d wheels -e .      # on a connected machine
pip install --no-index --find-links wheels -e .
```

## Use

```bash
streamlit run app/Home.py        # the daily driver, on localhost only
```

Point it at the folder holding your bank folders and follow the pages in order.

Or from the command line:

```bash
cardif scan     data/                          # what the folders and filenames say
cardif profile  data/ -o entetes.xlsx          # every header ever seen
cardif run      data/ --audit audit.xlsx       # process, writing nothing
cardif commit   data/ --audit audit.xlsx       # process and append to the warehouse
cardif status                                  # what the warehouse holds
```

`run` never writes. `commit` is the only command that changes the warehouse, and it
refuses to write when error-level flags were raised unless you pass `--allow-errors`.

## Expected layout

```
data/
  BNA 2025/
    Ventes_Janvier_2025.xlsx
    ventes fevrier 25.xlsx
    BNA_03_2025.xlsx                ... any naming; the month is parsed from it
  BIAT 2025/
    ...
```

The folder names the bank (mapped in `config/banks.yaml`); the filename names the month.

## What you get

```
warehouse/
  fact_ventes.parquet         all banks, one row per sale, `banque` as a column
  par_banque/BNA.parquet      + .xlsx, per-bank extracts
  dim_date.parquet            contiguous calendar with French labels
  dim_banque.parquet
  dim_produit.parquet
  _manifest.json              which files are already ingested, with content hashes
  audit.xlsx                  the full record (see below)
```

**Point Power BI at `fact_ventes.parquet` and filter on `banque`.** The per-bank files
exist for handing one bank its own data, but a single model with a bank filter gives
cross-bank comparison for free, where separate tables force a duplicated report page per
bank and make "all banks" impossible.

Mark `dim_date` as the date table so time intelligence (YoY, YTD, rolling 12) works.

## Configuration

| File | What it holds |
|---|---|
| `config/schema.yaml` | the canonical fields, their French aliases, derivation rules, and the **export profiles** — this is where "I only want the global premium" is expressed |
| `config/banks.yaml` | folder name → bank, plus per-bank overrides |
| `config/settings.yaml` | model endpoint, thresholds, tolerances |
| `config/aliases.yaml` | **the learned header map.** Grows every time you resolve a header. Back this up; it is the asset that makes the tool fast |

An export profile is just a column list:

```yaml
profiles:
  powerbi_2025:
    columns: [banque, mois_reception, num_contrat, nom_client, date_effet,
              produit, agence, prime_totale, capital_assure]
    required: [num_contrat, date_effet, prime_totale]
```

A bank reporting only a total is used as-is. A bank reporting `nette + frais` has the
total **derived** and tagged `prime_totale_source = derived`, so a computed figure is
never mistaken for a reported one.

## Two columns that look redundant and are not

`mois_reception` comes from the filename — your reporting period. `date_effet` comes from
the row — when the policy actually took effect. A policy effective 12 March can be
declared in the May file. Merging them would erase late declarations silently; keeping
both makes the reporting lag measurable.

## What the tool will not do

- **It does not delete rows it merely dislikes.** Only structural junk — blank rows, the
  `TOTAL` line, orphan cells — is removed, and every removal is recorded in the audit
  with its original cell reference. Rows that fail a check are *flagged and kept*.
- **It does not invent numbers.** A derived value is labelled as derived.
- **It does not guess when it is genuinely unsure.** An ambiguous month abbreviation
  ("jui" is both juin and juillet) resolves to nothing rather than to a coin flip; two
  columns claiming the same field go to you, not to whichever one happened to be last.
- **It does not silently change the output shape.** A month that would drop or rename a
  column fails the run instead of breaking every report downstream.

## Privacy

- The local model receives header text, filenames, and an *anonymised type profile* of a
  column ("38 non-empty values, 97% distinct, all parse as dates"). Client names and
  contract numbers do not leave the dataframe. Sending sample values is opt-in
  (`llm.send_sample_values`).
- Any non-loopback model endpoint is refused outright (`llm.require_loopback`).
- The HTTP client is stdlib-only, so no dependency can introduce telemetry.
- Streamlit's usage statistics are disabled and the app binds to `127.0.0.1` only
  (`.streamlit/config.toml`).

These are covered by tests in `tests/test_llm.py`, so the guarantee is checkable rather
than a promise.

## Tests

```bash
pytest
```

The suite runs against a generated corpus of 72 synthetic bank workbooks reproducing the
real pathologies — offset headers, merged two-row headers, French number and date
formats, trailing totals, per-month header drift. Traits are assigned from a fixed matrix
rather than at random, so every pathology is guaranteed coverage.

## Adapting it to your files

1. Run `cardif profile data/ -o entetes.xlsx`. That inventory is the ground truth for
   what your banks actually send.
2. Add the fields you need to `config/schema.yaml`, with the aliases you saw.
3. Define an export profile with the columns you want.
4. Add your banks to `config/banks.yaml`.
5. Run `cardif run data/ --audit audit.xlsx` and read the audit before committing
   anything.
