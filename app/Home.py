"""Cardif — consolidation of monthly bank sales workbooks.

Run with::

    streamlit run app/Home.py

Everything stays on this machine. The only network call the app can make is to the local
model endpoint, and that is refused unless it is on loopback.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cardif.config import load_config          # noqa: E402
from cardif.llm import LocalModel, OfflineViolation   # noqa: E402
from cardif.store import Warehouse             # noqa: E402

st.set_page_config(page_title="Cardif — Consolidation", page_icon="📊", layout="wide")


def get_config(config_dir: str):
    """Load config fresh each run so edits to aliases.yaml take effect immediately."""
    return load_config(config_dir)


def sidebar() -> dict:
    """Shared controls. Every page reads its settings from here."""
    st.sidebar.title("Cardif")
    st.sidebar.caption("Consolidation hors ligne des ventes bancaires")

    state = st.session_state
    state.setdefault("data_root", "")
    state.setdefault("config_dir", str(ROOT / "config"))
    state.setdefault("warehouse_dir", str(ROOT / "warehouse"))
    state.setdefault("overrides", {})

    state["data_root"] = st.sidebar.text_input(
        "Dossier des données", state["data_root"],
        help="Le dossier qui contient « BNA 2025 », « BIAT 2025 », …",
    )
    with st.sidebar.expander("Emplacements"):
        state["config_dir"] = st.text_input("Configuration", state["config_dir"])
        state["warehouse_dir"] = st.text_input("Entrepôt", state["warehouse_dir"])

    try:
        config = load_config(state["config_dir"])
    except Exception as exc:                      # noqa: BLE001 - shown to the user
        st.sidebar.error(f"Configuration illisible : {exc}")
        st.stop()

    profiles = list(config.schema_.profiles)
    default = config.settings.warehouse.profile
    state["profile"] = st.sidebar.selectbox(
        "Format de sortie", profiles,
        index=profiles.index(default) if default in profiles else 0,
        help="Les colonnes du fichier consolidé, définies dans schema.yaml",
    )
    st.sidebar.caption(
        "Colonnes : " + ", ".join(config.schema_.profiles[state["profile"]].columns)
    )

    state["use_model"] = st.sidebar.toggle(
        "Utiliser le modèle local", value=config.settings.llm.enabled,
        help="Seulement pour les en-têtes que les règles ne résolvent pas.",
    )

    if state["use_model"]:
        _model_status(config)

    return {"config": config, **state}


def _model_status(config) -> None:
    """Show whether LM Studio is actually reachable, and what would be sent to it."""
    try:
        model = LocalModel(config.settings.llm)
    except OfflineViolation as exc:
        st.sidebar.error(f"Point de terminaison refusé : {exc}")
        return

    if model.available():
        st.sidebar.success(f"Modèle disponible ({config.settings.llm.base_url})")
    else:
        st.sidebar.warning(
            f"Modèle injoignable ({config.settings.llm.base_url}). "
            "Le traitement continue sans lui."
        )
    if config.settings.llm.send_sample_values:
        st.sidebar.warning(
            "⚠️ L'envoi d'exemples de valeurs est activé : des données réelles "
            "sortent du tableau vers le modèle local."
        )
    else:
        st.sidebar.caption(
            "🔒 Seuls les en-têtes et un profil anonymisé sont envoyés au modèle."
        )


def main() -> None:
    context = sidebar()
    config = context["config"]

    st.title("Consolidation des ventes")
    st.markdown(
        "Déposez les fichiers de la banque dans son dossier, puis suivez les pages "
        "dans l'ordre : **Fichiers → En-têtes → Vérification → Consolidation**."
    )

    warehouse = Warehouse(config, context["warehouse_dir"], context["profile"])
    fact = warehouse.read_fact()

    left, middle, right = st.columns(3)
    left.metric("Lignes dans l'entrepôt", f"{len(fact):,}".replace(",", " "))
    middle.metric("Fichiers déjà intégrés", len(warehouse.manifest.files))
    right.metric(
        "Banques", fact["banque"].nunique() if not fact.empty else 0
    )

    if fact.empty:
        st.info(
            "L'entrepôt est vide. Commencez par la page **Fichiers** pour vérifier "
            "que chaque fichier est rattaché à la bonne banque et au bon mois."
        )
        return

    st.subheader("Contenu par banque et par mois")
    pivot = fact.pivot_table(
        index="banque", columns="mois_reception", values="row_hash",
        aggfunc="count", fill_value=0,
    )
    st.dataframe(pivot, use_container_width=True)

    if "prime_totale" in fact.columns:
        st.subheader("Prime totale par mois")
        monthly = (
            fact.groupby(["mois_reception", "banque"])["prime_totale"]
            .sum().unstack(fill_value=0)
        )
        st.bar_chart(monthly)


if __name__ == "__main__":
    main()
else:
    main()
