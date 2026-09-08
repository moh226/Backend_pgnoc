"""Tests du lot P1 (audit) — valeur probante de la chaîne de signature.

Couvre les corrections :
  - la preuve de signature couvre le CONTENU du dossier (empreinte des
    valeurs) ; une valeur modifiée après signature invalide la validation ;
  - la resoumission après rejet purge la signature (elle couvrait le
    contenu de la version précédente) ;
  - la convention acceptée est versionnée : une convention remplacée
    par la SGI rend l'acceptation caduque jusqu'à renouvellement ;
  - l'OTP résiste au brute-force (plafond de tentatives erronées) ;
  - l'upload est refusé sur un champ ou une étape désactivée ;
  - un dossier rejeté reste corrigeable sur un champ OBLIGATOIRE ajouté
    par la SGI après le rejet (sinon blocage définitif à <100 %).
"""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from comptes.models import Role, Utilisateur
from dossiers.models import ChampKYC, Dossier, EtapeKYC, ValeurChamp
from dossiers.services import (
    calculer_progression_pct,
    generer_code_otp,
    poser_signature_otp,
)
from dossiers.tests import _signer_dossier
from dossiers.workflow import transiter
from sgi.models import ConventionTarifaire, SGI


class PreuveSignatureCouvreContenuTests(APITestCase):
    """La preuve scellée à la signature doit suivre le contenu soumis."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.investisseur = Utilisateur.objects.create_user(
            "inv@example.com", "S3curise!2026",
        )
        role_agent = Role.objects.filter(code="AGENT_SGI").first()
        self.agent = Utilisateur.objects.create_user(
            "agent@example.com", "S3curise!2026", sgi=self.sgi, role=role_agent,
        )
        etape = EtapeKYC.objects.create(sgi=self.sgi, nom="Identité", ordre=1)
        self.champ = ChampKYC.objects.create(
            etape=etape, code="nom", nom="Nom",
            type=ChampKYC.TypeChamp.TEXTE_COURT, obligatoire=True,
        )
        self.dossier = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi,
        )

    def _cycle_jusqua_instruction(self):
        ValeurChamp.objects.create(
            dossier=self.dossier, champ=self.champ, valeur="Awa",
        )
        _signer_dossier(self.dossier)
        transiter(self.dossier, Dossier.Statut.SOUMIS)
        transiter(self.dossier, Dossier.Statut.EN_INSTRUCTION, agent=self.agent)

    def test_valeur_modifiee_apres_signature_bloque_la_validation(self):
        self._cycle_jusqua_instruction()

        # Valeur modifiée APRÈS la signature (bypass direct base pour le
        # test — via l'admin par exemple) : la preuve ne correspond plus.
        ValeurChamp.objects.filter(dossier=self.dossier, champ=self.champ).update(
            valeur="Awa Falsifiée",
        )

        with self.assertRaises(Exception):
            transiter(
                self.dossier, Dossier.Statut.VALIDE, agent=self.agent,
                utilisateur=self.agent,
            )
        self.dossier.refresh_from_db()
        self.assertNotEqual(self.dossier.statut, Dossier.Statut.VALIDE)

    def test_validation_possible_quand_le_contenu_est_intact(self):
        self._cycle_jusqua_instruction()
        transiter(
            self.dossier, Dossier.Statut.VALIDE, agent=self.agent,
            utilisateur=self.agent,
        )
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.statut, Dossier.Statut.VALIDE)

    def test_resoumission_apres_rejet_purge_la_signature(self):
        ValeurChamp.objects.create(
            dossier=self.dossier, champ=self.champ, valeur="Awa",
        )
        _signer_dossier(self.dossier)
        transiter(self.dossier, Dossier.Statut.SOUMIS)
        transiter(self.dossier, Dossier.Statut.EN_INSTRUCTION, agent=self.agent)
        transiter(
            self.dossier, Dossier.Statut.REJETE, agent=self.agent,
            motif_rejet="CNIB illisible", utilisateur=self.agent,
        )
        # Le rejet purge la preuve : elle couvrait l'ancien contenu.
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.donnee_signature, "")
        self.assertEqual(self.dossier.type_signature, "")

        # Resoumission sans re-signature : refusée.
        with self.assertRaises(Exception):
            transiter(self.dossier, Dossier.Statut.SOUMIS)

        # Après re-signature du contenu corrigé : la resoumission passe.
        ValeurChamp.objects.filter(dossier=self.dossier, champ=self.champ).update(
            valeur="Awa Corrigée",
        )
        _signer_dossier(self.dossier)
        transiter(self.dossier, Dossier.Statut.SOUMIS)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.statut, Dossier.Statut.SOUMIS)
        self.assertEqual(self.dossier.version, 2)


class ConventionVersioneeTests(APITestCase):
    """L'acceptation porte sur une version précise du PDF de la SGI."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Beta", code_sgi="SGIB")
        self.investisseur = Utilisateur.objects.create_user(
            "inv2@example.com", "S3curise!2026",
        )
        etape = EtapeKYC.objects.create(sgi=self.sgi, nom="Identité", ordre=1)
        self.champ = ChampKYC.objects.create(
            etape=etape, code="nom", nom="Nom",
            type=ChampKYC.TypeChamp.TEXTE_COURT, obligatoire=True,
        )
        self.dossier = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi,
        )
        self.url = reverse(
            "dossiers:dossier-accepter-convention",
            kwargs={"dossier_pk": self.dossier.pk},
        )

    def _publier(self, nom="a.pdf"):
        convention, _ = ConventionTarifaire.objects.get_or_create(
            sgi=self.sgi, defaults={"titre": "Convention"},
        )
        convention.fichier_pdf.name = f"sgi/conventions/{nom}"
        convention.save(update_fields=["fichier_pdf"])
        return convention

    def test_acceptation_figure_la_version(self):
        self._publier("v1.pdf")
        self.client.force_authenticate(self.investisseur)
        reponse = self.client.post(self.url)
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.dossier.refresh_from_db()
        self.assertEqual(
            self.dossier.convention_version, "sgi/conventions/v1.pdf",
        )

    def test_convention_remplacee_rend_l_acceptation_caduque(self):
        self._publier("v1.pdf")
        self.client.force_authenticate(self.investisseur)
        self.client.post(self.url)
        self.dossier.refresh_from_db()
        self.assertTrue(self.dossier.convention_acceptee)

        # La SGI publie une nouvelle version : la soumission doit être
        # refusée tant que l'investisseur n'a pas ré-accepté.
        self._publier("v2.pdf")
        ValeurChamp.objects.create(
            dossier=self.dossier, champ=self.champ, valeur="Awa",
        )
        _signer_dossier(self.dossier)
        with self.assertRaises(Exception):
            transiter(self.dossier, Dossier.Statut.SOUMIS)

        # Ré-acceptation : elle figure la nouvelle version.
        reponse = self.client.post(self.url)
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.dossier.refresh_from_db()
        self.assertEqual(
            self.dossier.convention_version, "sgi/conventions/v2.pdf",
        )
        transiter(self.dossier, Dossier.Statut.SOUMIS)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.statut, Dossier.Statut.SOUMIS)


