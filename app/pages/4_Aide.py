"""In-app help, written for someone who has never used the tool.

A guide kept in a file next to the program is a guide nobody reads. This one is one
click away from wherever they got stuck.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / "src"))

st.set_page_config(page_title="Aide", page_icon="❓", layout="wide")
st.title("Aide")

marche, problemes, questions = st.tabs(
    ["Comment ça marche", "Messages d'erreur", "Questions fréquentes"]
)

with marche:
    st.subheader("Chaque mois, en trois étapes")
    st.markdown(
        """
**Avant de commencer :** rangez le fichier reçu dans le dossier de sa banque,
par exemple `BNA 2025`. Mettez le mois dans le nom du fichier, par exemple
`Ventes_Mars_2025.xlsx`. C'est tout ce que l'outil demande.

---

#### 1 · Fichiers
L'outil lit les noms des dossiers et des fichiers pour savoir de quelle banque
et de quel mois il s'agit. Il n'ouvre encore rien : c'est instantané.

*Vous n'avez rien à faire ici si tout est vert.*

#### 2 · Colonnes
Chaque banque nomme ses colonnes à sa façon, et change souvent d'un mois à l'autre.
L'outil les reconnaît presque toujours seul. Quand il hésite, il vous montre la
colonne, ce qu'elle contient, et vous demande à quoi elle correspond.

*Votre réponse est retenue définitivement : la même colonne ne vous sera jamais
redemandée, quelle que soit la banque ou le mois.*

#### 3 · Consolidation
Vous voyez le résultat **avant** que quoi que ce soit soit enregistré : les ventes
retenues, ce qui a été retiré, ce qui mérite un coup d'œil, et les totaux par mois
à comparer avec les états des banques.

Si tout est correct, vous cliquez pour enregistrer. Sinon, vous ne cliquez pas :
rien n'aura été modifié.

---

#### Ensuite, dans Power BI
Ouvrez le fichier **`fact_ventes.parquet`** du dossier de la base.
Il contient toutes les banques et tous les mois, avec une colonne `banque`
pour filtrer.

