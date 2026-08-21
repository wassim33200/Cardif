# Cardif — offline consolidation of monthly bank sales

Every month the partner banks send Excel files of insurance sales. Each bank sells
**several products**, sends one file per product, fills them by hand, and changes them
between months: the table does not start at row 1, there are leftover scratch numbers
above and below it, column names differ per bank and per month, and there is usually a
`TOTAL` row at the bottom that poisons any grouping.

Worse, the products do not even agree on what a sale *is*: an ADE has a credit, a CRD
and a rate; a prévoyance has a chosen capital and a payment frequency; a travel policy
has a zone.

This tool turns that into one clean table per product, ready for Power BI —
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

For colleagues: double-click **`Installer.bat`** once, then **`Lancer Cardif.bat`**
(Windows) or the `.command` equivalents on macOS. `GUIDE.md` is written for them; this
README is not.

The app opens on localhost, offers to generate sample data on first run, and walks
through Fichiers → Colonnes → Consolidation.

```bash
streamlit run app/Accueil.py        # same thing, from a terminal
```

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

The folder names the bank (mapped in `config/banks.yaml`); the filename names **the
product and the month**. One file per product per month is the expected layout:

```
data/
  CNEP 2025/
    ADE_Immobilier_Mars_2025.xlsx
    SAHTI_Mars_2025.xlsx
    CTP mars 2025.xlsx
  BNPPED 2025/
    ADE credit automobile 03-2025.xlsx
    Assurcompte_2025_03.xlsx
```

A sub-folder per product works too: `CNEP 2025/SAHTI/ventes mars.xlsx`.

## What you get

```
warehouse/
  fact_ade_immobilier.parquet     one table per product, each with its own columns
  fact_sahti.parquet
  fact_cnep_total_prevoyance.parquet
  …
  dim_date.parquet                contiguous calendar with French labels
  dim_produit.parquet             every product and its family, from the catalogue
  dim_banque.parquet
  par_banque/CNEP_sahti.parquet   + .xlsx, extracts per bank AND per product
  _manifest.json                  which files are ingested, with content hashes
  audit.xlsx                      the full record (see below)
```

**In Power BI, load the `fact_*` tables you need and relate them all to `dim_date`,
`dim_banque` and `dim_produit`.** Each product table carries `banque`, `produit_code`
and `mois_reception`, so a shared date and bank dimension gives you per-product reports
and cross-product totals from the same model.

Mark `dim_date` as the date table so time intelligence (YoY, YTD, rolling 12) works.

## Configuration

| File | What it holds |
|---|---|
| `config/schema.yaml` | the catalogue of every field the tool can recognise, with its French aliases, its expected content, and its derivation rules |
| `config/produits.yaml` | **the products.** Each one picks its fields from the catalogue and gives the words that identify it in a filename. Adding a product is a block here and no code at all |
| `config/banks.yaml` | folder name → bank, plus per-bank overrides |
| `config/settings.yaml` | model endpoint, thresholds, tolerances |
| `config/aliases.yaml` | **the learned header map.** Grows every time you resolve a header. Back this up; it is the asset that makes the tool fast |

## Products are the unit of everything

Each bank sells several products, and **each product has its own columns**. An ADE is
attached to a credit and carries a CRD and a rate; a prévoyance carries a chosen capital
and a payment frequency; a travel policy carries a zone. Forcing them into one table
would leave it mostly empty and impossible to read.

So the product is detected from the filename (or a sub-folder), and it decides the
shape of the output:

```yaml
produits:
  sahti:
    label: "SAHTI"
    famille: sante
    partenaires: [CNEP]
    match: ["sahti", "sahty", "sante cnep"]
    colonnes: [banque, produit_code, mois_reception, num_contrat, nom_client,
               date_effet, formule, nb_assures, capital_assure, prime_totale]
    requis: [num_contrat, date_effet, prime_totale]
```

That block is the entire cost of adding a product. Matching only considers products the
bank actually distributes, and the longest keyword wins, so `ADE immobilier` is never
shadowed by `ADE`.

A file whose product cannot be read is not rejected: it falls back to `generique`, which
keeps the fields common to every product, and is flagged so you can rename it.

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

1. Run `cardif scan data/` first. It shows, without opening anything, which bank,
   product and month the tool reads from each path — fix any that come out blank by
   renaming the file or adding a keyword to `produits.yaml`.
2. Run `cardif profile data/ -o entetes.xlsx`. That inventory is the ground truth for
   what your banks actually send, and it now shows which products each header appears in.
3. Add any missing fields to `config/schema.yaml`, with the aliases you saw.
4. Adjust each product's `colonnes` in `config/produits.yaml`, and add your banks to
   `config/banks.yaml`.
5. Run `cardif run data/ --audit audit.xlsx` and read the audit before committing
   anything.
