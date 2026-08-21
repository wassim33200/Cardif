"""Shared interface pieces.

Written for people who do not work with file paths. Nothing here asks anyone to type a
path, remember a folder name, or know what a profile is.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

# Places worth offering as a starting point when someone opens the browser.
def _points_de_depart() -> list[tuple[str, Path]]:
    home = Path.home()
    candidats = [
        ("Bureau", home / "Desktop"),
        ("Bureau", home / "Bureau"),
        ("Documents", home / "Documents"),
        ("Téléchargements", home / "Downloads"),
        ("Téléchargements", home / "Téléchargements"),
        ("Dossier personnel", home),
    ]
    vus: set[Path] = set()
    sortie = []
    for nom, chemin in candidats:
        if chemin.exists() and chemin not in vus:
            vus.add(chemin)
            sortie.append((nom, chemin))
    return sortie


def _ressemble_a_un_dossier_banque(chemin: Path) -> bool:
    """Whether a folder looks like it holds a year of one bank's files."""
    try:
        return any(
            enfant.suffix.lower() in {".xlsx", ".xlsm", ".xls"}
            for enfant in chemin.iterdir()
            if enfant.is_file() and not enfant.name.startswith("~$")
        )
    except (PermissionError, OSError):
        return False


def compter_classeurs(chemin: Path) -> int:
    """How many workbooks sit anywhere under a folder."""
    try:
        return sum(
            1 for p in chemin.rglob("*")
            if p.is_file()
            and p.suffix.lower() in {".xlsx", ".xlsm", ".xls"}
            and not p.name.startswith("~$")
        )
    except (PermissionError, OSError):
        return 0


def selecteur_de_dossier(cle: str = "data_root") -> str | None:
    """Let the user find a folder by clicking, never by typing a path.

    Shows the usual starting points, then the sub-folders of wherever they are, with a
    running count of the Excel files each one contains so they can tell at a glance
    which is the right one.
    """
    etat = st.session_state
    position = etat.get("_navigation")
    if position is None:
        depart = _points_de_depart()
        position = str(depart[0][1]) if depart else str(Path.home())
        etat["_navigation"] = position

    courant = Path(position)

    raccourcis = _points_de_depart()
    if raccourcis:
        colonnes = st.columns(len(raccourcis))
        for colonne, (nom, chemin) in zip(colonnes, raccourcis):
            if colonne.button(nom, use_container_width=True, key=f"raccourci_{nom}_{chemin}"):
                etat["_navigation"] = str(chemin)
                st.rerun()

    st.caption(f"Vous êtes dans : {courant}")

    haut, choisir = st.columns([1, 2])
    if courant.parent != courant:
        if haut.button("⬆️ Dossier parent", use_container_width=True):
            etat["_navigation"] = str(courant.parent)
            st.rerun()

    nombre = compter_classeurs(courant)
    if choisir.button(
        f"✅ Choisir ce dossier ({nombre} fichier·s Excel)",
        type="primary", use_container_width=True, disabled=nombre == 0,
    ):
        etat[cle] = str(courant)
        return str(courant)

    try:
        sous_dossiers = sorted(
            (p for p in courant.iterdir() if p.is_dir() and not p.name.startswith(".")),
            key=lambda p: p.name.lower(),
        )
    except (PermissionError, OSError):
        st.warning("Ce dossier n'est pas accessible.")
        return None

    if not sous_dossiers:
        st.caption("Ce dossier ne contient pas d'autres dossiers.")
        return None

    st.write("**Ouvrir un sous-dossier :**")
    for lot in [sous_dossiers[i : i + 3] for i in range(0, len(sous_dossiers), 3)]:
        colonnes = st.columns(3)
        for colonne, dossier in zip(colonnes, lot):
            compte = compter_classeurs(dossier)
            etiquette = f"📁 {dossier.name}"
            if compte:
                etiquette += f"  ·  {compte} fichier·s"
            if colonne.button(
                etiquette, use_container_width=True, key=f"nav_{dossier}"
            ):
                etat["_navigation"] = str(dossier)
                st.rerun()
    return None


