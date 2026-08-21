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

PRENOMS = ["Mohamed", "Amina", "Youcef", "Fatima", "Karim", "Leila", "Samir", "Nour",
           "Hakim", "Sonia", "Walid", "Ines", "Anis", "Rania", "Tarek", "Salima"]
NOMS = ["Benali", "Bouazza", "Belkacem", "Hamdani", "Mokrani", "Zerrouki", "Ait Ahmed",
        "Boudjema", "Cherif", "Slimani", "Haddad", "Bensaid", "Meziane", "Larbi"]
PRODUITS = ["Temporaire Deces", "Epargne Retraite", "Assurance Credit",
            "Prevoyance Famille", "Capital Deces"]
AGENCES = ["Alger Centre", "Bab Ezzouar", "Oran Es Senia", "Constantine",
           "Annaba", "Blida", "Sétif", "Tlemcen", "Béjaïa", "Tizi Ouzou"]

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


class _Offre:
    """One bank selling one product, with its own habits and its own columns.

    The point of the sample is to show the situation the tool exists for: the same bank
    sends several files a month, one per product, and those files have almost nothing in
    common beyond the client and the premium.
    """

    def __init__(self, banque: str, produit: str, colonnes: list[str],
                 texte: bool, feuille: str):
        self.banque = banque
        self.produit = produit
        self.colonnes = colonnes
        # Does it write amounts as French text ("1 234,56") rather than as numbers?
        self.texte = texte
        self.feuille = feuille


# Champs propres à chaque famille de produits : c'est ce qui rend les fichiers
# irréconciliables dans un seul tableau.
COLONNES_ADE = ["num_contrat", "num_credit", "nom_client", "date_effet", "agence",
                "montant_credit", "capital_restant_du", "duree", "taux_prime",
                "prime_nette", "frais", "prime_totale"]
COLONNES_PREVOYANCE = ["num_contrat", "nom_client", "date_effet", "agence", "formule",
                       "capital_assure", "periodicite", "prime_totale"]
COLONNES_SANTE = ["num_contrat", "nom_client", "date_effet", "agence", "formule",
                  "nb_assures", "capital_assure", "prime_totale"]
COLONNES_VOYAGE = ["num_contrat", "nom_client", "date_effet", "agence", "zone",
                   "duree", "capital_assure", "prime_totale"]
COLONNES_CARTE = ["num_contrat", "nom_client", "date_effet", "agence", "type_carte",
                  "zone", "capital_assure", "prime_totale"]

OFFRES = [
    _Offre("CNEP", "ade_immobilier", COLONNES_ADE, texte=False, feuille="Feuil1"),
    _Offre("CNEP", "sahti", COLONNES_SANTE, texte=True, feuille="Ventes"),
    _Offre("CNEP", "cnep_total_prevoyance", COLONNES_PREVOYANCE, texte=False,
           feuille="Production"),
    _Offre("CNEP", "rihlati", COLONNES_VOYAGE, texte=True, feuille="Feuil1"),
    _Offre("BNPPED", "ade_immobilier", COLONNES_ADE, texte=True, feuille="Sheet1"),
    _Offre("BNPPED", "ade_automobile", COLONNES_ADE, texte=False, feuille="Ventes"),
    _Offre("BNPPED", "assurcompte", COLONNES_PREVOYANCE, texte=False, feuille="Feuil1"),
    _Offre("BNPPED", "voyage_visa", COLONNES_CARTE, texte=True, feuille="VENTES"),
]

# Comment chaque produit est nommé dans le nom du fichier, tel que les banques le font.
NOM_PRODUIT = {
    "ade_immobilier": ["ADE_Immobilier", "ADE immo", "Credit immobilier"],
    "ade_automobile": ["ADE_Automobile", "ADE auto", "Credit automobile"],
    "sahti": ["SAHTI", "Sahti"],
    "cnep_total_prevoyance": ["CTP", "CNEP Total Prevoyance"],
    "rihlati": ["RIHLATI", "Rihlati"],
    "assurcompte": ["Assurcompte", "ASSURCOMPTE"],
    "voyage_visa": ["Carte VISA", "Voyage VISA"],
}