class OtpAntiBruteForceTests(APITestCase):
    """Un OTP ne survit pas à N codes erronés."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Gamma", code_sgi="SGIC")
        self.investisseur = Utilisateur.objects.create_user(
            "inv3@example.com", "S3curise!2026",
        )
        self.dossier = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi,
        )

    def test_otp_purge_apres_trop_de_codes_errones(self):
        code = generer_code_otp(self.dossier)
        mauvais = "000000" if code != "000000" else "111111"

        from django.core.exceptions import ValidationError

        for _ in range(4):
            with self.assertRaises(ValidationError):
                poser_signature_otp(self.dossier, mauvais)
        self.dossier.refresh_from_db()
        # Encore actif sous le plafond.
        self.assertTrue(self.dossier.otp_hash)

        # 5e échec : OTP purgé.
        with self.assertRaises(ValidationError):
            poser_signature_otp(self.dossier, mauvais)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.otp_hash, "")

        # Même le BON code est désormais refusé.
        with self.assertRaises(ValidationError):
            poser_signature_otp(self.dossier, code)


class UploadChampDesactiveTests(APITestCase):
    """Un upload sur un champ/étape retiré du parcours est refusé."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Delta", code_sgi="SGID")
        self.investisseur = Utilisateur.objects.create_user(
            "inv4@example.com", "S3curise!2026",
        )
        self.etape = EtapeKYC.objects.create(sgi=self.sgi, nom="Identité", ordre=1)
        self.champ = ChampKYC.objects.create(
            etape=self.etape, code="cnib", nom="CNIB",
            type=ChampKYC.TypeChamp.FICHIER, formats_acceptes="pdf",
        )
        self.dossier = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi,
        )
        self.url = reverse(
            "dossiers:dossier-valeur-fichier-upload",
            kwargs={"dossier_pk": self.dossier.pk},
        )

    def _upload(self):
        return self.client.post(
            self.url,
            {"champ": self.champ.pk, "fichier": SimpleUploadedFile(
                "cnib.pdf", b"%PDF-1.4 contenu", content_type="application/pdf",
            )},
            format="multipart",
        )

    def test_upload_refuse_sur_champ_desactive(self):
        self.champ.actif = False
        self.champ.save(update_fields=["actif"])
        self.client.force_authenticate(self.investisseur)
        reponse = self._upload()
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_refuse_sur_etape_desactivee(self):
        self.etape.actif = False
        self.etape.save(update_fields=["actif"])
        self.client.force_authenticate(self.investisseur)
        reponse = self._upload()
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_accepte_sur_champ_actif(self):
        self.client.force_authenticate(self.investisseur)
        reponse = self._upload()
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)


