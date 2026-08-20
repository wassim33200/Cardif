"""Generate synthetic bank workbooks that reproduce the real files' pathologies.

The production data cannot leave the user's machine, so the test suite runs against
these instead. Every mess here was described as occurring in the real files:

* the table not starting at row 1, with blank rows and leftover scratch sums above it
* two-row merged headers ("Prime" spanning two columns, "nette"/"frais" beneath)
* header wording that changes between banks and between months of the same bank
* missing columns: some banks report nette + frais, others only the global premium
* stray totals and orphan numbers below the table
* French number formatting, non-breaking spaces, mixed date representations
* filenames that name their month in half a dozen different ways

Generation is seeded, so the same fixture set is produced on every run and tests are
reproducible.
"""

from __future__ import annotations

import argparse
import random
import shutil
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

MONTHS_FR = [
    "janvier", "fevrier", "mars", "avril", "mai", "juin",
    "juillet", "aout", "septembre", "octobre", "novembre", "decembre",
]
MONTHS_FR_ACCENTED = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

PRENOMS = ["Mohamed", "Amina", "Youssef", "Fatma", "Karim", "Leila", "Sami",
           "Nour", "Hedi", "Sonia", "Walid", "Ines", "Anis", "Rania", "Tarek"]
NOMS = ["Ben Ali", "Trabelsi", "Gharbi", "Mansouri", "Bouazizi", "Khelifi",
        "Jendoubi", "Sassi", "Chaabane", "Ferchichi", "Haddad", "Ayari"]
PRODUITS = ["Temporaire Deces", "Epargne Retraite", "Assurance Credit",
            "Prevoyance Famille", "Capital Deces"]
AGENCES = ["Tunis Centre", "Sfax Nord", "Sousse Medina", "Ariana", "Bizerte",
           "Gabes", "Nabeul", "Monastir"]

# Header wording variants, keyed by canonical field. A bank picks one variant per month,
# which is exactly how the real files drift.
HEADER_VARIANTS = {
    "num_contrat": ["N° Contrat", "Num contrat", "Numéro de contrat", "N°Contrat",
                    "Référence contrat", "N° POLICE"],
    "nom_client": ["Nom client", "Nom du client", "Client", "Nom et Prénom",
                   "ASSURE", "Souscripteur"],
    "date_effet": ["Date d'effet", "Date effet", "DATE D EFFET", "Date de souscription",
                   "Date début"],
    "produit": ["Produit", "Type de produit", "Garantie", "Formule", "Libellé produit"],
    "agence": ["Agence", "Code agence", "AGENCE", "Point de vente", "Succursale"],
    "prime_nette": ["Prime nette", "Prime de base", "PRIME NETTE", "Prime HT"],
    "frais": ["Frais", "Frais de dossier", "Accessoires", "Chargements"],
    "prime_totale": ["Prime totale", "Prime globale", "PRIME TTC", "Montant prime",
                     "Total prime", "Mtt Glob"],
    "capital_assure": ["Capital assuré", "Capital", "Montant assuré", "CAPITAL GARANTI"],
    "duree": ["Durée", "Durée (mois)", "Duree contrat", "Nb mois"],
}

SHEET_NAMES = ["Feuil1", "Sheet1", "Ventes", "VENTES", "Production", "Feuille1", "Etat"]


class BankStyle:
    """A bank's habits: which columns it reports and how it formats them.

    Held constant per bank so that fixtures behave like a real partner whose template
    drifts in wording but not in substance.

    Traits are assigned by cycling a fixed matrix rather than by rolling dice, because a
    test corpus must *guarantee* that every pathology appears. Random assignment left
    the two-row merged header uncovered entirely on the first run.
    """

    # (reports_breakdown, two_row_header, text_numbers, text_dates)
    TRAIT_MATRIX = [
        (True,  True,  False, False),  # breakdown + merged two-row header
        (False, False, True,  True),   # total only, everything as French text
        (True,  False, True,  False),  # breakdown, text numbers, real dates
        (False, False, False, True),   # total only, real numbers, text dates
        (True,  True,  True,  True),   # every awkward trait at once
        (False, False, False, False),  # the well-behaved bank
    ]

    def __init__(self, code: str, index: int, rng: random.Random):
        self.code = code
        traits = self.TRAIT_MATRIX[index % len(self.TRAIT_MATRIX)]
        # Does this bank break the premium down, or only report the global figure?
        self.reports_breakdown = traits[0]
        # Uses a merged two-row header for the premium block.
        self.two_row_header = traits[1]
        # Numbers written as French text ("1 234,56") rather than real numeric cells.
        self.text_numbers = traits[2]
        # Dates as text rather than real Excel dates.
        self.text_dates = traits[3]
        # Optional columns stay random: their absence is a mapping concern, not a
        # parsing pathology, so full coverage is not required.
        self.has_capital = rng.random() < 0.8
        self.has_duree = rng.random() < 0.6
        self.has_agence = rng.random() < 0.8
        self.sheet_name = SHEET_NAMES[index % len(SHEET_NAMES)]

    def fields(self) -> list[str]:
        out = ["num_contrat", "nom_client", "date_effet", "produit"]
        if self.has_agence:
            out.append("agence")
        if self.reports_breakdown:
            out += ["prime_nette", "frais", "prime_totale"]
        else:
            out.append("prime_totale")
        if self.has_capital:
            out.append("capital_assure")
        if self.has_duree:
            out.append("duree")
        return out


