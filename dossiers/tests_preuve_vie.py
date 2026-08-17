"""Preuve de vie asynchrone (champ SELFIE) : capture caméra contrainte à
l'UI, chaîne de preuve SERVEUR (hash SHA-256 + date + signature HMAC),
re-vérification d'authenticité pour l'audit CREPMF."""

import hashlib
import tempfile

from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from comptes.models import Role, Utilisateur
from dossiers.models import ChampKYC, Dossier, EtapeKYC, ValeurChamp
from dossiers.services import calculer_progression_pct
from sgi.models import SGI

MEDIA_TEMPORAIRE = tempfile.mkdtemp()

_JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 256
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 256
_WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64  # magic bytes vidéo (interdit)


class ChampSelfieValidationTests(APITestCase):
    """Le type SELFIE ne peut être configuré qu'avec des formats image."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.etape = EtapeKYC.objects.create(sgi=self.sgi, nom="Identité", ordre=1)

    def test_creation_selfie_avec_formats_image_ok(self):
        champ = ChampKYC(
            etape=self.etape, code="selfie", nom="Selfie",
            type=ChampKYC.TypeChamp.SELFIE, formats_acceptes="jpg,png",
            taille_max_mo=5,
        )
        champ.save()
        self.assertEqual(champ.formats_acceptes, "jpg,png")

    def test_creation_selfie_sans_formats_refusee(self):
        with self.assertRaises(ValidationError):
            ChampKYC(
                etape=self.etape, code="selfie", nom="Selfie",
                type=ChampKYC.TypeChamp.SELFIE,
            ).save()

    def test_creation_selfie_avec_format_non_image_refusee(self):
        with self.assertRaises(ValidationError):
            ChampKYC(
                etape=self.etape, code="selfie", nom="Selfie",
                type=ChampKYC.TypeChamp.SELFIE, formats_acceptes="pdf",
            ).save()

    def test_formats_autorises_pour_non_fichier_refuses(self):
        """Les formats/taille restent réservés aux types fichiers."""
        with self.assertRaises(ValidationError):
            ChampKYC(
                etape=self.etape, code="nom", nom="Nom",
                type=ChampKYC.TypeChamp.TEXTE_COURT, formats_acceptes="jpg",
            ).save()


@override_settings(
    MEDIA_ROOT=MEDIA_TEMPORAIRE,
    STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}},
)
class UploadSelfieTests(APITestCase):
    """Upload d'un selfie : empreinte + horodatage + signature serveur."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        etape = EtapeKYC.objects.create(sgi=self.sgi, nom="Identité", ordre=1)
        self.champ = ChampKYC.objects.create(
            etape=etape, code="selfie", nom="Selfie CNIB",
            type=ChampKYC.TypeChamp.SELFIE, formats_acceptes="jpg,png,webp",
            taille_max_mo=5,
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

    def _uploader(self, nom, contenu):
        return self.client.post(
            self.url,
            {"champ": self.champ.pk, "fichier": SimpleUploadedFile(nom, contenu)},
            format="multipart",
        )

    def test_selfie_jpg_accepte_avec_preuve_serveur(self):
        self.assertEqual(calculer_progression_pct(self.dossier), 0)
        reponse = self._uploader("selfie.jpg", _JPG)
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)

        empreinte_attendue = hashlib.sha256(_JPG).hexdigest()
        self.assertEqual(reponse.data["empreinte_sha256"], empreinte_attendue)
        self.assertIsNotNone(reponse.data["date_capture"])

        valeur = ValeurChamp.objects.get(dossier=self.dossier, champ=self.champ)
        self.assertEqual(valeur.empreinte_sha256, empreinte_attendue)
        self.assertEqual(len(valeur.signature_serveur), 64)
        self.assertIsNotNone(valeur.date_capture)
        self.assertEqual(calculer_progression_pct(self.dossier), 100)

    def test_selfie_png_accepte(self):
        reponse = self._uploader("selfie.png", _PNG)
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)

    def test_video_deguisee_en_jpg_refusee(self):
        reponse = self._uploader("selfie.jpg", _WEBM)
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(ValeurChamp.objects.filter(dossier=self.dossier).exists())

    def test_extension_hors_formats_refusee(self):
        reponse = self._uploader("selfie.mp4", _PNG)
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_selfie_trop_volumineux_refuse(self):
        gros = _JPG + b"\x00" * (6 * 1024 * 1024)
        reponse = self._uploader("selfie.jpg", gros)
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_valeur_selfie_sans_fichier_refusee(self):
        champ = ChampKYC.objects.create(
            etape=self.champ.etape, code="selfie2", nom="Selfie 2",
            type=ChampKYC.TypeChamp.SELFIE, formats_acceptes="jpg",
        )
        with self.assertRaises(ValidationError):
            ValeurChamp(
                dossier=self.dossier, champ=champ,
            ).save()


