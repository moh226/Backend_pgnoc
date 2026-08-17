"""Étape 3E — Convention tarifaire & présentation (UC16).

Publication par l'Admin SGI, fiche d'adhésion côté investisseur,
acceptation obligatoire avant soumission.
"""

import os
import tempfile

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from audit.models import JournalAudit
from comptes.models import Role, Utilisateur
from dossiers.models import Dossier, ValeurChamp
from dossiers.tests import _configurer_parcours, _signer_dossier
from sgi.models import ConventionTarifaire, InformationPresentation, SGI

MEDIA_TEMPORAIRE = tempfile.mkdtemp()

# En test, l'upload passe par un stockage local éphémère (le S3/MinIO
# de développement n'est pas disponible pendant la suite de tests).
def _pdf(nom="convention.pdf"):
    return SimpleUploadedFile(nom, b"%PDF-1.4 test", content_type="application/pdf")


@override_settings(
    MEDIA_ROOT=MEDIA_TEMPORAIRE,
    STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}},
)
class PublicationConventionTests(APITestCase):
    """UC16 : l'Admin SGI publie convention + présentation, cloisonné."""

    def setUp(self):
        self.sgi_a = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.sgi_b = SGI.objects.create(nom="SGI Bêta", code_sgi="SGIB")
        role_admin = Role.objects.filter(code="ADMIN_SGI").first()
        role_agent = Role.objects.filter(code="AGENT_SGI").first()
        self.admin_a = Utilisateur.objects.create_user(
            "admin.a@example.com", "S3curise!2026", sgi=self.sgi_a, role=role_admin,
        )
        self.admin_b = Utilisateur.objects.create_user(
            "admin.b@example.com", "S3curise!2026", sgi=self.sgi_b, role=role_admin,
        )
        self.agent = Utilisateur.objects.create_user(
            "agent@example.com", "S3curise!2026", sgi=self.sgi_a, role=role_agent,
        )
        self.investisseur = Utilisateur.objects.create_user(
            "inv@example.com", "S3curise!2026",
        )
        self.url = reverse("sgi:admin-convention")
        self.client.force_authenticate(self.admin_a)

    def test_publication_convention_pdf(self):
        reponse = self.client.put(
            self.url,
            {"titre": "Convention 2026", "fichier_pdf": _pdf()},
            format="multipart",
        )
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.assertTrue(reponse.data["url_signee"].endswith(".pdf"))
        convention = ConventionTarifaire.objects.get(sgi=self.sgi_a)
        self.assertTrue(convention.fichier_pdf.name.startswith("sgi/conventions/"))

    def test_seul_pdf_accepte(self):
        reponse = self.client.put(
            self.url,
            {"fichier_pdf": SimpleUploadedFile(
                "convention.txt", b"texte", content_type="text/plain")},
            format="multipart",
        )
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_remplacement_via_nom_genere_serveur(self):
        self.client.put(self.url, {"fichier_pdf": _pdf()}, format="multipart")
        premier = ConventionTarifaire.objects.get(sgi=self.sgi_a).fichier_pdf.name
        self.client.put(self.url, {"fichier_pdf": _pdf()}, format="multipart")
        second = ConventionTarifaire.objects.get(sgi=self.sgi_a).fichier_pdf.name
        self.assertNotEqual(premier, second)
        self.assertFalse(os.path.exists(default_storage.path(premier)))

    def test_sans_identifiant_de_sgi_l_admin_concerne_sa_propre_sgi(self):
        reponse = self.client.put(self.url, {"fichier_pdf": _pdf()}, format="multipart")
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.assertTrue(ConventionTarifaire.objects.filter(sgi=self.sgi_a).exists())
        self.assertFalse(ConventionTarifaire.objects.filter(sgi=self.sgi_b).exists())

    def test_presentation_publication_et_lecture(self):
        url = reverse("sgi:admin-presentation")
        reponse = self.client.put(
            url,
            {
                "mission": "Leader de la gestion de titres en UEMOA.",
                "forme_sociale": "SA",
                "capital_social": "500 000 000 FCFA",
                "numero_agrement": "AG-2024-015",
                "activites": [
                    {"titre": "Intermédiation", "description": "Achat-vente de titres."},
                    {"titre": "Conseil", "description": "Stratégie de financement."},
                ],
                "membres": [{"nom": "Awa Koné", "fonction": "Directrice générale"}],
                "references": [{"titre": "IPO BRVM 2025", "annee": "2025"}],
            },
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.assertEqual(reponse.data["mission"], "Leader de la gestion de titres en UEMOA.")
        self.assertTrue(reponse.data["est_regule"])
        self.assertEqual(len(reponse.data["activites"]), 2)
        self.assertEqual(len(reponse.data["membres"]), 1)

        presentation = InformationPresentation.objects.get(sgi=self.sgi_a)
        self.assertEqual(presentation.mission, "Leader de la gestion de titres en UEMOA.")
        self.assertEqual(list(presentation.activites.values_list("titre", flat=True)),
                         ["Intermédiation", "Conseil"])
        self.assertEqual(presentation.activites.get(titre="Conseil").ordre, 1)

        reponse = self.client.get(url)
        self.assertEqual(reponse.data["mission"], "Leader de la gestion de titres en UEMOA.")
        self.assertEqual(reponse.data["autorite_agrement"], "AMF-UEMOA (ex-CREPMF)")

    def test_presentation_listes_remplacees_integralement(self):
        url = reverse("sgi:admin-presentation")
        self.client.put(
            url,
            {"mission": "V1", "activites": [{"titre": "Ancien"}], "membres": [{"nom": "A"}]},
            format="json",
        )
        reponse = self.client.put(
            url,
            {"mission": "V2", "activites": [{"titre": "Nouveau"}, {"titre": "Second"}]},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        presentation = InformationPresentation.objects.get(sgi=self.sgi_a)
        self.assertEqual(presentation.mission, "V2")
        # La liste non fournie est conservée ; celle fournie est réécrite.
        self.assertEqual(list(presentation.activites.values_list("titre", flat=True)),
                         ["Nouveau", "Second"])
        self.assertEqual(list(presentation.membres.values_list("nom", flat=True)), ["A"])

    def test_presentation_validation_entrees(self):
        url = reverse("sgi:admin-presentation")
        reponse = self.client.put(
            url,
            {"activites": [{"titre": "   "}]},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)
        reponse = self.client.put(
            url, {"email_contact": "pas-un-email"}, format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_admin_sgi_forbidden(self):
        for utilisateur, url in [
            (self.agent, self.url),
            (self.investisseur, self.url),
            (self.agent, reverse("sgi:admin-presentation")),
        ]:
            self.client.force_authenticate(utilisateur)
            self.assertEqual(
                self.client.get(url).status_code, status.HTTP_403_FORBIDDEN,
            )


class FicheAdhesionTests(APITestCase):
    """UC01 : la fiche SGI expose présentation + convention à l'investisseur."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.investisseur = Utilisateur.objects.create_user(
            "inv@example.com", "S3curise!2026",
        )
        InformationPresentation.objects.create(
            sgi=self.sgi, contenu="Bienvenue chez Alpha.",
        )
        ConventionTarifaire.objects.create(sgi=self.sgi, titre="Convention 2026")
        self.client.force_authenticate(self.investisseur)

    def test_fiche_sans_pdf_convention_non_signable(self):
        reponse = self.client.get(
            reverse("sgi:sgi-fiche", kwargs={"pk": self.sgi.pk}),
        )
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        presentation = reponse.data["presentation"]
        # Tant que les sections sont vides, l'ancien texte libre sert de mission.
        self.assertEqual(presentation["mission"], "Bienvenue chez Alpha.")
        self.assertFalse(presentation["est_regule"])
        self.assertEqual(presentation["activites"], [])
        self.assertFalse(reponse.data["convention"]["signe_requis"])


class AcceptationConventionTests(APITestCase):
    """Accord investisseur + blocage de soumission (UC16)."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.investisseur = Utilisateur.objects.create_user(
            "inv@example.com", "S3curise!2026",
        )
        self.champs = _configurer_parcours(self.sgi)
        self.dossier = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi,
        )
        self.url = reverse(
            "dossiers:dossier-accepter-convention", kwargs={"dossier_pk": self.dossier.pk},
        )
        self.client.force_authenticate(self.investisseur)

    def _renseigner_parcours(self):
        for champ, valeur in [
            (self.champs["p"], "Morale"),
            (self.champs["a"], "Awa"),
            (self.champs["b"], "RCCM-123"),
        ]:
            ValeurChamp.objects.create(dossier=self.dossier, champ=champ, valeur=valeur)
        ValeurChamp.objects.create(
            dossier=self.dossier, champ=self.champs["f"], fichier="dossiers/x/y.pdf",
        )
        self.dossier.refresh_from_db()

    def _publier_convention(self):
        ConventionTarifaire.objects.create(
            sgi=self.sgi, titre="Convention 2026", fichier_pdf="sgi/conventions/x.pdf",
        )

    def test_acceptation_refusee_sans_convention_publiee(self):
        reponse = self.client.post(self.url)
        self.assertEqual(reponse.status_code, status.HTTP_409_CONFLICT)

    def test_acceptation_ok_et_idempotente(self):
        self._publier_convention()
        reponse = self.client.post(self.url)
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.assertTrue(reponse.data["convention_acceptee"])
        self.dossier.refresh_from_db()
        self.assertTrue(self.dossier.convention_acceptee)
        reponse = self.client.post(self.url)
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)

    def test_acceptation_tracee_dans_le_journal(self):
        self._publier_convention()
        self.client.post(self.url)
        self.assertTrue(JournalAudit.objects.filter(
            action=JournalAudit.Action.ACCEPTATION_CONVENTION,
            entite_concernee="Dossier",
            entite_id=str(self.dossier.pk),
        ).exists())

    def test_acceptation_impossible_apres_soumission(self):
        self._publier_convention()
        self.client.post(self.url)
        self._renseigner_parcours()
        _signer_dossier(self.dossier)
        from dossiers.workflow import transiter
        transiter(self.dossier, Dossier.Statut.SOUMIS)
        reponse = self.client.post(self.url)
        self.assertEqual(reponse.status_code, status.HTTP_409_CONFLICT)

    def test_etranger_ne_peut_pas_accepter_pour_autrui(self):
        self._publier_convention()
        autre = Utilisateur.objects.create_user("autre@example.com", "S3curise!2026")
        self.client.force_authenticate(autre)
        reponse = self.client.post(self.url)
        self.assertEqual(reponse.status_code, status.HTTP_403_FORBIDDEN)

    def test_soumission_bloquee_sans_accord(self):
        self._publier_convention()
        self._renseigner_parcours()
        _signer_dossier(self.dossier)
        reponse = self.client.post(
            reverse("dossiers:dossier-soumettre", kwargs={"dossier_pk": self.dossier.pk}),
        )
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(b"convention", reponse.content.lower())

    def test_accord_debloque_la_soumission(self):
        self._publier_convention()
        self._renseigner_parcours()
        self.client.post(self.url)
        _signer_dossier(self.dossier)
        reponse = self.client.post(
            reverse("dossiers:dossier-soumettre", kwargs={"dossier_pk": self.dossier.pk}),
        )
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.assertEqual(reponse.data["statut"], Dossier.Statut.SOUMIS)