def etat_du_dossier(racine: str) -> tuple[bool, str]:
    """Check a chosen folder makes sense, and say plainly what is wrong if not."""
    if not racine:
        return False, "Aucun dossier choisi."
    chemin = Path(racine)
    if not chemin.exists():
        return False, f"Le dossier « {racine} » n'existe plus. Choisissez-en un autre."
    if not chemin.is_dir():
        return False, "Ce n'est pas un dossier."
    if compter_classeurs(chemin) == 0:
        return False, (
            "Ce dossier ne contient aucun fichier Excel. Vérifiez que vous avez "
            "choisi le dossier qui contient les dossiers des banques."
        )
    return True, ""


def exiger_un_dossier() -> str:
    """Every working page starts with this. Stops the page if nothing is chosen yet."""
    racine = st.session_state.get("data_root", "")
    ok, probleme = etat_du_dossier(racine)
    if not ok:
        st.warning(probleme)
        st.page_link("Accueil.py", label="◀ Retour à l'accueil pour choisir un dossier",
                     icon="🏠")
        st.stop()
    return racine


def etapes(active: int) -> None:
    """A breadcrumb, so nobody has to remember where they are in the process."""
    noms = ["Fichiers", "Colonnes", "Consolidation"]
    morceaux = []
    for index, nom in enumerate(noms, start=1):
        if index < active:
            morceaux.append(f"~~{index}. {nom}~~")
        elif index == active:
            morceaux.append(f"**{index}. {nom}**")
        else:
            morceaux.append(f"{index}. {nom}")
    st.caption("  ▸  ".join(morceaux))


# Columns the pipeline attaches for traceability. Valuable in the audit file and in
# Power BI, but noise on screen for someone checking a month's sales.
COLONNES_TECHNIQUES = [
    "source_file", "source_sheet", "source_row", "ingested_at", "row_hash",
]

# Machine values that must never be shown raw to a reader.
VALEURS_LISIBLES = {
    "prime_totale_source": {"reported": "déclarée", "derived": "calculée"},
}

EN_TETES_LISIBLES = {
    "prime_totale_source": "Origine prime",
    "prime_nette_source": "Origine prime nette",
    "source_file": "Fichier d'origine",
    "source_sheet": "Feuille",
    "source_row": "Ligne",
    "ingested_at": "Traité le",
    "row_hash": "Identifiant",
}


def pour_affichage(frame, config, profil: str, technique: bool = False):
    """Turn the internal frame into something a person can read at a glance.

    Dates lose the meaningless "00:00:00", machine values become words, columns take
    their French labels, and the traceability columns are hidden unless asked for.
    """
    import pandas as pd

    sortie = frame.copy()
    if not technique:
        sortie = sortie.drop(columns=COLONNES_TECHNIQUES, errors="ignore")

    # Le code interne du produit ne veut rien dire pour un lecteur : on affiche son nom.
    if "produit_code" in sortie.columns:
        libelles = {c: config.produit(c).label for c in config.schema_.profiles}
        sortie["produit_code"] = sortie["produit_code"].map(
            lambda code: libelles.get(code, code)
        )

    for colonne in sortie.columns:
        if pd.api.types.is_datetime64_any_dtype(sortie[colonne]):
            sortie[colonne] = sortie[colonne].dt.strftime("%d/%m/%Y")
        remplacements = VALEURS_LISIBLES.get(colonne)
        if remplacements:
            sortie[colonne] = sortie[colonne].map(
                lambda valeur: remplacements.get(valeur, valeur)
            )

    # Une case vide doit rester vide : « None » à l'écran ressemble à une valeur.
    sortie = sortie.astype(object).where(sortie.notna(), "")

    etiquettes = dict(config.schema_.label_map(profil))
    etiquettes.update(EN_TETES_LISIBLES)
    return sortie.rename(columns=etiquettes)
