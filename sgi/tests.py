"""Tests du modèle SGI et du durcissement de l'upload de convention.

Les scénarios fonctionnels complets de l'UC16 (publication, fiche
d'adhésion, acceptation avant soumission) vivent dans
`sgi/tests_convention.py` ; ce module couvre les invariants de bas
niveau : sémantique de `est_publiee()` et validation du fichier PDF
(extension, contenu réel, taille).
"""

import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from comptes.models import Role, Utilisateur
from sgi.models import ConventionTarifaire, SGI
from sgi.views_admin import _obtenir_ou_creer

MEDIA_TEMPORAIRE = tempfile.mkdtemp()


class ConventionEstPublieeTests(TestCase):
    """`est_publiee()` ne doit dépendre QUE du PDF déposé.

    Régression : la méthode acceptait auparavant « titre OU PDF »,
    alors que la fiche publique (`signe_requis`) et le blocage de
    soumission du workflow s'appuient sur le PDF seul. Un simple titre
    saisi rendait donc l'acceptation exigible sans document à lire.
    """

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")

    def test_convention_vide_non_publiee(self):
        convention = ConventionTarifaire.objects.create(sgi=self.sgi)

        self.assertFalse(convention.est_publiee())

    def test_titre_seul_ne_publie_pas(self):
        convention = ConventionTarifaire.objects.create(
            sgi=self.sgi, titre="Convention 2026",
        )

        self.assertFalse(convention.est_publiee())

    def test_pdf_depose_publie(self):
        convention = ConventionTarifaire.objects.create(sgi=self.sgi)
        convention.fichier_pdf.name = "sgi/conventions/abc.pdf"

        self.assertTrue(convention.est_publiee())


@override_settings(
    MEDIA_ROOT=MEDIA_TEMPORAIRE,
    STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}},
)
class ValidationFichierConventionTests(APITestCase):
    """L'upload de convention est contrôlé sur le CONTENU, pas le nom.

    Le nom de fichier et le `Content-Type` sont fournis par le client :
    ils sont falsifiables. Seuls les magic bytes attestent qu'il s'agit
    réellement d'un PDF.
    """

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.admin = Utilisateur.objects.create_user(
            "admin.a@example.com",
            "S3curise!2026",
            sgi=self.sgi,
            role=Role.Code.ADMIN_SGI,
        )
        self.url = reverse("sgi:admin-convention")
        self.client.force_authenticate(self.admin)

    def test_pdf_valide_accepte(self):
        fichier = SimpleUploadedFile(
            "convention.pdf", b"%PDF-1.4 contenu", content_type="application/pdf",
        )

        reponse = self.client.put(
            self.url, {"fichier_pdf": fichier}, format="multipart",
        )

        self.assertEqual(reponse.status_code, status.HTTP_200_OK)

    def test_executable_renomme_en_pdf_refuse(self):
        # Extension et Content-Type « corrects », contenu ELF : doit être
        # rejeté par le sniff des magic bytes.
        fichier = SimpleUploadedFile(
            "convention.pdf", b"\x7fELF\x02\x01\x01charge", content_type="application/pdf",
        )

        reponse = self.client.put(
            self.url, {"fichier_pdf": fichier}, format="multipart",
        )

        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("fichier_pdf", reponse.data)
        self.assertFalse(
            ConventionTarifaire.objects.get(sgi=self.sgi).fichier_pdf
        )

    def test_mauvaise_extension_refusee(self):
        fichier = SimpleUploadedFile(
            "convention.txt", b"%PDF-1.4 contenu", content_type="text/plain",
        )

        reponse = self.client.put(
            self.url, {"fichier_pdf": fichier}, format="multipart",
        )

        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_fichier_trop_volumineux_refuse(self):
        # Plafond abaissé le temps du test : inutile de faire transiter
        # réellement 10 Mo en mémoire pour vérifier la règle.
        fichier = SimpleUploadedFile(
            "convention.pdf", b"%PDF-1.4" + b"0" * 4096,
            content_type="application/pdf",
        )

        with mock.patch("sgi.views_admin.TAILLE_MAX_CONVENTION_MO", 0.001):
            reponse = self.client.put(
                self.url, {"fichier_pdf": fichier}, format="multipart",
            )

        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("volumineux", reponse.data["fichier_pdf"])


class ObtenirOuCreerTolereLaCourseTests(TestCase):
    """Régression : deux PUT simultanés sur la convention d'une même SGI.

    La contrainte d'unicité OneToOne déclenche une IntegrityError sur la
    seconde requête ; `_obtenir_ou_creer` doit retomber proprement sur
    l'enregistrement existant au lieu de remonter un 500.
    """

    def test_collision_de_creation_recuperee(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        convention = ConventionTarifaire.objects.create(sgi=self.sgi, titre="Existante")

        appels = {"n": 0}

        def get_or_create_concurrent_des(*args, **kwargs):
            appels["n"] += 1
            if appels["n"] == 1:
                raise IntegrityError("duplicate key value violates unique constraint")
            return convention, False

        with mock.patch.object(
            ConventionTarifaire.objects, "get_or_create",
            side_effect=get_or_create_concurrent_des,
        ):
            objet, cree = _obtenir_ou_creer(ConventionTarifaire, sgi_id=self.sgi.pk)

        self.assertFalse(cree)
        self.assertEqual(objet.pk, convention.pk)
