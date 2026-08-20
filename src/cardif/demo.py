"""Sample data, so the tool can be tried without touching real files.

A colleague seeing this for the first time should be able to click one button and watch
the whole thing work, before being asked to point it at anything confidential. The
sample deliberately contains the same problems the real files have -- a table that does
not start at row 1, a leftover total, headers worded differently each month, one bank
that reports a premium breakdown and another that reports only the global figure -- so
what they see demonstrated is what they will actually face.

It is written to a folder the user chooses (their Documents by default), not hidden in
the program, so they can open the files in Excel and see for themselves what the tool
was given.
"""

from __future__ import annotations

import datetime as dt
import random
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

MOIS = [
    "Janvier", "Fevrier", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Aout", "Septembre", "Octobre", "Novembre", "Decembre",
]

PRENOMS = ["Mohamed", "Amina", "Youssef", "Fatma", "Karim", "Leila", "Sami", "Nour",
           "Hedi", "Sonia", "Walid", "Ines", "Anis", "Rania", "Tarek", "Salma"]
NOMS = ["Ben Ali", "Trabelsi", "Gharbi", "Mansouri", "Bouazizi", "Khelifi", "Jendoubi",
        "Sassi", "Chaabane", "Ferchichi", "Haddad", "Ayari", "Nasri", "Baccouche"]
PRODUITS = ["Temporaire Deces", "Epargne Retraite", "Assurance Credit",
            "Prevoyance Famille", "Capital Deces"]
AGENCES = ["Tunis Centre", "Sfax Nord", "Sousse Medina", "Ariana", "Bizerte",
           "Gabes", "Nabeul", "Monastir"]

# Each bank keeps its habits but varies its wording month to month, exactly as the real
# partners do. This is what the header-matching step exists to absorb.
VARIANTES = {
    "num_contrat": ["N° Contrat", "Num contrat", "Numéro de contrat", "N° POLICE"],
    "nom_client": ["Nom client", "Nom et Prénom", "ASSURE", "Souscripteur"],
    "date_effet": ["Date d'effet", "Date effet", "Date de souscription", "Date début"],
    "produit": ["Produit", "Type de produit", "Garantie", "Formule"],
    "agence": ["Agence", "AGENCE", "Point de vente", "Code agence"],
    "prime_nette": ["Prime nette", "Prime de base", "PRIME NETTE"],
    "frais": ["Frais", "Frais de dossier", "Accessoires"],
    "prime_totale": ["Prime totale", "Prime globale", "PRIME TTC", "Montant prime"],
    "capital_assure": ["Capital assuré", "Capital", "CAPITAL GARANTI"],
}


class _Banque:
    """One partner bank's habits, held constant across the year."""

    def __init__(self, code: str, detaille: bool, texte: bool, feuille: str):
        self.code = code
        # Does it report nette + frais, or only the global premium?
        self.detaille = detaille
        # Does it write amounts as French text ("1 234,56") rather than as numbers?
        self.texte = texte
        self.feuille = feuille

    def champs(self) -> list[str]:
        base = ["num_contrat", "nom_client", "date_effet", "produit", "agence"]
        if self.detaille:
            base += ["prime_nette", "frais", "prime_totale"]
        else:
            base += ["prime_totale"]
        return base + ["capital_assure"]


BANQUES = [
    _Banque("BNA", detaille=True, texte=False, feuille="Feuil1"),
    _Banque("BIAT", detaille=False, texte=True, feuille="Ventes"),
    _Banque("STB", detaille=True, texte=True, feuille="Production"),
]


def _montant_fr(valeur: float) -> str:
    """Render an amount the way a French-locale Excel does."""
    return f"{valeur:,.2f}".replace(",", " ").replace(".", ",")