class RejeteChampObligatoireAjouteTests(APITestCase):
    """Champ obligatoire ajouté après rejet : saisissable (anti-blocage)."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Epsilon", code_sgi="SGIE")
        self.investisseur = Utilisateur.objects.create_user(
            "inv5@example.com", "S3curise!2026",
        )
        role_agent = Role.objects.filter(code="AGENT_SGI").first()
        self.agent = Utilisateur.objects.create_user(
            "agent5@example.com", "S3curise!2026", sgi=self.sgi, role=role_agent,
        )
        self.etape = EtapeKYC.objects.create(sgi=self.sgi, nom="Identité", ordre=1)
        self.champ = ChampKYC.objects.create(
            etape=self.etape, code="nom", nom="Nom",
            type=ChampKYC.TypeChamp.TEXTE_COURT, obligatoire=True,
        )
        self.dossier = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi,
        )

    def _rejeter(self):
        ValeurChamp.objects.create(
            dossier=self.dossier, champ=self.champ, valeur="Awa",
        )
        _signer_dossier(self.dossier)
        transiter(self.dossier, Dossier.Statut.SOUMIS)
        transiter(self.dossier, Dossier.Statut.EN_INSTRUCTION, agent=self.agent)
        transiter(
            self.dossier, Dossier.Statut.REJETE, agent=self.agent,
            motif_rejet="À compléter", utilisateur=self.agent,
        )

    def test_champ_obligatoire_ajoute_apres_rejet_est_saisissable(self):
        self._rejeter()
        nouveau = ChampKYC.objects.create(
            etape=self.etape, code="telephone", nom="Téléphone",
            type=ChampKYC.TypeChamp.TEXTE_COURT, obligatoire=True,
        )
        self.assertEqual(calculer_progression_pct(self.dossier), 50)

        self.client.force_authenticate(self.investisseur)
        reponse = self.client.post(
            reverse("dossiers:dossier-valeurs",
                    kwargs={"dossier_pk": self.dossier.pk}),
            {"champ": nouveau.pk, "valeur": "+226 70 00 00 00"},
        )
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)
        self.assertEqual(calculer_progression_pct(self.dossier), 100)

    def test_champ_facultatif_ajoute_apres_rejet_reste_interdit(self):
        self._rejeter()
        facultatif = ChampKYC.objects.create(
            etape=self.etape, code="surnom", nom="Surnom",
            type=ChampKYC.TypeChamp.TEXTE_COURT, obligatoire=False,
        )
        self.client.force_authenticate(self.investisseur)
        reponse = self.client.post(
            reverse("dossiers:dossier-valeurs",
                    kwargs={"dossier_pk": self.dossier.pk}),
            {"champ": facultatif.pk, "valeur": "Quelconque"},
        )
        self.assertEqual(reponse.status_code, status.HTTP_403_FORBIDDEN)
