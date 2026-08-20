"""Step 1 — what the folders and filenames say, before any file is opened.

Reads only paths, so it is instant even on a full year of every bank. The point is to
catch a mislabelled folder or an unreadable filename here, rather than discovering it
after processing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from cardif.config import load_config          # noqa: E402
from cardif.filemeta import scan               # noqa: E402
from cardif.llm import LocalModel              # noqa: E402
from cardif.store import Warehouse, WarehouseCorrupt   # noqa: E402

st.set_page_config(page_title="Fichiers", page_icon="📁", layout="wide")
st.title("1 · Fichiers détectés")

state = st.session_state
root = state.get("data_root", "")
if not root or not Path(root).exists():
    st.warning("Indiquez un dossier de données valide dans la barre latérale.")
    st.stop()

config = load_config(state.get("config_dir", "config"))
metas = scan(Path(root), config.banks)
if not metas:
    st.error(f"Aucun classeur trouvé sous {root}")
    st.stop()

warehouse = Warehouse(config, state.get("warehouse_dir", "warehouse"), state.get("profile"))
try:
    status = {str(m.path): warehouse.manifest.status(m.path) for m in metas}
except WarehouseCorrupt as exc:
    st.error(str(exc))
    st.stop()

STATUS_LABEL = {"new": "nouveau", "changed": "modifié", "unchanged": "déjà intégré"}

rows = [{
    "fichier": m.path.name,
    "dossier": m.path.parent.name,
    "banque": m.bank_code or "—",
    "mois": m.period or "—",
    "détecté par": {
        "numeric": "motif numérique", "month_name": "nom de mois",
        "model": "modèle local", "unresolved": "non résolu",
    }.get(m.period_method, m.period_method),
    "état": STATUS_LABEL[status[str(m.path)]],
    "remarques": " ; ".join(m.notes),
} for m in metas]
frame = pd.DataFrame(rows)

unresolved = [m for m in metas if not m.resolved]
new_or_changed = [m for m in metas if status[str(m.path)] != "unchanged"]

a, b, c = st.columns(3)
a.metric("Classeurs", len(metas))
b.metric("À traiter", len(new_or_changed))
c.metric("Non résolus", len(unresolved), delta=None if not unresolved else "à corriger")

st.dataframe(frame, use_container_width=True, hide_index=True)

if unresolved:
    st.subheader("Fichiers à rattacher manuellement")
    st.caption(
        "Le nom du fichier ou du dossier ne permet pas de déduire la banque ou le mois. "
        "Corrigez-les ici ; le modèle local peut proposer une lecture du nom de fichier."
    )

    if state.get("use_model") and st.button("Proposer une période avec le modèle local"):
        try:
            model = LocalModel(config.settings.llm)
            if model.available():
                for meta in unresolved:
                    if meta.period is None:
                        answer = model.resolve_period(meta.path.name)
                        if answer.value:
                            state.setdefault("period_overrides", {})[str(meta.path)] = answer.value
                            st.info(f"{meta.path.name} → {answer.value} ({answer.reason})")
            else:
                st.warning("Modèle injoignable.")
        except Exception as exc:                  # noqa: BLE001
            st.error(f"Modèle indisponible : {exc}")

    for meta in unresolved:
        with st.expander(meta.path.name, expanded=True):
            left, right = st.columns(2)
            codes = ["—"] + [b.code for b in config.banks.banks]
            current = meta.bank_code or "—"
            chosen = left.selectbox(
                "Banque", codes, index=codes.index(current) if current in codes else 0,
                key=f"bank_{meta.path}",
            )
            suggested = state.get("period_overrides", {}).get(str(meta.path), meta.period or "")
            period = right.text_input(
                "Mois (AAAA-MM)", suggested, key=f"period_{meta.path}",
                placeholder="2025-03",
            )
            if chosen != "—" and period:
                state.setdefault("file_overrides", {})[str(meta.path)] = {
                    "bank": chosen, "period": period,
                }
    st.info(
        "Les corrections saisies ici s'appliquent au traitement. Pour qu'elles soient "
        "permanentes, renommez le fichier ou ajoutez la banque dans `banks.yaml`."
    )
else:
    st.success("Chaque fichier est rattaché à une banque et à un mois.")