VARIANTES = {
    "num_contrat": ["N° Contrat", "Num contrat", "Numéro de contrat", "N° POLICE"],
    "num_credit": ["N° Crédit", "Num credit", "Référence crédit", "Dossier crédit"],
    "nom_client": ["Nom client", "Nom et Prénom", "ASSURE", "Adhérent", "Emprunteur"],
    "date_effet": ["Date d'effet", "Date effet", "Date de souscription", "Date début"],
    "agence": ["Agence", "AGENCE", "Point de vente", "Code agence"],
    "montant_credit": ["Montant du crédit", "Montant crédit", "Capital emprunté"],
    "capital_restant_du": ["CRD", "Capital restant dû", "Encours"],
    "duree": ["Durée", "Durée (mois)", "Nb mois"],
    "taux_prime": ["Taux", "Taux de prime", "Tarif"],
    "prime_nette": ["Prime nette", "Prime de base", "PRIME NETTE"],
    "frais": ["Frais", "Frais de dossier", "Accessoires"],
    "prime_totale": ["Prime totale", "Prime globale", "PRIME TTC", "Montant prime"],
    "capital_assure": ["Capital assuré", "Capital", "CAPITAL GARANTI"],
    "formule": ["Formule", "Formule de couverture", "Option"],
    "periodicite": ["Périodicité", "Fréquence", "Mode de paiement"],
    "nb_assures": ["Nombre d'assurés", "Nb assurés", "Nb bénéficiaires"],
    "zone": ["Zone", "Zone géographique", "Destination"],
    "type_carte": ["Type de carte", "Carte", "Gamme carte"],
}

FORMULES = {
    "ade_immobilier": ["Classique", "Enrichie"],
    "ade_automobile": ["Classique"],
    "sahti": ["Individuelle", "Familiale"],
    "cnep_total_prevoyance": ["Formule 1", "Formule 2"],
    "assurcompte": ["Classic", "Prestige"],
    "rihlati": ["Zone 1", "Zone 2"],
}
PERIODICITES = ["Mensuel", "Semestriel", "Annuel"]
ZONES = ["Zone 1", "Zone 2"]
CARTES = ["Classic", "Gold", "Platinium"]
CAPITAUX_CTP = [500_000, 1_000_000, 1_500_000, 2_000_000, 3_000_000]


def _montant_fr(valeur: float) -> str:
    """Render an amount the way a French-locale Excel does."""
    return f"{valeur:,.2f}".replace(",", " ").replace(".", ",")


def _valeur(champ: str, offre: "_Offre", index: int, annee: int, mois: int,
            rng: random.Random) -> object:
    """One cell, generated to suit the product's own field."""
    premier = dt.date(annee, mois, 1)

    if champ == "num_contrat":
        return f"{offre.banque}-{offre.produit[:4].upper()}-{annee}{mois:02d}-{index:04d}"
    if champ == "num_credit":
        return f"CR{annee}{mois:02d}{index:05d}"
    if champ == "nom_client":
        return f"{rng.choice(PRENOMS)} {rng.choice(NOMS)}"
    if champ == "date_effet":
        # A few policies take effect before the month that reports them.
        if rng.random() < 0.05:
            return premier - dt.timedelta(days=rng.randint(20, 70))
        return premier + dt.timedelta(days=rng.randint(0, 27))
    if champ == "agence":
        return rng.choice(AGENCES)
    if champ == "duree":
        return rng.choice([12, 24, 36, 60, 120, 180, 240])
    if champ == "periodicite":
        return rng.choice(PERIODICITES)
    if champ == "formule":
        return rng.choice(FORMULES.get(offre.produit, ["Standard"]))
    if champ == "nb_assures":
        return rng.randint(1, 5)
    if champ == "zone":
        return rng.choice(ZONES)
    if champ == "type_carte":
        return rng.choice(CARTES)
    if champ == "taux_prime":
        return f"{rng.uniform(0.20, 0.85):.2f}%".replace(".", ",")
    return None


