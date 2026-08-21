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

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / "src"))
sys.path.insert(0, str(RACINE / "app"))

from composants import etapes, exiger_un_dossier, pour_affichage   # noqa: E402

from cardif.audit import write_audit           # noqa: E402
from cardif.config import load_config          # noqa: E402
from cardif.filemeta import scan               # noqa: E402
from cardif.pipeline import commit, run        # noqa: E402
from cardif.store import SchemaContractError, Warehouse, WarehouseCorrupt   # noqa: E402
from cardif.validate import reconciliation_table          # noqa: E402

st.set_page_config(page_title="Consolidation", page_icon="📦", layout="wide")
st.title("3 · Consolider")
etapes(3)
st.caption(
    "L'aperçu montre le résultat sans rien enregistrer. Rien n'est ajouté à la base "
    "tant que vous n'avez pas cliqué sur le bouton de confirmation, en bas."
)

state = st.session_state
root = exiger_un_dossier()

config = load_config(state.get("config_dir", "config"))
warehouse_dir = state.get("warehouse_dir", "warehouse")
warehouse = Warehouse(config, warehouse_dir)

# Shown once after a save. The page reruns so the counters at the top are current
# rather than still showing the files as new.
confirmation = state.pop("dernier_enregistrement", None)
if confirmation:
    st.success(confirmation["message"])
    for note in confirmation.get("notes", []):
        st.info(note)
    st.markdown(confirmation["chemins"])
    st.page_link("Accueil.py", label="Retour à l'accueil", icon="🏠")
    st.divider()

metas = scan(Path(root), config.banks)
try:
    buckets = warehouse.pending([m.path for m in metas])
except WarehouseCorrupt as exc:
    st.error(str(exc))
    st.stop()
todo = buckets["new"] + buckets["changed"]

a, b, c = st.columns(3)
a.metric("Déjà dans la base", len(buckets["unchanged"]))
b.metric("Nouveaux", len(buckets["new"]))
c.metric("Modifiés depuis", len(buckets["changed"]))

everything = st.checkbox(
    "Tout retraiter depuis le début", value=not todo,
    help="Normalement inutile : seuls les fichiers nouveaux ou modifiés sont traités. "
         "Retraiter tout ne crée pas de doublons.",
)
selection = [m.path for m in metas] if everything else todo

if not selection:
    st.success(
        "Tous les fichiers de ce dossier sont déjà dans la base. "
        "Déposez le fichier du mois suivant, puis revenez ici."
    )
    st.stop()

if st.button(f"Voir le résultat ({len(selection)} fichier·s)", type="primary"):
    with st.spinner("Traitement…"):
        state["run"] = run(
            root, config,
            use_model=state.get("use_model", True),
            only=selection,
            overrides=state.get("overrides", {}),
        )

outcome = state.get("run")
if outcome is None:
    st.info("Cliquez sur le bouton ci-dessus. Rien ne sera enregistré à ce stade.")
    st.stop()

st.subheader("Résultat")
st.caption(outcome.summary_line())

compte_produits = outcome.produits_rencontres()
if compte_produits:
    st.caption(
        "Produits reconnus : "
        + " · ".join(
            f"{config.produit(code).label} ({nombre})"
            for code, nombre in sorted(compte_produits.items())
        )
    )

non_identifies = outcome.produits_non_identifies()
if non_identifies:
    st.warning(
        f"**{len(non_identifies)} fichier·s dont le produit n'a pas été reconnu.** "
        "Ils sont traités avec un format de repli qui ne garde que les colonnes "
        "communes : les colonnes propres au produit seraient perdues. Renommez-les "
        "en y mettant le nom du produit, ou indiquez-le à l'étape **Fichiers**."
    )
    for resultat in non_identifies[:5]:
        st.caption(f"· {Path(resultat.meta.path).name}")

if outcome.skipped:
    st.error(
        f"**{len(outcome.skipped)} fichier·s n'ont pas pu être traités.** "
        "Ils ne seront pas ajoutés à la base ; les autres le seront normalement."
    )
    for path, reason in outcome.skipped:
        with st.expander(f"❌ {Path(path).name}"):
            st.write(reason)

