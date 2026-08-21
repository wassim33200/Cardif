"""Products: detection, per-product schemas, and the separation between them.

Every bank sells several products, and each product carries its own fields. These tests
guard the two things that follow from that: the product must be read from the path
before anything is opened, and one product's shape must never leak into another's.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import gen_hostile
from cardif.filemeta import read_meta, resolve_produit, scan
from cardif.pipeline import commit, run
from cardif.store import Warehouse


class TestDetectionDuProduit:
    @pytest.mark.parametrize(
        "chemin,banque,attendu",
        [
            ("CNEP 2025/ADE_Immobilier_Mars_2025.xlsx", "CNEP", "ade_immobilier"),
            ("CNEP 2025/SAHTI_Mars_2025.xlsx", "CNEP", "sahti"),
            ("CNEP 2025/CTP mars 2025.xlsx", "CNEP", "cnep_total_prevoyance"),
            ("CNEP 2025/RIHLATI mars.xlsx", "CNEP", "rihlati"),
            ("CNEP 2025/ade amenagement mars.xlsx", "CNEP", "ade_amenagement"),
            ("BNPPED 2025/ADE credit automobile 03-2025.xlsx", "BNPPED", "ade_automobile"),
            ("BNPPED 2025/Assurcompte_2025_03.xlsx", "BNPPED", "assurcompte"),
            ("BNPPED 2025/Protection Optimale mars.xlsx", "BNPPED", "protection_optimale"),
            ("BNPPED 2025/carte visa mars 2025.xlsx", "BNPPED", "voyage_visa"),
        ],
    )
    def test_depuis_le_nom_du_fichier(self, config, chemin, banque, attendu):
        code, _, methode = resolve_produit(Path(chemin), config.schema_, banque)
        assert code == attendu
        assert methode == "nom_fichier"

    def test_depuis_un_sous_dossier(self, config):
        """Some banks file by product folder rather than by filename."""
        chemin = Path("CNEP 2025/SAHTI/ventes mars 2025.xlsx")
        code, _, _ = resolve_produit(chemin, config.schema_, "CNEP")
        assert code == "sahti"

    def test_le_mot_le_plus_long_gagne(self, config):
        """"ADE immobilier" must not be shadowed by the shorter "ADE"."""
        code, _, _ = resolve_produit(
            Path("BNPPED 2025/ADE immobilier mars.xlsx"), config.schema_, "BNPPED"
        )
        assert code == "ade_immobilier"

    def test_produit_inconnu_reste_non_identifie(self, config):
        code, _, methode = resolve_produit(
            Path("CNEP 2025/ventes mars 2025.xlsx"), config.schema_, "CNEP"
        )
        assert code is None
        assert methode == "unresolved"

    def test_seuls_les_produits_de_la_banque_sont_candidats(self, config):
        """SAHTI is a CNEP product; a BNPPED file must not resolve to it."""
        candidats = config.schema_.produits_du_partenaire("BNPPED")
        assert "sahti" not in candidats
        assert "sahti" in config.schema_.produits_du_partenaire("CNEP")

    def test_le_meme_produit_chez_deux_banques(self, config):
        """ADE immobilier is sold by both partners and must resolve for each."""
        for banque in ("CNEP", "BNPPED"):
            code, _, _ = resolve_produit(
                Path(f"{banque} 2025/ADE_Immobilier_Mars_2025.xlsx"),
                config.schema_, banque,
            )
            assert code == "ade_immobilier"

    def test_le_produit_est_lu_sans_ouvrir_le_fichier(self, tmp_path, config):
        chemin = tmp_path / "CNEP 2025" / "SAHTI_Mars_2025.xlsx"
        chemin.parent.mkdir(parents=True)
        chemin.write_bytes(b"pas un classeur du tout")
        meta = read_meta(chemin, config.banks, config.schema_)
        assert meta.produit == "sahti"
        assert meta.bank_code == "CNEP"
        assert meta.period == "2025-03"


class TestSchemasSepares:
    """The point of the whole change: products do not share a column list."""

    def test_deux_produits_nont_pas_les_memes_colonnes(self, config):
        ade = set(config.produit("ade_immobilier").columns)
        sahti = set(config.produit("sahti").columns)
        assert "capital_restant_du" in ade and "capital_restant_du" not in sahti
        assert "nb_assures" in sahti and "nb_assures" not in ade
        # But they still share the identity and the money.
        assert {"num_contrat", "date_effet", "prime_totale"} <= ade & sahti

    def test_chaque_produit_declare_ses_champs_requis(self, config):
        for code, produit in config.schema_.profiles.items():
            assert produit.required, f"{code} ne déclare aucun champ requis"
            for champ in produit.required:
                assert champ in produit.columns

    def test_tous_les_champs_existent_au_catalogue(self, config):
        connus = set(config.schema_.fields) | set(config.schema_.derived_fields)
        for code, produit in config.schema_.profiles.items():
            inconnus = [c for c in produit.columns if c not in connus]
            assert inconnus == [], f"{code} référence {inconnus}"

    def test_ajouter_un_produit_ne_demande_pas_de_code(self, config):
        """A product is a block of configuration, nothing more."""
        avant = set(config.schema_.profiles)
        config.schema_.profiles["produit_test"] = type(
            config.produit("sahti")
        )(columns=["banque", "produit_code", "mois_reception", "num_contrat",
                   "date_effet", "prime_totale"],
          required=["num_contrat"], label="Produit d'essai", famille="autre",
          match=["produit essai"])
        code, _, _ = resolve_produit(
            Path("CNEP 2025/produit essai mars 2025.xlsx"), config.schema_, "CNEP"
        )
        assert code == "produit_test"
        assert set(config.schema_.profiles) - avant == {"produit_test"}


class TestBoutEnBout:
    @pytest.fixture
    def deux_produits(self, tmp_path):
        dossier = tmp_path / "CNEP 2025"
        dossier.mkdir(parents=True)
        gen_hostile.valid_baseline(dossier / "ADE_Immobilier_Mars_2025.xlsx",
                                   rows=8, month=3)
        gen_hostile.valid_baseline(dossier / "SAHTI_Mars_2025.xlsx", rows=6, month=3)
        return tmp_path

    def test_un_fichier_par_produit_donne_une_table_par_produit(
        self, deux_produits, config, tmp_path
    ):
        resultat = run(deux_produits, config, use_model=False)
        assert len(resultat.ok_results) == 2
        assert set(resultat.produits_rencontres()) == {"ade_immobilier", "sahti"}

        entrepot = tmp_path / "wh"
        commit(resultat, config, entrepot)
        assert sorted(p.name for p in entrepot.glob("fact_*.parquet")) == [
            "fact_ade_immobilier.parquet", "fact_sahti.parquet",
        ]

    def test_chaque_table_garde_ses_propres_colonnes(
        self, deux_produits, config, tmp_path
    ):
        entrepot = tmp_path / "wh"
        commit(run(deux_produits, config, use_model=False), config, entrepot)
        magasin = Warehouse(config, entrepot)

        ade = magasin.read_fact("ade_immobilier")
        sahti = magasin.read_fact("sahti")
        assert "capital_restant_du" in ade.columns
        assert "capital_restant_du" not in sahti.columns
        assert "nb_assures" in sahti.columns

    def test_chaque_ligne_porte_son_produit(self, deux_produits, config):
        resultat = run(deux_produits, config, use_model=False)
        for sortie in resultat.ok_results:
            assert set(sortie.frame["produit_code"]) == {sortie.produit}

    def test_les_deux_produits_du_meme_mois_ne_sont_pas_un_doublon(
        self, deux_produits, config
    ):
        """Two files for one bank-month are normal when they are different products."""
        resultat = run(deux_produits, config, use_model=False)
        doublons = [
            f for f in resultat.validation.flags if f.code == "period_covered_twice"
        ]
        assert doublons == []

    def test_deux_fichiers_du_meme_produit_et_du_meme_mois_restent_signales(
        self, tmp_path, config
    ):
        """The double-count guard must still fire within one product."""
        dossier = tmp_path / "CNEP 2025"
        dossier.mkdir(parents=True)
        gen_hostile.valid_baseline(dossier / "SAHTI_Mars_2025.xlsx", rows=6, month=3)
        gen_hostile.valid_baseline(dossier / "SAHTI_Mars_2025_corrige.xlsx",
                                   rows=6, month=3)
        resultat = run(tmp_path, config, use_model=False)
        doublons = [
            f for f in resultat.validation.flags if f.code == "period_covered_twice"
        ]
        assert len(doublons) == 1
        assert doublons[0].severity == "error"

    def test_un_fichier_sans_produit_utilise_le_format_de_repli(
        self, tmp_path, config
    ):
        """An unidentified file must still be usable, not rejected outright."""
        dossier = tmp_path / "CNEP 2025"
        dossier.mkdir(parents=True)
        gen_hostile.valid_baseline(dossier / "ventes mars 2025.xlsx", rows=5, month=3)
        resultat = run(tmp_path, config, use_model=False)
        assert len(resultat.ok_results) == 1
        sortie = resultat.ok_results[0]
        assert sortie.produit == config.settings.warehouse.profile
        assert any("non identifié" in note.lower() for note in sortie.notes)
        assert resultat.produits_non_identifies()
