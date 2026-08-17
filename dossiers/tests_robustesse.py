"""Robustesse — suite revue de code : validation typée KYC, uploads durcis,
bugs du cycle rejet/correction (motif, est_corrige, champs inactifs)."""

import tempfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from comptes.models import Role, Utilisateur
from dossiers.models import ChampKYC, Dossier, EtapeKYC, ValeurChamp
from dossiers.services import calculer_progression_pct
from dossiers.tests import _signer_dossier
from dossiers.workflow import transiter
from sgi.models import SGI

MEDIA_TEMPORAIRE = tempfile.mkdtemp()


class ValidationTypeeKYCTests(APITestCase):
    """NOMBRE / DATE / BOOLEEN / CHOIX : seules des valeurs conformes passent."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        etape = EtapeKYC.objects.create(sgi=self.sgi, nom="Identité", ordre=1)
        self.champs = {
            "nombre": ChampKYC.objects.create(
                etape=etape, code="revenu", nom="Revenu",
                type=ChampKYC.TypeChamp.NOMBRE,
            ),
            "date": ChampKYC.objects.create(
                etape=etape, code="naissance", nom="Naissance",
                type=ChampKYC.TypeChamp.DATE,
            ),
            "booleen": ChampKYC.objects.create(
                etape=etape, code="resident", nom="Résident",
                type=ChampKYC.TypeChamp.BOOLEEN,
            ),
            "choix": ChampKYC.objects.create(
                etape=etape, code="type", nom="Type",
                type=ChampKYC.TypeChamp.CHOIX_UNIQUE,
                options_choix=["Morale", "Physique"],
            ),
            "multichoix": ChampKYC.objects.create(
                etape=etape, code="activites", nom="Activités",
                type=ChampKYC.TypeChamp.CHOIX_MULTIPLE,
                options_choix=["Salarié", "Retraité", "Commerçant"],
            ),
        }
        self.dossier = Dossier.objects.create(
            utilisateur=Utilisateur.objects.create_user("inv@example.com", "S3curise!2026"),
            sgi=self.sgi,
        )

    def _valeur(self, champ, valeur):
        return ValeurChamp(dossier=self.dossier, champ=self.champs[champ], valeur=valeur)

    def test_nombre(self):
        self._valeur("nombre", "1250000").full_clean()
        self._valeur("nombre", "1250,50").full_clean()
        with self.assertRaises(ValidationError):
            self._valeur("nombre", "douze").full_clean()

    def test_date(self):
        self._valeur("date", "1990-05-12").full_clean()
        with self.assertRaises(ValidationError):
            self._valeur("date", "12/05/1990").full_clean()

    def test_booleen(self):
        self._valeur("booleen", "oui").full_clean()
        self._valeur("booleen", "non").full_clean()
        with self.assertRaises(ValidationError):
            self._valeur("booleen", "peut-etre").full_clean()

    def test_choix_unique_hors_options(self):
        self._valeur("choix", "Morale").full_clean()
        with self.assertRaises(ValidationError):
            self._valeur("choix", "Coopérative").full_clean()

    def test_choix_multiple(self):
        self._valeur("multichoix", '["Salarié", "Retraité"]').full_clean()
        with self.assertRaises(ValidationError):
            self._valeur("multichoix", '["Sans emploi"]').full_clean()
        with self.assertRaises(ValidationError):
            self._valeur("multichoix", "Salarié").full_clean()

    def test_api_renvoie_400_pour_valeur_hors_options(self):
        investisseur = self.dossier.utilisateur
        self.client.force_authenticate(investisseur)
        reponse = self.client.post(
            reverse("dossiers:dossier-valeurs", kwargs={"dossier_pk": self.dossier.pk}),
            {"champ": self.champs["choix"].pk, "valeur": "Coopérative"},
        )
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)


class ProgressionSansValeursFacticesTests(APITestCase):
    """« false », « 0 » ou « non » ne « remplissent » pas un champ obligatoire."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        etape = EtapeKYC.objects.create(sgi=self.sgi, nom="Identité", ordre=1)
        self.booleen = ChampKYC.objects.create(
            etape=etape, code="resident", nom="Résident",
            type=ChampKYC.TypeChamp.BOOLEEN, obligatoire=True,
        )
        self.texte = ChampKYC.objects.create(
            etape=etape, code="nom", nom="Nom",
            type=ChampKYC.TypeChamp.TEXTE_COURT, obligatoire=True,
        )
        self.dossier = Dossier.objects.create(
            utilisateur=Utilisateur.objects.create_user("inv@example.com", "S3curise!2026"),
            sgi=self.sgi,
        )

    def test_booleen_false_ne_compte_pas_comme_rempli(self):
        ValeurChamp.objects.create(
            dossier=self.dossier, champ=self.booleen, valeur="non",
        )
        ValeurChamp.objects.create(
            dossier=self.dossier, champ=self.texte, valeur="  ",
        )
        self.assertEqual(calculer_progression_pct(self.dossier), 0)

    def test_booleen_oui_compte(self):
        ValeurChamp.objects.create(
            dossier=self.dossier, champ=self.booleen, valeur="oui",
        )
        ValeurChamp.objects.create(
            dossier=self.dossier, champ=self.texte, valeur="Awa",
        )
        self.assertEqual(calculer_progression_pct(self.dossier), 100)