@override_settings(
    MEDIA_ROOT=MEDIA_TEMPORAIRE,
    STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}},
)
class VerificationAuthenticiteSelfieTests(APITestCase):
    """Endpoint d'authenticité : recompute hash + revalidation HMAC."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        etape = EtapeKYC.objects.create(sgi=self.sgi, nom="Identité", ordre=1)
        self.champ = ChampKYC.objects.create(
            etape=etape, code="selfie", nom="Selfie CNIB",
            type=ChampKYC.TypeChamp.SELFIE, formats_acceptes="jpg",
        )
        self.champ_fichier = ChampKYC.objects.create(
            etape=etape, code="cnib", nom="CNIB",
            type=ChampKYC.TypeChamp.FICHIER, formats_acceptes="pdf",
        )
        self.investisseur = Utilisateur.objects.create_user(
            "inv@example.com", "S3curise!2026",
        )
        self.agent = Utilisateur.objects.create_user(
            "agent@example.com", "S3curise!2026", sgi=self.sgi,
            role=Role.objects.filter(code="AGENT_SGI").first(),
        )
        self.dossier = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi,
        )
        self.url_upload = reverse(
            "dossiers:dossier-valeur-fichier-upload",
            kwargs={"dossier_pk": self.dossier.pk},
        )
        self.client.force_authenticate(self.investisseur)
        reponse = self.client.post(
            self.url_upload,
            {"champ": self.champ.pk, "fichier": SimpleUploadedFile("selfie.jpg", _JPG)},
            format="multipart",
        )
        self.valeur = ValeurChamp.objects.get(dossier=self.dossier, champ=self.champ)
        self.client.logout()

    def _verifier(self, utilisateur):
        self.client.force_authenticate(utilisateur)
        return self.client.get(reverse(
            "dossiers:dossier-valeur-selfie-authenticite",
            kwargs={"dossier_pk": self.dossier.pk, "valeur_pk": self.valeur.pk},
        ))

    def test_authenticite_conforme_pour_l_investisseur(self):
        reponse = self._verifier(self.investisseur)
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.assertTrue(reponse.data["concordante"])
        self.assertTrue(reponse.data["signature_valide"])
        self.assertEqual(reponse.data["empreinte_sha256"], self.valeur.empreinte_sha256)

    def test_authenticite_acces_agent_sgi(self):
        reponse = self._verifier(self.agent)
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)

    def test_authenticite_detecte_la_moitise_du_fichier_stocke(self):
        chemin = self.valeur.fichier
        with default_storage.open(chemin, "wb") as objet:
            objet.write(_JPG + b"\xFF\xFE\xFD")  # contenu altéré en stockage
        reponse = self._verifier(self.investisseur)
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        # L'altération du contenu est détectée par le recalcul du hash ;
        # la signature HMAC, elle, reste valide : les MÉTADONNÉES de
        # l'enregistrement (référence, chemin, empreinte, horodatage)
        # n'ont pas été modifiées en base. Verdict global : non conforme.
        self.assertFalse(reponse.data["concordante"])
        self.assertTrue(reponse.data["signature_valide"])
        self.assertIn("ne correspond pas", reponse.data["detail"])

    def test_authenticite_refuse_un_champ_non_selfie(self):
        valeur_fichier = ValeurChamp.objects.create(
            dossier=self.dossier, champ=self.champ_fichier, fichier="dossiers/a.pdf",
        )
        self.client.force_authenticate(self.investisseur)
        reponse = self.client.get(reverse(
            "dossiers:dossier-valeur-selfie-authenticite",
            kwargs={"dossier_pk": self.dossier.pk, "valeur_pk": valeur_fichier.pk},
        ))
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_authenticite_refuse_un_tiers(self):
        tiers = Utilisateur.objects.create_user(
            "tiers@example.com", "S3curise!2026",
            role=Role.objects.filter(code="INVESTISSEUR").first(),
        )
        reponse = self._verifier(tiers)
        self.assertEqual(reponse.status_code, status.HTTP_403_FORBIDDEN)