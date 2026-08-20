"""Cardif — consolidation des ventes mensuelles des banques partenaires.

Se lance avec le raccourci « Lancer Cardif », ou en ligne de commande ::

    streamlit run app/Accueil.py

Tout reste sur cet ordinateur. Aucune donnée n'est envoyée sur internet.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "src"))
sys.path.insert(0, str(RACINE / "app"))

from composants import compter_classeurs, etat_du_dossier, selecteur_de_dossier  # noqa: E402
from cardif.config import load_config                     # noqa: E402
from cardif.demo import creer_donnees_exemple             # noqa: E402
from cardif.llm import LocalModel, OfflineViolation       # noqa: E402
from cardif.store import Warehouse, WarehouseCorrupt      # noqa: E402

st.set_page_config(page_title="Cardif — Consolidation", page_icon="📊", layout="wide")


def barre_laterale(config):
    """Only the choices a normal user needs. The rest is folded away."""
    etat = st.session_state
    st.sidebar.title("Cardif")
    st.sidebar.caption("Consolidation des ventes · hors ligne")

    racine = etat.get("data_root", "")
    if racine:
        st.sidebar.success(f"📁 {Path(racine).name}")
        st.sidebar.caption(racine)
        if st.sidebar.button("Changer de dossier", use_container_width=True):
            etat.pop("data_root", None)
            for cle in ("run", "review_run", "inventory"):
                etat.pop(cle, None)
            st.rerun()

    profils = list(config.schema_.profiles)
    defaut = config.settings.warehouse.profile
    etiquettes = {
        "powerbi_2025": "Prime globale seulement",
        "detaille": "Détail des primes (nette + frais)",
    }
    etat["profile"] = st.sidebar.selectbox(
        "Colonnes à produire", profils,
        index=profils.index(defaut) if defaut in profils else 0,
        format_func=lambda nom: etiquettes.get(nom, nom),
        help="Ce que contiendra le fichier consolidé.",
    )
    st.sidebar.caption(
        "→ " + ", ".join(config.schema_.label_map(etat["profile"]).values())
    )

    with st.sidebar.expander("Options avancées"):
        etat["use_model"] = st.toggle(
            "Assistant local pour les colonnes inconnues",
            value=config.settings.llm.enabled,
            help="Uniquement pour les noms de colonnes que les règles ne "
                 "reconnaissent pas. Fonctionne sans internet.",
        )
        etat["warehouse_dir"] = st.text_input(
            "Dossier de la base consolidée", etat.get("warehouse_dir", str(RACINE / "warehouse"))
        )
        etat["config_dir"] = st.text_input(
            "Dossier de configuration", etat.get("config_dir", str(RACINE / "config"))
        )

    if etat.get("use_model"):
        _etat_du_modele(config)

    st.sidebar.divider()
    st.sidebar.caption("🔒 Aucune donnée ne quitte cet ordinateur.")


def _etat_du_modele(config) -> None:
    try:
        modele = LocalModel(config.settings.llm)
    except OfflineViolation:
        st.sidebar.error(
            "L'assistant est configuré vers un serveur distant. "
            "Par sécurité il est désactivé."
        )
        return
    if modele.available():
        st.sidebar.caption("🤖 Assistant local : disponible")
    else:
        st.sidebar.caption("🤖 Assistant local : éteint (ce n'est pas un problème)")


def accueil_premiere_fois() -> None:
    """What someone sees before they have chosen anything."""
    st.title("Bienvenue")
    st.markdown(
        "Cet outil rassemble les fichiers Excel envoyés chaque mois par les banques "
        "en **une seule base propre**, prête pour Power BI.\n\n"
        "Il nettoie les fichiers à votre place : lignes vides en haut, chiffres de "
        "brouillon, lignes TOTAL en bas, noms de colonnes différents d'une banque et "
        "d'un mois à l'autre."
    )

    essai, reel = st.tabs(["🎓 Essayer d'abord", "📂 Utiliser mes fichiers"])

    with essai:
        st.markdown(
            "Créez des **fichiers d'exemple** et faites tourner l'outil dessus. "
            "Ce sont de faux fichiers, avec les mêmes défauts que les vrais : "
            "rien de confidentiel, rien à craindre."
        )
        destination = Path.home() / "Documents" / "Cardif-exemple"
        st.caption(f"Ils seront créés dans : {destination}")
        if st.button("Créer les fichiers d'exemple", type="primary"):
            with st.spinner("Création des fichiers…"):
                resume = creer_donnees_exemple(destination)
            st.session_state["data_root"] = resume["dossier"]
            st.success(
                f"{resume['fichiers']} fichiers créés pour {resume['banques']} banques "
                f"({resume['ventes']} ventes)."
            )
            st.rerun()

    with reel:
        st.markdown(
            "Choisissez le dossier qui contient **les dossiers des banques**, "
            "par exemple un dossier qui contient « BNA 2025 », « BIAT 2025 »…"
        )
        if selecteur_de_dossier():
            st.rerun()


def accueil_normal(config) -> None:
    racine = st.session_state["data_root"]
    st.title("Consolidation des ventes")

    entrepot = Warehouse(
        config, st.session_state.get("warehouse_dir", str(RACINE / "warehouse")),
        st.session_state.get("profile"),
    )
    try:
        faits = entrepot.read_fact()
    except WarehouseCorrupt as exc:
        st.error(str(exc))
        st.stop()

    gauche, milieu, droite = st.columns(3)
    gauche.metric("Fichiers dans le dossier", compter_classeurs(Path(racine)))
    milieu.metric("Ventes déjà consolidées", f"{len(faits):,}".replace(",", " "))
    droite.metric("Banques", faits["banque"].nunique() if not faits.empty else 0)

    if faits.empty:
        st.info(
            "**Rien n'a encore été consolidé.** Suivez les trois pages dans l'ordre, "
            "à gauche : d'abord **Fichiers**, puis **Colonnes**, puis "
            "**Consolidation**."
        )
        st.page_link("pages/1_Fichiers.py", label="Commencer par les fichiers ▶",
                     icon="📁")
        return

    st.page_link("pages/3_Consolidation.py", label="Ajouter un nouveau mois ▶",
                 icon="📦")
    st.divider()

    st.subheader("Ce que contient la base")
    tableau = faits.pivot_table(
        index="banque", columns="mois_reception", values="row_hash",
        aggfunc="count", fill_value=0,
    )
    st.dataframe(tableau, use_container_width=True)

    if "prime_totale" in faits.columns:
        st.subheader("Prime totale par mois")
        st.bar_chart(
            faits.groupby(["mois_reception", "banque"])["prime_totale"]
            .sum().unstack(fill_value=0)
        )

    with st.expander("Où sont les fichiers pour Power BI ?"):
        chemin = entrepot.root
        st.markdown(
            f"Dans le dossier `{chemin}` :\n\n"
            f"- **`fact_ventes.parquet`** — toutes les banques ensemble. "
            "C'est ce fichier qu'il faut ouvrir dans Power BI.\n"
            "- `dim_date.parquet` — le calendrier, à marquer comme table de dates "
            "dans Power BI.\n"
            "- `par_banque/` — un fichier Excel et un fichier Parquet par banque, "
            "si vous devez en transmettre un à une banque.\n"
            "- `audit.xlsx` — le détail de tout ce qui a été nettoyé, gardé ou "
            "signalé."
        )


def main() -> None:
    etat = st.session_state
    etat.setdefault("config_dir", str(RACINE / "config"))
    etat.setdefault("warehouse_dir", str(RACINE / "warehouse"))
    etat.setdefault("overrides", {})

    try:
        config = load_config(etat["config_dir"])
    except Exception as exc:                      # noqa: BLE001 - shown to the user
        st.error(
            "La configuration de l'outil est illisible. Prévenez la personne qui "
            f"l'a installé.\n\nDétail technique : {exc}"
        )
        st.stop()

    barre_laterale(config)

    racine = etat.get("data_root", "")
    if not etat_du_dossier(racine)[0]:
        etat.pop("data_root", None)
        accueil_premiere_fois()
    else:
        accueil_normal(config)


main()