def fr_number(value: float, rng: random.Random) -> str:
    """Render a number the way a French-locale Excel does, with occasional oddities."""
    whole = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    if rng.random() < 0.15:
        # Narrow no-break space, which is what recent Excel versions emit.
        whole = whole.replace(" ", " ")
    if rng.random() < 0.10:
        whole += " DT"
    return whole


def make_rows(style: BankStyle, year: int, month: int, count: int,
              rng: random.Random) -> list[dict]:
    """Build the month's sale records before any formatting is applied."""
    rows = []
    first = date(year, month, 1)
    for i in range(count):
        nette = round(rng.uniform(80, 4000), 2)
        frais = round(nette * rng.uniform(0.02, 0.12), 2)
        total = round(nette + frais, 2)
        # Most policies take effect within the reported month; a few are late
        # declarations from an earlier month, which the pipeline must keep and flag.
        if rng.random() < 0.06:
            effet = first - timedelta(days=rng.randint(20, 90))
        else:
            effet = first + timedelta(days=rng.randint(0, 27))
        rows.append({
            "num_contrat": f"{style.code}-{year}{month:02d}-{i + 1:04d}",
            "nom_client": f"{rng.choice(PRENOMS)} {rng.choice(NOMS)}",
            "date_effet": effet,
            "produit": rng.choice(PRODUITS),
            "agence": rng.choice(AGENCES),
            "prime_nette": nette,
            "frais": frais,
            "prime_totale": total,
            "capital_assure": round(rng.uniform(10_000, 500_000), 2),
            "duree": rng.choice([12, 24, 36, 60, 120, 240]),
        })
    return rows


def format_cell(field: str, value, style: BankStyle, rng: random.Random):
    """Apply the bank's formatting habits to one value."""
    if field == "date_effet":
        if style.text_dates:
            if rng.random() < 0.15:
                return f"{value.day:02d}-{MONTHS_FR[value.month - 1][:4]}-{value.year}"
            return value.strftime("%d/%m/%Y")
        return value
    if field in {"prime_nette", "frais", "prime_totale", "capital_assure"}:
        if style.text_numbers:
            return fr_number(value, rng)
        return value
    return value