def _ecrire_mois(chemin: Path, offre: "_Offre", annee: int, mois: int,
                 rng: random.Random) -> tuple[int, float]:
    """Write one month of one product for one bank. Returns (rows, premium total)."""
    classeur = Workbook()
    feuille = classeur.active
    feuille.title = offre.feuille

    entetes = {c: rng.choice(VARIANTES[c]) for c in offre.colonnes}

    # --- the mess above the table, which is what has to be cleaned by hand today ---
    ligne = 1 + rng.randint(0, 4)
    if rng.random() < 0.6:
        titre = feuille.cell(
            row=ligne, column=1,
            value=f"Etat des ventes {offre.produit.replace('_', ' ').upper()} - "
                  f"{MOIS[mois - 1]} {annee}",
        )
        titre.font = Font(bold=True, size=13)
        ligne += rng.randint(1, 2)
    if rng.random() < 0.4:
        feuille.cell(row=ligne, column=rng.randint(2, 4),
                     value=round(rng.uniform(50_000, 900_000), 2))
        ligne += rng.randint(1, 2)

    ligne_entete = ligne
    for colonne, champ in enumerate(offre.colonnes, start=1):
        cellule = feuille.cell(row=ligne_entete, column=colonne, value=entetes[champ])
        cellule.font = Font(bold=True)

    # --- the sales themselves ------------------------------------------------------
    nombre = rng.randint(15, 55)
    total = 0.0

    for index in range(1, nombre + 1):
        # Money is built together so the premium stays coherent with the cover.
        if offre.produit.startswith("ade"):
            credit = round(rng.uniform(500_000, 8_000_000), 2)
            crd = round(credit * rng.uniform(0.35, 1.0), 2)
            nette = round(credit * rng.uniform(0.002, 0.009), 2)
            capital = credit
        elif offre.produit == "cnep_total_prevoyance":
            capital = float(rng.choice(CAPITAUX_CTP))
            nette = round(capital * rng.uniform(0.0008, 0.0025), 2)
            credit = crd = 0.0
        else:
            capital = round(rng.uniform(100_000, 2_000_000), 2)
            nette = round(rng.uniform(1_500, 25_000), 2)
            credit = crd = 0.0

        frais = round(nette * rng.uniform(0.03, 0.10), 2)
        globale = round(nette + frais, 2)
        total += globale

        montants = {
            "montant_credit": credit,
            "capital_restant_du": crd,
            "capital_assure": capital,
            "prime_nette": nette,
            "frais": frais,
            "prime_totale": globale,
        }

        for colonne, champ in enumerate(offre.colonnes, start=1):
            if champ in montants:
                brut = montants[champ]
                valeur = _montant_fr(brut) if offre.texte else brut
            else:
                valeur = _valeur(champ, offre, index, annee, mois, rng)
            feuille.cell(row=ligne_entete + index, column=colonne, value=valeur)

    # --- the total line at the bottom, which ruins grouping in Excel ---------------
    fin = ligne_entete + nombre + rng.randint(2, 3)
    if rng.random() < 0.75:
        feuille.cell(row=fin, column=1, value="TOTAL").font = Font(bold=True)
        if "prime_totale" in offre.colonnes:
            colonne_totale = offre.colonnes.index("prime_totale") + 1
            feuille.cell(row=fin, column=colonne_totale,
                         value=_montant_fr(total) if offre.texte else round(total, 2))

    for colonne in range(1, len(offre.colonnes) + 1):
        feuille.column_dimensions[
            feuille.cell(row=ligne_entete, column=colonne).column_letter
        ].width = 20

    chemin.parent.mkdir(parents=True, exist_ok=True)
    classeur.save(chemin)
    return nombre, total


def _nom_fichier(offre: "_Offre", annee: int, mois: int, rng: random.Random) -> str:
    """A filename naming the product and the month, as the banks actually write them."""
    produit = rng.choice(NOM_PRODUIT[offre.produit])
    m = MOIS[mois - 1]
    modeles = [
        f"{produit}_{m}_{annee}.xlsx",
        f"{produit} {m.lower()} {annee}.xlsx",
        f"{produit}_{mois:02d}_{annee}.xlsx",
        f"Ventes {produit} {m.lower()} {annee % 100}.xlsx",
        f"{annee}{mois:02d} {produit}.xlsx",
    ]
    return rng.choice(modeles)


def creer_donnees_exemple(
    destination: Path | str, annee: int = 2025, graine: int = 7
) -> dict:
    """Create a year of sample files: one file per product, per bank, per month.

    Returns a summary the interface can show.
    """
    destination = Path(destination)
    rng = random.Random(graine)

    fichiers = 0
    ventes = 0
    produits = sorted({offre.produit for offre in OFFRES})
    banques = sorted({offre.banque for offre in OFFRES})

    for offre in OFFRES:
        dossier = destination / f"{offre.banque} {annee}"
        for mois in range(1, 13):
            nom = _nom_fichier(offre, annee, mois, rng)
            lignes, _ = _ecrire_mois(dossier / nom, offre, annee, mois, rng)
            ventes += lignes
            fichiers += 1

    lisez_moi = destination / "LISEZ-MOI.txt"
    lisez_moi.write_text(
        "Données d'exemple créées par Cardif\n"
        "==================================\n\n"
        f"{len(banques)} banques, {len(produits)} produits, {fichiers} fichiers, "
        f"{ventes} ventes au total.\n\n"
        "Un fichier par produit, par banque et par mois, comme dans la réalité.\n"
        "Les produits n'ont PAS les mêmes colonnes :\n"
        "  - une ADE porte un crédit, un CRD et un taux\n"
        "  - une prévoyance porte un capital et une périodicité\n"
        "  - une assurance voyage porte une zone et une durée de séjour\n\n"
        "Ces fichiers imitent volontairement les défauts des vrais fichiers :\n"
        "  - le tableau ne commence pas à la première ligne\n"
        "  - il reste des chiffres de brouillon au-dessus du tableau\n"
        "  - une ligne TOTAL en bas, qui fausse les regroupements\n"
        "  - les noms de colonnes changent d'un mois à l'autre\n"
        "  - certains montants sont écrits en texte (1 234,56) et non en nombres\n\n"
        "Ouvrez-en un dans Excel pour voir ce que l'outil reçoit.\n"
        "Aucune donnée réelle : tous les noms sont inventés.\n",
        encoding="utf-8",
    )

    return {
        "dossier": str(destination),
        "banques": len(banques),
        "produits": len(produits),
        "fichiers": fichiers,
        "ventes": ventes,
    }