@override_settings(
    MEDIA_ROOT=MEDIA_TEMPORAIRE,
    STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}},
)
class UploadDurciTests(APITestCase):
    """Magic bytes vérifiés + plafond de secours + pas de clé MinIO exposée."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        etape = EtapeKYC.objects.create(sgi=self.sgi, nom="Identité", ordre=1)
        self.champ = ChampKYC.objects.create(
            etape=etape, code="cnib", nom="CNIB",
            type=ChampKYC.TypeChamp.FICHIER, formats_acceptes="pdf",
        )
        self.investisseur = Utilisateur.objects.create_user(
            "inv@example.com", "S3curise!2026",
        )
        self.dossier = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi,
        )
        self.client.force_authenticate(self.investisseur)
        self.url = reverse(
            "dossiers:dossier-valeur-fichier-upload",
            kwargs={"dossier_pk": self.dossier.pk},
        )

    def test_pdf_avec_bon_magic_bytes_accepte(self):
        reponse = self.client.post(
            self.url,
            {"champ": self.champ.pk, "fichier": SimpleUploadedFile(
                "cnib.pdf", b"%PDF-1.4 contenu", content_type="application/pdf")},
            format="multipart",
        )
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)

    def test_contenu_falsifie_refuse(self):
        """Un exécutable déguisé en .pdf doit être rejeté (magic bytes)."""
        executable = b"\x7fELF\x02\x01\x01" + b"\x00" * 50
        reponse = self.client.post(
            self.url,
            {"champ": self.champ.pk, "fichier": SimpleUploadedFile(
                "cnib.pdf", executable, content_type="application/pdf")},
            format="multipart",
        )
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(ValeurChamp.objects.filter(dossier=self.dossier).exists())

    def test_reponse_sans_cle_de_stockage_interne(self):
        reponse = self.client.post(
            self.url,
            {"champ": self.champ.pk, "fichier": SimpleUploadedFile(
                "cnib.pdf", b"%PDF-1.4 contenu", content_type="application/pdf")},
            format="multipart",
        )
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)
        self.assertNotIn("fichier", reponse.data)
        self.assertIn("url_signee", reponse.data)


@override_settings(
    MEDIA_ROOT=MEDIA_TEMPORAIRE,
    STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}},
)
class CycleRejetCorrectionTests(APITestCase):
    """Bugs : motif purgé à la resoumission, est_corrige honoré à l'upload."""

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

    def test_motif_de_rejet_purge_a_la_resoumission(self):
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
        self.assertEqual(self.dossier.motif_rejet, "CNIB illisible")

        transiter(self.dossier, Dossier.Statut.SOUMIS)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.motif_rejet, "")
        self.assertEqual(self.dossier.version, 2)

    def test_upload_apres_commentaire_marque_est_corrige(self):
        champ_fichier = ChampKYC.objects.create(
            etape=self.champ.etape, code="cnib", nom="CNIB",
            type=ChampKYC.TypeChamp.FICHIER, formats_acceptes="pdf",
        )
        valeur = ValeurChamp.objects.create(
            dossier=self.dossier, champ=champ_fichier, fichier="dossiers/x/ancien.pdf",
        )
        valeur.commentaire_agent = "Pièce illisible."
        valeur.save(update_fields=["commentaire_agent"])

        self.client.force_authenticate(self.investisseur)
        reponse = self.client.post(
            reverse("dossiers:dossier-valeur-fichier-upload",
                    kwargs={"dossier_pk": self.dossier.pk}),
            {"champ": champ_fichier.pk, "fichier": SimpleUploadedFile(
                "cnib.pdf", b"%PDF-1.4 nouveau", content_type="application/pdf")},
            format="multipart",
        )
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)
        valeur.refresh_from_db()
        self.assertTrue(valeur.est_corrige)
        self.assertTrue(valeur.fichier.endswith(".pdf"))

    def _rejeter_en_instruction(self, motif="À corriger"):
        _signer_dossier(self.dossier)
        transiter(self.dossier, Dossier.Statut.SOUMIS)
        transiter(self.dossier, Dossier.Statut.EN_INSTRUCTION, agent=self.agent)
        transiter(
            self.dossier, Dossier.Statut.REJETE, agent=self.agent,
            motif_rejet=motif, utilisateur=self.agent,
        )
        self.assertEqual(self.dossier.statut, Dossier.Statut.REJETE)

    def test_rejete_bloque_la_correction_d_un_champ_non_commentee(self):
        """UC12 : après rejet, un champ jamais signalé ne peut plus être modifié."""
        ValeurChamp.objects.create(
            dossier=self.dossier, champ=self.champ, valeur="Awa",
        )
        self._rejeter_en_instruction()

        self.client.force_authenticate(self.investisseur)
        reponse = self.client.post(
            reverse("dossiers:dossier-valeurs", kwargs={"dossier_pk": self.dossier.pk}),
            {"champ": self.champ.pk, "valeur": "Awa Koné"},
        )
        self.assertEqual(reponse.status_code, status.HTTP_403_FORBIDDEN)
        valeur = ValeurChamp.objects.get(dossier=self.dossier, champ=self.champ)
        self.assertEqual(valeur.valeur, "Awa")
        self.assertFalse(valeur.est_corrige)

    def test_rejete_bloque_l_ajout_d_un_champ_jamais_rempli(self):
        """Après rejet, on ne peut pas créer de valeur sur un champ non commenté."""
        ValeurChamp.objects.create(
            dossier=self.dossier, champ=self.champ, valeur="Awa",
        )
        champ_b = ChampKYC.objects.create(
            etape=self.champ.etape, code="prenom", nom="Prénom",
            type=ChampKYC.TypeChamp.TEXTE_COURT, obligatoire=False,
        )
        self._rejeter_en_instruction()

        self.client.force_authenticate(self.investisseur)
        reponse = self.client.post(
            reverse("dossiers:dossier-valeurs", kwargs={"dossier_pk": self.dossier.pk}),
            {"champ": champ_b.pk, "valeur": "Nouvelle valeur"},
        )
        self.assertEqual(reponse.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(
            ValeurChamp.objects.filter(dossier=self.dossier, champ=champ_b).exists()
        )

    def test_rejete_bloque_l_upload_sur_un_champ_non_commentee(self):
        """Après rejet, le remplacement de fichier exige aussi un retour d'agent."""
        champ_fichier = ChampKYC.objects.create(
            etape=self.champ.etape, code="cnib", nom="CNIB",
            type=ChampKYC.TypeChamp.FICHIER, formats_acceptes="pdf",
        )
        ValeurChamp.objects.create(
            dossier=self.dossier, champ=champ_fichier, fichier="dossiers/x/ancien.pdf",
        )
        ValeurChamp.objects.create(
            dossier=self.dossier, champ=self.champ, valeur="Awa",
        )
        self._rejeter_en_instruction()

        self.client.force_authenticate(self.investisseur)
        reponse = self.client.post(
            reverse("dossiers:dossier-valeur-fichier-upload",
                    kwargs={"dossier_pk": self.dossier.pk}),
            {"champ": champ_fichier.pk, "fichier": SimpleUploadedFile(
                "cnib.pdf", b"%PDF-1.4 autre", content_type="application/pdf")},
            format="multipart",
        )
        self.assertEqual(reponse.status_code, status.HTTP_403_FORBIDDEN)
        valeur = ValeurChamp.objects.get(dossier=self.dossier, champ=champ_fichier)
        self.assertEqual(valeur.fichier, "dossiers/x/ancien.pdf")