def write_month(path: Path, style: BankStyle, year: int, month: int,
                rng: random.Random) -> dict:
    """Write one month's workbook and return what the truth actually is.

    The returned record is the answer key the tests assert against: how many real data
    rows exist, where the header landed, and which fields were present.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = style.sheet_name

    fields = style.fields()
    n_rows = rng.randint(25, 120)
    rows = make_rows(style, year, month, n_rows, rng)

    # --- junk above the table -------------------------------------------------
    # Blank rows, plus the classic leftover: somebody's scratch sum in a lone cell.
    lead_blank = rng.randint(0, 8)
    cursor = 1 + lead_blank
    if rng.random() < 0.5:
        # A stray title or scratch number floating above the real header.
        if rng.random() < 0.5:
            ws.cell(row=cursor, column=rng.randint(1, 4),
                    value=round(rng.uniform(50_000, 2_000_000), 2))
        else:
            ws.cell(row=cursor, column=1,
                    value=f"Etat des ventes {MONTHS_FR_ACCENTED[month - 1]} {year}")
        cursor += rng.randint(1, 3)

    header_row = cursor

    # --- header ---------------------------------------------------------------
    # A bank keeps its wording for a whole month but drifts between months.
    chosen = {f: rng.choice(HEADER_VARIANTS[f]) for f in fields}

    if style.two_row_header and "prime_nette" in fields:
        # "Prime" merged across the premium columns, with the detail on the row below.
        col = 1
        second_row = {}
        for f in fields:
            if f == "prime_nette":
                start = col
                ws.cell(row=header_row, column=start, value="Prime")
                second_row[start] = "nette"
                second_row[start + 1] = "frais"
                second_row[start + 2] = "totale"
                ws.merge_cells(start_row=header_row, start_column=start,
                               end_row=header_row, end_column=start + 2)
                col += 3
            elif f in {"frais", "prime_totale"}:
                continue  # already covered by the merged block
            else:
                ws.cell(row=header_row, column=col, value=chosen[f])
                col += 1
        for c, text in second_row.items():
            ws.cell(row=header_row + 1, column=c, value=text)
        data_start = header_row + 2
        # Recompute column order to match what was actually written.
        order = []
        for f in fields:
            if f == "prime_nette":
                order += ["prime_nette", "frais", "prime_totale"]
            elif f in {"frais", "prime_totale"}:
                continue
            else:
                order.append(f)
    else:
        order = fields
        for c, f in enumerate(order, start=1):
            ws.cell(row=header_row, column=c, value=chosen[f])
        data_start = header_row + 1

    # --- data -----------------------------------------------------------------
    for r, record in enumerate(rows, start=data_start):
        for c, f in enumerate(order, start=1):
            ws.cell(row=r, column=c, value=format_cell(f, record[f], style, rng))

    last_data_row = data_start + len(rows) - 1

    # --- junk below the table -------------------------------------------------
    trailing = []
    after = last_data_row + rng.randint(1, 3)
    if rng.random() < 0.7:
        # A TOTAL line: label in the first column, sums under the money columns.
        ws.cell(row=after, column=1, value=rng.choice(["TOTAL", "Total", "TOTAUX", "Somme"]))
        for c, f in enumerate(order, start=1):
            if f in {"prime_nette", "frais", "prime_totale"}:
                ws.cell(row=after, column=c, value=sum(r[f] for r in rows))
        trailing.append(after)
        after += rng.randint(1, 3)
    if rng.random() < 0.4:
        # An orphan number with no label at all: someone's calculator scratch.
        ws.cell(row=after, column=rng.randint(2, max(2, len(order))),
                value=round(rng.uniform(1000, 90_000), 2))
        trailing.append(after)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)

    return {
        "path": str(path),
        "bank": style.code,
        "year": year,
        "month": month,
        "sheet": style.sheet_name,
        "header_row": header_row,
        "data_start": data_start,
        "data_end": last_data_row,
        "n_rows": len(rows),
        "fields": order,
        "headers": chosen,
        "two_row_header": style.two_row_header and "prime_nette" in fields,
        "trailing_junk_rows": trailing,
        "total_prime_totale": round(sum(r["prime_totale"] for r in rows), 2),
    }


def filename_for(bank: str, year: int, month: int, rng: random.Random) -> str:
    """Produce one of the many filename shapes the banks actually use."""
    m = MONTHS_FR[month - 1]
    m_acc = MONTHS_FR_ACCENTED[month - 1]
    yy = year % 100
    patterns = [
        f"Ventes_{m_acc.capitalize()}_{year}.xlsx",
        f"ventes {m} {yy}.xlsx",
        f"{bank}_{month:02d}_{year}.xlsx",
        f"{bank} {m_acc} {year}.xlsx",
        f"production-{year}-{month:02d}.xlsx",
        f"Etat {m[:4]} {yy}.xlsx",
        f"{year}{month:02d} ventes {bank}.xlsx",
        f"VENTES {m.upper()} {year}.xlsx",
    ]
    return rng.choice(patterns)


def generate(out_dir: Path, banks: list[str], year: int, seed: int = 42) -> list[dict]:
    """Generate a full year of workbooks for each bank. Returns the answer key."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    rng = random.Random(seed)
    truth = []
    for index, bank in enumerate(banks):
        style = BankStyle(bank, index, rng)
        folder = out_dir / f"{bank} {year}"
        for month in range(1, 13):
            name = filename_for(bank, year, month, rng)
            truth.append(write_month(folder / name, style, year, month, rng))
    return truth


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="tests/fixtures/generated")
    parser.add_argument("--banks", nargs="+", default=["BNA", "BIAT", "STB", "BH", "ATB", "UIB"])
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    truth = generate(Path(args.out), args.banks, args.year, args.seed)

    import json
    key = Path(args.out) / "_truth.json"
    key.write_text(json.dumps(truth, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"generated {len(truth)} workbooks in {args.out}")
    for t in truth[:3]:
        print(f"  {Path(t['path']).name}: header row {t['header_row']}, "
              f"{t['n_rows']} rows, fields={len(t['fields'])}, "
              f"two-row header={t['two_row_header']}")


if __name__ == "__main__":
    main()
