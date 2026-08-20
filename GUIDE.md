# Cardif — guide d'utilisation

Cet outil rassemble les fichiers Excel envoyés chaque mois par les banques
partenaires en **une seule base propre**, prête pour Power BI.

Il fait à votre place le nettoyage que vous faisiez à la main : les lignes vides
en haut du fichier, les chiffres de brouillon oubliés, la ligne TOTAL en bas, et
les noms de colonnes qui changent d'une banque et d'un mois à l'autre.

**Rien ne part sur internet.** Tout se passe sur votre ordinateur.

---

## La première fois

### Installation (une seule fois)

- **Windows** : double-cliquez sur **`Installer.bat`**, attendez le message
  « Installation terminée », fermez la fenêtre.
- **Mac** : double-cliquez sur **`installer.command`**.

Si un message dit que Python n'est pas installé, téléchargez-le sur
[python.org/downloads](https://www.python.org/downloads/). Sur Windows, cochez
**« Add Python to PATH »** pendant l'installation, sinon rien ne fonctionnera.

### Essayer sans risque

Lancez l'outil (voir ci-dessous) et cliquez sur **« Créer les fichiers
d'exemple »**. L'outil fabrique de faux fichiers de banques — avec les mêmes
défauts que les vrais — et vous pouvez dérouler tout le processus dessus.
Aucune donnée réelle, rien à craindre.

Ouvrez-en un dans Excel : vous verrez exactement le genre de fichier que l'outil
sait traiter.

---

## Chaque mois

### Lancer l'outil

- **Windows** : double-cliquez sur **`Lancer Cardif.bat`**
- **Mac** : double-cliquez sur **`lancer-cardif.command`**

Une fenêtre noire s'ouvre, puis une page dans votre navigateur.
**Ne fermez pas la fenêtre noire** tant que vous utilisez l'outil : c'est elle
qui le fait tourner. Pour arrêter, fermez-la.

### Ranger le fichier reçu

Deux règles, et c'est tout :

1. **Un dossier par banque**, avec le nom de la banque dedans : `BNA 2025`
2. **Le mois dans le nom du fichier** : `Ventes_Mars_2025.xlsx`

```
Ventes 2025/
├── BNA 2025/
│   ├── Ventes_Janvier_2025.xlsx
│   └── Ventes_Fevrier_2025.xlsx
└── BIAT 2025/
    └── ventes mars 2025.xlsx
```

Peu importe la façon d'écrire le mois : `Mars`, `mars`, `03`, `2025-03`
fonctionnent tous. Le reste du nom n'a aucune importance.

### Les trois étapes

**1 · Fichiers** — l'outil vérifie qu'il sait à quelle banque et à quel mois
correspond chaque fichier. Si tout est vert, vous n'avez rien à faire.

**2 · Colonnes** — l'outil reconnaît les colonnes malgré les changements de
nom. Quand il hésite, il vous montre la colonne et ce qu'elle contient, et vous
demande à quoi elle correspond.

> Votre réponse est retenue **définitivement**. La même colonne ne vous sera
> jamais redemandée. C'est pourquoi cette étape devient de plus en plus rapide
> avec les mois.

**3 · Consolidation** — vous voyez le résultat **avant** que quoi que ce soit
soit enregistré. Vérifiez l'onglet **Totaux par mois** contre les états envoyés
par les banques, puis cliquez pour enregistrer.

Tant que vous n'avez pas cliqué, rien n'a été modifié.

### Dans Power BI

Ouvrez le fichier **`fact_ventes.parquet`** du dossier de la base. Il contient
toutes les banques et tous les mois, avec une colonne `banque` pour filtrer.

Marquez `dim_date.parquet` comme table de dates : c'est ce qui permet les
comparaisons d'une année sur l'autre.

---

## Ce que l'outil ne fait jamais

- **Il ne modifie pas vos fichiers d'origine.** Il les lit, c'est tout.
- **Il ne supprime pas une vente.** Une vente signalée reste dans la base ; le
  signalement vous demande simplement de vérifier quelque chose.
- **Il n'invente pas de chiffre.** Quand il calcule une prime totale à partir de
  la prime nette et des frais, il l'indique dans la colonne
  `prime_totale_source` (`derived` au lieu de `reported`).
- **Il ne devine pas quand il n'est pas sûr.** Il préfère vous demander.

Tout ce qui a été retiré — lignes vides, lignes TOTAL, chiffres de brouillon —
est listé dans `audit.xlsx` avec sa position exacte dans le fichier d'origine.

---

## Si quelque chose ne va pas

L'onglet **Aide** dans l'outil explique chaque message d'erreur et ce qu'il faut
faire. Les trois plus fréquents :

| Message | Que faire |
|---|---|
| **Mois introuvable** | Renommez le fichier en y mettant le mois |
| **Banque inconnue** | Renommez le dossier avec le nom de la banque |
| **Colonnes calculées non enregistrées** | Ouvrez le fichier dans Excel, faites Ctrl+S, refermez |
| **Ancien format .xls** | Ouvrez-le dans Excel, Enregistrer sous ▸ Classeur Excel (.xlsx) |

Le dernier mérite une explication : quand une banque calcule la prime totale par
une formule, Excel n'enregistre parfois pas le résultat. Le fichier s'ouvre
normalement mais la colonne est vide pour tout programme qui la lit. L'outil
refuse volontairement ces fichiers plutôt que de produire une base à laquelle il
manque les primes.

**Pour signaler un problème**, notez le message exact affiché et le nom du
fichier concerné.

---

## Deux dates, et pourquoi

La base contient deux dates qui se ressemblent mais ne disent pas la même chose :

- **Mois de réception** — le mois du fichier, votre période de reporting
- **Date d'effet** — la date réelle de prise d'effet de la police

Une police prenant effet le 12 mars peut n'être déclarée que dans le fichier de
mai. Garder les deux permet de mesurer ce décalage au lieu de le masquer.
