"""Step 3 — preview, then commit.

The preview runs the whole pipeline in memory and writes nothing. Committing is a
separate, deliberate action, and it appends: files already in the warehouse are skipped
unless their contents have changed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from cardif.audit import write_audit           # noqa: E402
from cardif.config import load_config          # noqa: E402
from cardif.filemeta import scan               # noqa: E402
from cardif.pipeline import commit, run        # noqa: E402
from cardif.store import SchemaContractError, Warehouse, WarehouseCorrupt   # noqa: E402
from cardif.validate import reconciliation_table          # noqa: E402

st.set_page_config(page_title="Consolidation", page_icon="📦", layout="wide")
st.title("3 · Consolidation")

state = st.session_state
root = state.get("data_root", "")
if not root or not Path(root).exists():
    st.warning("Indiquez un dossier de données valide dans la barre latérale.")
    st.stop()

config = load_config(state.get("config_dir", "config"))
profile_name = state.get("profile", config.settings.warehouse.profile)
warehouse_dir = state.get("warehouse_dir", "warehouse")
warehouse = Warehouse(config, warehouse_dir, profile_name)

metas = scan(Path(root), config.banks)
try:
    buckets = warehouse.pending([m.path for m in metas])
except WarehouseCorrupt as exc:
    st.error(str(exc))
    st.stop()
todo = buckets["new"] + buckets["changed"]

a, b, c = st.columns(3)
a.metric("Déjà intégrés", len(buckets["unchanged"]))
b.metric("Nouveaux", len(buckets["new"]))
c.metric("Modifiés", len(buckets["changed"]))

everything = st.checkbox(
    "Retraiter tous les fichiers", value=not todo,
    help="Par défaut seuls les fichiers nouveaux ou modifiés sont traités.",
)
selection = [m.path for m in metas] if everything else todo

if not selection:
    st.success("Rien de nouveau à traiter.")
    st.stop()

if st.button(f"Prévisualiser ({len(selection)} fichier·s)", type="primary"):
    with st.spinner("Traitement…"):
        state["run"] = run(
            root, config, profile_name,
            use_model=state.get("use_model", True),
            only=selection,
            overrides=state.get("overrides", {}),
        )

outcome = state.get("run")
if outcome is None:
    st.info("Lancez une prévisualisation. Rien n'est écrit à ce stade.")
    st.stop()

st.subheader("Résultat de la prévisualisation")
st.caption(outcome.summary_line())

if outcome.skipped:
    with st.expander(f"{len(outcome.skipped)} fichier(s) non traité(s)", expanded=True):
        for path, reason in outcome.skipped:
            st.error(f"**{Path(path).name}** — {reason}")

unresolved = outcome.unresolved_headers()
if unresolved:
    st.warning(
        f"{len(unresolved)} en-tête(s) non résolu(s) : "
        + ", ".join(f"« {h} » ×{n}" for h, n in unresolved.most_common(6))
        + ". Passez par la page **En-têtes** pour les trancher, sinon ces colonnes "
        "resteront vides."
    )

if outcome.frame is None or outcome.frame.empty:
    st.stop()

preview, flags_tab, reconciliation, dropped = st.tabs(
    ["Aperçu", "Anomalies", "Rapprochement", "Supprimé"]
)

with preview:
    st.caption(f"{len(outcome.frame)} lignes — 200 premières affichées")
    labels = config.schema_.label_map(profile_name)
    st.dataframe(
        outcome.frame.head(200).rename(columns=labels),
        use_container_width=True, hide_index=True,
    )

with flags_tab:
    report = outcome.validation
    if not report.flags:
        st.success("Aucune anomalie détectée.")
    else:
        a, b = st.columns(2)
        a.metric("Erreurs", report.errors)
        b.metric("Avertissements", report.warnings)
        st.caption(
            "Les lignes signalées restent dans la base : un signalement demande une "
            "vérification, il ne supprime rien."
        )
        st.dataframe(report.to_frame(), use_container_width=True, hide_index=True)

with reconciliation:
    st.caption(
        "Totaux par banque et par mois, à comparer avec les états transmis par "
        "chaque banque."
    )
    st.dataframe(
        reconciliation_table(outcome.frame), use_container_width=True, hide_index=True
    )

with dropped:
    rows = []
    for result in outcome.results:
        if result.table is None:
            continue
        for d in result.table.dropped_rows:
            rows.append({
                "fichier": Path(result.meta.path).name,
                "position": f"ligne {d.row}", "motif": d.reason, "contenu": d.preview,
            })
        for col in result.table.dropped_columns:
            rows.append({
                "fichier": Path(result.meta.path).name,
                "position": f"colonne {col.letter}", "motif": col.reason,
                "contenu": col.header,
            })
    st.caption("Tout ce qui a été retiré, avec sa position dans le fichier d'origine.")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------------
st.divider()
st.subheader("Écrire dans l'entrepôt")

errors = outcome.validation.errors
if errors:
    st.error(
        f"{errors} anomalie(s) de niveau erreur. Vérifiez-les dans l'onglet "
        "**Anomalies** avant d'écrire."
    )
allow = st.checkbox("Écrire malgré les erreurs", value=False, disabled=not errors)

if st.button("Confirmer et écrire", type="primary", disabled=bool(errors) and not allow):
    try:
        summary = commit(outcome, config, warehouse_dir)
    except (SchemaContractError, WarehouseCorrupt) as exc:
        st.error(str(exc))
        st.stop()

    audit_path = Path(warehouse_dir) / "audit.xlsx"
    write_audit(audit_path, outcome.results, outcome.validation, outcome.frame)

    st.success(
        f"{summary['written']} lignes écrites "
        f"(dont {summary['replaced']} remplacées). "
        f"L'entrepôt contient maintenant {summary['total']} lignes."
    )
    st.caption(f"Rapport d'audit : {audit_path}")
    st.caption(
        f"Pour Power BI : {Path(warehouse_dir) / 'fact_ventes.parquet'} "
        "avec dim_date, dim_banque et dim_produit."
    )
    state.pop("run", None)
