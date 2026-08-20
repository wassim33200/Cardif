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

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / "src"))
sys.path.insert(0, str(RACINE / "app"))

from composants import etapes, exiger_un_dossier   # noqa: E402

from cardif.config import load_config          # noqa: E402
from cardif.filemeta import scan               # noqa: E402
from cardif.llm import LocalModel              # noqa: E402
from cardif.store import Warehouse, WarehouseCorrupt   # noqa: E402

st.set_page_config(page_title="Fichiers", page_icon="📁", layout="wide")
st.title("1 · Vérifier les fichiers")
etapes(1)
st.caption(
    "L'outil lit le nom de chaque dossier pour savoir de quelle banque il s'agit, "
    "et le nom de chaque fichier pour savoir de quel mois. Rien n'est encore ouvert."
)

state = st.session_state
root = exiger_un_dossier()

config = load_config(state.get("config_dir", "config"))
metas = scan(Path(root), config.banks)
if not metas:
    st.error("Aucun fichier Excel trouvé dans ce dossier.")
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
a.metric("Fichiers trouvés", len(metas))
b.metric("Nouveaux à traiter", len(new_or_changed))
c.metric("Problèmes", len(unresolved))

if not unresolved:
    st.success(
        "Chaque fichier est bien rattaché à une banque et à un mois. "
        "Vous pouvez passer à l'étape suivante."
    )
    st.page_link("pages/2_Colonnes.py", label="Étape 2 · Colonnes ▶", icon="🔤")

with st.expander("Voir le détail des fichiers", expanded=bool(unresolved)):
    st.dataframe(frame, use_container_width=True, hide_index=True)

if unresolved:
    st.subheader(f"{len(unresolved)} fichier·s à corriger")
    st.warning(
        "Pour ces fichiers, le nom ne permet pas de savoir de quelle banque ou de "
        "quel mois il s'agit. **Le plus simple est de renommer le fichier** en y "
        "mettant le mois, par exemple « Ventes_Mars_2025.xlsx », puis de recharger "
        "cette page. Sinon, indiquez-le à la main ci-dessous."
    )

    if state.get("use_model") and st.button("Demander à l'assistant de deviner le mois"):
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
                st.info(
                    "L'assistant local n'est pas démarré. Indiquez le mois à la main "
                    "ci-dessous, ou renommez les fichiers."
                )
        except Exception:                         # noqa: BLE001
            st.info(
                "L'assistant local n'est pas disponible. Indiquez le mois à la main "
                "ci-dessous, ou renommez les fichiers."
            )

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
    st.caption(
        "Ces corrections valent pour cette session. Pour qu'elles tiennent d'un mois "
        "sur l'autre, renommez le fichier."
    )
    st.page_link("pages/2_Colonnes.py", label="Étape 2 · Colonnes ▶", icon="🔤")
