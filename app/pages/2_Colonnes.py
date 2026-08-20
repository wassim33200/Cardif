"""Step 2 — the header inventory and the review screen.

This is the only page where the user really has to think, so it is organised by how much
thought each header needs: the ones already known are collapsed away, and what is left is
either a suggestion to confirm or a genuine unknown.

Every decision made here is written to `aliases.yaml`, which is why this page gets
emptier every month.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from cardif.config import load_config          # noqa: E402
from cardif.pipeline import profile_headers, run  # noqa: E402

st.set_page_config(page_title="En-têtes", page_icon="🔤", layout="wide")
st.title("2 · En-têtes des colonnes")

state = st.session_state
root = state.get("data_root", "")
if not root or not Path(root).exists():
    st.warning("Indiquez un dossier de données valide dans la barre latérale.")
    st.stop()

config = load_config(state.get("config_dir", "config"))
profile_name = state.get("profile", config.settings.warehouse.profile)

inventory_tab, review_tab = st.tabs(["Inventaire", "À décider"])

# ---------------------------------------------------------------------------------
with inventory_tab:
    st.caption(
        "Toutes les variantes d'en-tête rencontrées, toutes banques et tous mois "
        "confondus. C'est à partir de cette liste que l'on choisit les colonnes "
        "du fichier consolidé."
    )
    if st.button("Analyser les en-têtes", type="primary"):
        with st.spinner("Lecture de chaque classeur…"):
            state["inventory"] = profile_headers(root, config)

    inventory = state.get("inventory")
    if inventory is not None and not inventory.empty:
        known = inventory[inventory["champ_propose"] != "(non résolu)"]
        unknown = inventory[inventory["champ_propose"] == "(non résolu)"]

        a, b, c = st.columns(3)
        a.metric("En-têtes distincts", len(inventory))
        b.metric("Reconnus", len(known))
        c.metric("Inconnus", len(unknown))

        st.dataframe(
            inventory[["variantes", "banques", "fichiers", "champ_propose",
                       "methode", "exemples"]],
            use_container_width=True, hide_index=True,
        )
        st.download_button(
            "Télécharger l'inventaire (CSV)",
            inventory.to_csv(index=False).encode("utf-8"),
            file_name="inventaire_entetes.csv", mime="text/csv",
        )

# ---------------------------------------------------------------------------------
with review_tab:
    st.caption(
        "Les en-têtes que les règles n'ont pas tranchés, et ceux dont le contenu "
        "ne correspond pas au champ proposé."
    )

    if st.button("Chercher les en-têtes à décider", type="primary"):
        with st.spinner("Traitement…"):
            state["review_run"] = run(
                root, config, profile_name,
                use_model=state.get("use_model", True),
                overrides=state.get("overrides", {}),
            )

    outcome = state.get("review_run")
    if outcome is None:
        st.info("Lancez la recherche pour voir ce qui reste à décider.")
        st.stop()

    # Collect one entry per distinct header, with the files it appears in.
    pending: dict[str, dict] = {}
    for result in outcome.results:
        for mapping in result.mappings:
            if not mapping.normalized or not mapping.needs_review:
                continue
            entry = pending.setdefault(mapping.normalized, {
                "header": mapping.header,
                "mapping": mapping,
                "banks": set(),
                "files": 0,
            })
            entry["banks"].add(result.meta.bank_code or "?")
            entry["files"] += 1

    if not pending:
        st.success(
            "Rien à décider : tous les en-têtes sont résolus et cohérents avec "
            "leur contenu."
        )
        st.stop()

    st.warning(f"{len(pending)} en-tête(s) à confirmer.")
    fields = list(config.schema_.fields)

    for normalized, entry in sorted(pending.items(), key=lambda kv: -kv[1]["files"]):
        mapping = entry["mapping"]
        banks = ", ".join(sorted(entry["banks"]))
        label = f"« {entry['header']} » — {entry['files']} fichier(s) · {banks}"

        with st.expander(label, expanded=len(pending) <= 5):
            if mapping.profile:
                st.caption("Contenu observé : " + mapping.profile.describe())
                if mapping.profile.samples:
                    st.caption("Exemples : " + ", ".join(mapping.profile.samples[:5]))

            for warning in mapping.warnings:
                st.warning(warning)
            if mapping.reason:
                st.caption(f"Analyse : {mapping.reason}")

            options = ["(ignorer cette colonne)"] + fields
            default = 0
            if mapping.suggestions:
                best = mapping.suggestions[0][0]
                if best in fields:
                    default = options.index(best)
                    st.info(
                        f"Suggestion : **{best}** "
                        f"({mapping.suggestions[0][1]:.0f} %)"
                    )
            elif mapping.canonical in fields:
                default = options.index(mapping.canonical)

            choice = st.selectbox(
                "Ce champ correspond à", options, index=default,
                key=f"choice_{normalized}",
                format_func=lambda name: (
                    name if name.startswith("(")
                    else f"{name} — {config.schema_.fields[name].label}"
                ),
            )
            scope = st.radio(
                "Appliquer", ["à toutes les banques", f"seulement à {banks}"],
                key=f"scope_{normalized}", horizontal=True,
                disabled=len(entry["banks"]) != 1,
            )

            if st.button("Enregistrer", key=f"save_{normalized}"):
                target = "__ignore__" if choice.startswith("(") else choice
                if target != "__ignore__":
                    bank = None
                    if scope.startswith("seulement") and len(entry["banks"]) == 1:
                        bank = next(iter(entry["banks"]))
                    config.aliases.learn(normalized, target, bank)
                    config.save_aliases()
                    st.success(
                        f"« {entry['header']} » → {target}. "
                        "Cette décision est enregistrée ; elle ne sera plus redemandée."
                    )
                else:
                    state.setdefault("overrides", {})[normalized] = "__ignore__"
                    st.info(f"« {entry['header']} » sera ignorée.")
                state.pop("review_run", None)
                st.rerun()
