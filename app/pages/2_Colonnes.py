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

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / "src"))
sys.path.insert(0, str(RACINE / "app"))

from composants import etapes, exiger_un_dossier   # noqa: E402

from cardif.config import load_config          # noqa: E402
from cardif.pipeline import profile_headers, run  # noqa: E402

st.set_page_config(page_title="Colonnes", page_icon="🔤", layout="wide")
st.title("2 · Reconnaître les colonnes")
etapes(2)
st.caption(
    "Chaque banque nomme ses colonnes à sa façon. L'outil les reconnaît tout seul "
    "dans la grande majorité des cas ; il ne vous demande que ce dont il n'est pas sûr."
)

state = st.session_state
root = exiger_un_dossier()

config = load_config(state.get("config_dir", "config"))

review_tab, inventory_tab = st.tabs(["À vérifier", "Toutes les colonnes"])

# ---------------------------------------------------------------------------------
with inventory_tab:
    st.caption(
        "Tous les noms de colonnes rencontrés, toutes banques et tous mois confondus, "
        "avec ce que l'outil en a compris. Utile pour vérifier, pas nécessaire au "
        "quotidien."
    )
    if st.button("Analyser toutes les colonnes"):
        with st.spinner("Lecture de chaque classeur…"):
            state["inventory"] = profile_headers(root, config)

    inventory = state.get("inventory")
    if inventory is not None and not inventory.empty:
        known = inventory[inventory["champ_propose"] != "(non résolu)"]
        unknown = inventory[inventory["champ_propose"] == "(non résolu)"]

        a, b, c = st.columns(3)
        a.metric("Noms de colonnes différents", len(inventory))
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

    if st.button("Vérifier les colonnes", type="primary"):
        with st.spinner("Traitement…"):
            state["review_run"] = run(
                root, config,
                use_model=state.get("use_model", True),
                overrides=state.get("overrides", {}),
            )

    outcome = state.get("review_run")
    if outcome is None:
        st.info("Cliquez sur **Vérifier les colonnes** pour commencer.")
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
            "Toutes les colonnes ont été reconnues. Rien à faire ici : passez à "
            "l'étape suivante."
        )
        st.page_link("pages/3_Consolidation.py", label="Étape 3 · Consolidation ▶",
                     icon="📦")
        st.stop()

    st.warning(
        f"**{len(pending)} colonne·s à confirmer.** Dites à quoi elles correspondent : "
        "l'outil s'en souviendra et ne vous le redemandera plus jamais."
    )
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

            if st.button("Confirmer", key=f"save_{normalized}", type="primary"):
                target = "__ignore__" if choice.startswith("(") else choice
                if target != "__ignore__":
                    bank = None
                    if scope.startswith("seulement") and len(entry["banks"]) == 1:
                        bank = next(iter(entry["banks"]))
                    config.aliases.learn(normalized, target, bank)
                    config.save_aliases()
                    st.success(
                        f"« {entry['header']} » enregistrée. "
                        "Elle sera reconnue automatiquement les prochaines fois."
                    )
                else:
                    state.setdefault("overrides", {})[normalized] = "__ignore__"
                    st.info(f"« {entry['header']} » sera ignorée.")
                state.pop("review_run", None)
                st.rerun()