Marquez `dim_date.parquet` comme table de dates : c'est ce qui permet les
comparaisons d'une année sur l'autre.
        """
    )

with problemes:
    st.subheader("Ce que veulent dire les messages")

    with st.expander("« Mois introuvable »"):
        st.markdown(
            "Le nom du fichier ne contient pas de mois lisible.\n\n"
            "**Que faire :** renommez le fichier en y mettant le mois — "
            "`Ventes_Mars_2025.xlsx`, `ventes_03_2025.xlsx` et `BNA mars 2025.xlsx` "
            "fonctionnent tous."
        )
    with st.expander("« Banque inconnue »"):
        st.markdown(
            "Le nom du dossier ne correspond à aucune banque enregistrée.\n\n"
            "**Que faire :** renommez le dossier avec le nom de la banque, par "
            "exemple `BNA 2025`. Si c'est une nouvelle banque partenaire, demandez "
            "qu'elle soit ajoutée à la configuration."
        )
    with st.expander("« Colonnes calculées non enregistrées »"):
        st.markdown(
            "Le fichier contient des formules (par exemple une prime totale calculée "
            "à partir de la prime nette et des frais), mais leur résultat n'a jamais "
            "été enregistré. Ces colonnes sont donc vides à la lecture.\n\n"
            "**Que faire :** ouvrez le fichier dans Excel, enregistrez-le avec "
            "**Ctrl+S**, refermez-le, et relancez.\n\n"
            "L'outil refuse volontairement ce fichier : le traiter tel quel ferait "
            "disparaître ces colonnes sans prévenir."
        )
    with st.expander("« Ancien format .xls »"):
        st.markdown(
            "Le fichier est dans l'ancien format Excel, que l'outil ne sait pas "
            "lire directement.\n\n"
            "**Que faire :** ouvrez-le dans Excel, puis "
            "**Fichier ▸ Enregistrer sous ▸ Classeur Excel (.xlsx)**. "
            "Traitez ensuite le nouveau fichier."
        )
    with st.expander("« Fichier illisible »"):
        st.markdown(
            "Excel n'arrive pas à ouvrir ce fichier. Il est abîmé, incomplet, ou ce "
            "n'est pas vraiment un fichier Excel.\n\n"
            "**Que faire :** essayez de l'ouvrir vous-même dans Excel. S'il ne "
            "s'ouvre pas non plus, redemandez-le à la banque."
        )
    with st.expander("« … est couvert par 2 fichiers »"):
        st.markdown(
            "Deux fichiers différents contiennent le même mois pour la même banque. "
            "C'est généralement une banque qui a renvoyé une version corrigée sous "
            "un nouveau nom.\n\n"
            "**Pourquoi c'est important :** si les deux sont enregistrés, les totaux "
            "de ce mois seront **comptés deux fois** dans tous vos tableaux de bord.\n\n"
            "**Que faire :** supprimez l'ancien fichier, ou remplacez-le par la "
            "version corrigée en gardant le même nom."
        )
    with st.expander("« … n'est pas cohérent avec les dates »"):
        st.markdown(
            "Le fichier s'appelle « mars » mais les ventes qu'il contient sont "
            "presque toutes d'un autre mois.\n\n"
            "**Que faire :** vérifiez que le fichier porte le bon nom. Si le nom est "
            "juste et les dates fausses, c'est à la banque de corriger."
        )
    with st.expander("« Colonnes obligatoires absentes »"):
        st.markdown(
            "Une colonne indispensable (numéro de contrat, date, prime) manque ou "
            "n'a pas été reconnue.\n\n"
            "**Que faire :** allez à l'étape **Colonnes** : la colonne est sans doute "
            "là mais sous un nom que l'outil ne connaît pas encore. Si elle est "
            "vraiment absente du fichier, redemandez-le à la banque."
        )

with questions:
    st.subheader("Questions fréquentes")

    with st.expander("Est-ce que mes données partent sur internet ?"):
        st.markdown(
            "**Non.** Tout se passe sur cet ordinateur. L'outil ne sait pas envoyer "
            "de données ailleurs : il refuse même de contacter autre chose que cette "
            "machine.\n\n"
            "L'assistant qui aide à reconnaître les noms de colonnes est lui aussi "
            "installé localement, et ne reçoit que des **noms de colonnes** — jamais "
            "de noms de clients ni de numéros de contrat."
        )
    with st.expander("Est-ce que je risque d'abîmer mes fichiers d'origine ?"):
        st.markdown(
            "**Non.** L'outil ouvre vos fichiers en lecture seulement. Il ne les "
            "modifie jamais, ne les déplace pas et ne les supprime pas. Tout ce "
            "qu'il produit va dans un dossier séparé."
        )
    with st.expander("Que se passe-t-il si je relance deux fois le même mois ?"):
        st.markdown(
            "Rien de fâcheux. Un fichier déjà enregistré est reconnu et ignoré. "
            "Si vous le modifiez et relancez, ses lignes sont **remplacées**, pas "
            "ajoutées une deuxième fois."
        )
    with st.expander("Une vente a été signalée. Est-ce qu'elle a été supprimée ?"):
        st.markdown(
            "**Non, jamais.** Un signalement vous demande de vérifier quelque chose ; "
            "la vente reste dans la base.\n\n"
            "Les seules choses retirées sont les lignes vides, les lignes TOTAL et "
            "les chiffres de brouillon — et chacune est listée dans l'onglet "
            "**Ce qui a été retiré**, avec sa position dans le fichier d'origine."
        )
    with st.expander("Une police a pris effet avant le mois du fichier. C'est normal ?"):
        st.markdown(
            "Oui, cela arrive : une police du mois de mars peut n'être déclarée "
            "qu'en mai. C'est pourquoi la base garde **deux dates** :\n\n"
            "- **Mois de réception** — le mois du fichier, votre période de reporting\n"
            "- **Date d'effet** — la date réelle de prise d'effet de la police\n\n"
            "L'écart entre les deux est signalé quand il est inhabituel, mais la "
            "vente est conservée."
        )
    with st.expander("D'où vient un chiffre que je vois dans Power BI ?"):
        st.markdown(
            "Chaque ligne de la base indique le fichier et le numéro de ligne d'où "
            "elle vient (`source_file`, `source_row`). Vous pouvez donc toujours "
            "remonter à la cellule d'origine.\n\n"
            "Le fichier `audit.xlsx` reprend, pour chaque fichier traité : les "
            "colonnes reconnues et comment, tout ce qui a été retiré et pourquoi, "
            "et les totaux par banque et par mois."
        )
    with st.expander("La prime totale a été « calculée ». Qu'est-ce que ça veut dire ?"):
        st.markdown(
            "Certaines banques donnent la prime globale directement ; d'autres ne "
            "donnent que la prime nette et les frais. Dans ce second cas l'outil "
            "fait l'addition, et le marque dans la colonne "
            "`prime_totale_source` :\n\n"
            "- `reported` — le chiffre vient de la banque\n"
            "- `derived` — le chiffre a été calculé par l'outil\n\n"
            "Un chiffre calculé n'est jamais présenté comme un chiffre reçu."
        )
    with st.expander("Comment ajouter une nouvelle banque partenaire ?"):
        st.markdown(
            "Créez un dossier à son nom (par exemple `AMEN 2025`) et demandez à la "
            "personne qui gère l'outil de l'ajouter à la configuration. "
            "C'est une ligne à écrire, pas un développement."
        )

st.divider()
st.caption(
    "Un problème qui n'est pas décrit ici ? Notez le message exact affiché à l'écran "
    "et le nom du fichier concerné : c'est ce qui permettra de vous aider."
)