def _ecrire_mois(chemin: Path, banque: _Banque, annee: int, mois: int,
                 rng: random.Random) -> int:
    """Write one month for one bank. Returns the number of sales written."""
    classeur = Workbook()
    feuille = classeur.active
    feuille.title = banque.feuille

    champs = banque.champs()
    entetes = {c: rng.choice(VARIANTES[c]) for c in champs}

    # --- the mess above the table, which is what has to be cleaned by hand today ---
    ligne = 1 + rng.randint(0, 4)
    if rng.random() < 0.6:
        cellule = feuille.cell(row=ligne, column=1,
                               value=f"Etat des ventes - {MOIS[mois - 1]} {annee}")
        cellule.font = Font(bold=True, size=13)
        ligne += rng.randint(1, 2)
    if rng.random() < 0.4:
        # Somebody's scratch sum, left behind in a stray cell.
        feuille.cell(row=ligne, column=rng.randint(2, 4),
                     value=round(rng.uniform(50_000, 900_000), 2))
        ligne += rng.randint(1, 2)

    ligne_entete = ligne
    for colonne, champ in enumerate(champs, start=1):
        cellule = feuille.cell(row=ligne_entete, column=colonne, value=entetes[champ])
        cellule.font = Font(bold=True)

    # --- the sales themselves ------------------------------------------------------
    premier = dt.date(annee, mois, 1)
    nombre = rng.randint(18, 60)
    total = 0.0

    for index in range(nombre):
        nette = round(rng.uniform(120, 3800), 2)
        frais = round(nette * rng.uniform(0.03, 0.10), 2)
        globale = round(nette + frais, 2)
        total += globale

        # A few policies took effect before the month that reports them: a real thing,
        # and the reason the tool keeps the effective date and the reporting month apart.
        if rng.random() < 0.05:
            effet = premier - dt.timedelta(days=rng.randint(20, 70))
        else:
            effet = premier + dt.timedelta(days=rng.randint(0, 27))

        valeurs = {
            "num_contrat": f"{banque.code}-{annee}{mois:02d}-{index + 1:04d}",
            "nom_client": f"{rng.choice(PRENOMS)} {rng.choice(NOMS)}",
            "date_effet": effet,
            "produit": rng.choice(PRODUITS),
            "agence": rng.choice(AGENCES),
            "prime_nette": _montant_fr(nette) if banque.texte else nette,
            "frais": _montant_fr(frais) if banque.texte else frais,
            "prime_totale": _montant_fr(globale) if banque.texte else globale,
            "capital_assure": round(rng.uniform(15_000, 450_000), 2),
        }
        for colonne, champ in enumerate(champs, start=1):
            feuille.cell(row=ligne_entete + 1 + index, column=colonne,
                         value=valeurs[champ])

    # --- the total line at the bottom, which ruins grouping in Excel ---------------
    fin = ligne_entete + nombre + rng.randint(2, 3)
    if rng.random() < 0.75:
        feuille.cell(row=fin, column=1, value="TOTAL").font = Font(bold=True)
        colonne_totale = champs.index("prime_totale") + 1
        feuille.cell(row=fin, column=colonne_totale,
                     value=_montant_fr(total) if banque.texte else round(total, 2))

    for colonne in range(1, len(champs) + 1):
        feuille.column_dimensions[
            feuille.cell(row=ligne_entete, column=colonne).column_letter
        ].width = 20

    chemin.parent.mkdir(parents=True, exist_ok=True)
    classeur.save(chemin)
    return nombre


def _nom_fichier(banque: str, annee: int, mois: int, rng: random.Random) -> str:
    """One of the several naming habits the banks actually use."""
    m = MOIS[mois - 1]
    modeles = [
        f"Ventes_{m}_{annee}.xlsx",
        f"ventes {m.lower()} {annee % 100}.xlsx",
        f"{banque}_{mois:02d}_{annee}.xlsx",
        f"Etat {m.lower()} {annee}.xlsx",
        f"{annee}{mois:02d} ventes {banque}.xlsx",
    ]
    return rng.choice(modeles)


def creer_donnees_exemple(
    destination: Path | str, annee: int = 2025, graine: int = 7
) -> dict:
    """Create a year of sample files for three banks.

    Returns a summary the interface can show: how many folders, files and sales.
    """
    destination = Path(destination)
    rng = random.Random(graine)

    fichiers = 0
    ventes = 0
    for banque in BANQUES:
        dossier = destination / f"{banque.code} {annee}"
        for mois in range(1, 13):
            nom = _nom_fichier(banque.code, annee, mois, rng)
            ventes += _ecrire_mois(dossier / nom, banque, annee, mois, rng)
            fichiers += 1

    lisez_moi = destination / "LISEZ-MOI.txt"
    lisez_moi.write_text(
        "Données d'exemple créées par Cardif\n"
        "==================================\n\n"
        f"{len(BANQUES)} banques, {fichiers} fichiers, {ventes} ventes au total.\n\n"
        "Ces fichiers imitent volontairement les défauts des vrais fichiers :\n"
        "  - le tableau ne commence pas à la première ligne\n"
        "  - il reste des chiffres de brouillon au-dessus du tableau\n"
        "  - une ligne TOTAL en bas, qui fausse les regroupements\n"
        "  - les noms de colonnes changent d'un mois à l'autre\n"
        "  - une banque détaille prime nette + frais, une autre donne le total seul\n"
        "  - certains montants sont écrits en texte (1 234,56) et non en nombres\n\n"
        "Ouvrez-en un dans Excel pour voir ce que l'outil reçoit.\n"
        "Aucune donnée réelle : tous les noms sont inventés.\n",
        encoding="utf-8",
    )

    return {
        "dossier": str(destination),
        "banques": len(BANQUES),
        "fichiers": fichiers,
        "ventes": ventes,
    }