unresolved = outcome.unresolved_headers()
if unresolved:
    st.warning(
        f"**{len(unresolved)} colonne·s ne sont pas reconnues** : "
        + ", ".join(f"« {h} »" for h, _ in unresolved.most_common(6))
        + ". Ces colonnes seront vides dans la base. Passez par l'étape "
        "**Colonnes** pour dire à quoi elles correspondent."
    )
    st.page_link("pages/2_Colonnes.py", label="◀ Retour à l'étape 2 · Colonnes",
                 icon="🔤")

if outcome.frame is None or outcome.frame.empty:
    st.stop()

preview, flags_tab, reconciliation, dropped = st.tabs(
    ["Aperçu du résultat", "À vérifier", "Totaux par mois", "Ce qui a été retiré"]
)

with preview:
    # Each product has its own columns, so they are shown one at a time: stacking them
    # would give a table that is mostly empty and impossible to read.
    compte = outcome.produits_rencontres()
    codes = sorted(compte)
    if not codes:
        st.info("Aucune vente à afficher.")
    else:
        choix = st.selectbox(
            "Produit à afficher", codes,
            format_func=lambda c: f"{config.produit(c).label} — {compte[c]} fichier·s",
        )
        morceaux = [r.frame for r in outcome.ok_results if r.produit == choix]
        extrait = pd.concat(morceaux, ignore_index=True) if morceaux else pd.DataFrame()
        st.caption(
            f"{len(extrait)} ventes pour ce produit — les 200 premières sont affichées."
        )
        technique = st.checkbox(
            "Afficher aussi la provenance de chaque ligne",
            help="Le fichier et la ligne d'où vient chaque vente. Toujours enregistré "
                 "dans la base, même quand ce n'est pas affiché ici.",
        )
        st.dataframe(
            pour_affichage(extrait.head(200), config, choix, technique),
            use_container_width=True, hide_index=True,
        )

with flags_tab:
    report = outcome.validation
    if not report.flags:
        st.success("Rien d'anormal détecté.")
    else:
        a, b = st.columns(2)
        a.metric("À corriger", report.errors)
        b.metric("À regarder", report.warnings)
        st.caption(
            "**Les lignes signalées ne sont pas supprimées.** Un signalement vous "
            "demande de vérifier, il n'enlève jamais une vente de la base."
        )
        frame = report.to_frame()
        st.dataframe(
            frame.rename(columns={
                "severity": "niveau", "code": "type", "message": "explication",
                "source_file": "fichier", "source_row": "ligne", "field": "colonne",
            }).drop(columns=["row_hash"], errors="ignore"),
            use_container_width=True, hide_index=True,
        )

with reconciliation:
    st.caption(
        "Totaux par banque et par mois. **Comparez-les avec les états que les banques "
        "vous ont envoyés** : c'est la preuve que rien n'a été perdu en route."
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
    st.caption(
        "Lignes vides, lignes TOTAL et chiffres de brouillon retirés, avec leur "
        "position exacte dans le fichier d'origine si vous voulez vérifier."
    )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------------
st.divider()
st.subheader("Enregistrer dans la base")

errors = outcome.validation.errors
if errors:
    st.error(
        f"**{errors} point·s à corriger** avant d'enregistrer. Regardez l'onglet "
        "**À vérifier** ci-dessus : il explique chacun d'eux."
    )
    allow = st.checkbox("J'ai vérifié, enregistrer quand même", value=False)
else:
    allow = True
    st.caption("Tout est en ordre. Vous pouvez enregistrer.")

if st.button("Enregistrer dans la base", type="primary", disabled=not allow):
    try:
        summary = commit(outcome, config, warehouse_dir)
    except (SchemaContractError, WarehouseCorrupt) as exc:
        st.error(str(exc))
        st.stop()

    audit_path = Path(warehouse_dir) / "audit.xlsx"
    write_audit(audit_path, outcome.results, outcome.validation, outcome.frame)

    state["dernier_enregistrement"] = {
        "message": (
            f"**Enregistré.** {summary['written']} ventes ajoutées"
            + (f", dont {summary['replaced']} qui remplacent des versions précédentes"
               if summary["replaced"] else "")
            + f". La base contient maintenant {summary['total']} ventes."
        ),
        "notes": summary.get("skipped_xlsx", []),
        "chemins": (
            f"**Pour Power BI**, ouvrez ce fichier :\n\n"
            f"`{Path(warehouse_dir) / 'fact_ventes.parquet'}`\n\n"
            f"Le détail de ce qui a été nettoyé est dans `{audit_path}`."
        ),
    }
    state.pop("run", None)
    st.rerun()
