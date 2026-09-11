"""Tests de la preuve de dépôt minimum post-validation (Solution 1).

Couverture :
  - configuration admin SGI (activation, incohérences, cloisonnement) ;
  - création du dépôt à la validation du dossier (exigence active) ;
  - consultation / dépôt de la preuve par l'investisseur (magic bytes) ;
  - vérification par le personnel SGI : approbation → dossier ACTIF,
    rejet → l'investisseur redépose ;
  - activation directe (SGI sans exigence) et blocage (exigence non
    satisfaite) ;
  - fiche SGI publique (montant visible avant validation).
"""

import os
import tempfile

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from audit.models import JournalAudit
from comptes.models import Role, Utilisateur
from dossiers.models import DepotMinimum, Dossier, ValeurChamp
from dossiers.tests import _configurer_parcours, _signer_dossier
from notifications.models import Notification
from sgi.models import ConfigDepotMinimum, SGI

_JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
_PDF = b"%PDF-1.4 test"
MEDIA_TEMPORAIRE = tempfile.mkdtemp()


@override_settings(
    MEDIA_ROOT=MEDIA_TEMPORAIRE,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class DepotMinimumTests(APITestCase):
    """Flux complet du dépôt minimum post-validation."""

    def setUp(self):
        self.sgi_a = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.sgi_b = SGI.objects.create(nom="SGI Bêta", code_sgi="SGIB")
        role_admin = Role.objects.filter(code="ADMIN_SGI").first()
        role_agent = Role.objects.filter(code="AGENT_SGI").first()
        role_investisseur = Role.objects.filter(code="INVESTISSEUR").first()
        self.admin_a = Utilisateur.objects.create_user(
            "admin.a@example.com", "S3curise!2026", sgi=self.sgi_a, role=role_admin,
        )
        self.agent_a = Utilisateur.objects.create_user(
            "agent.a@example.com", "S3curise!2026", sgi=self.sgi_a, role=role_agent,
        )
        self.inv = Utilisateur.objects.create_user(
            "inv@example.com", "S3curise!2026", role=role_investisseur,
        )
        self.inv_b = Utilisateur.objects.create_user(
            "inv.b@example.com", "S3curise!2026", role=role_investisseur,
        )
        self.champs = _configurer_parcours(self.sgi_a)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _config_depot(self, *, exigee=True, montant=25_000, methodes=("ORANGE_MONEY", "WAVE")):
        config, _ = ConfigDepotMinimum.objects.get_or_create(
            sgi=self.sgi_a,
            defaults={
                "exige_depot": exigee,
                "montant_depot_min": montant,
                "instructions": "Versez 25 000 FCFA via Orange Money au +225 07 00 00 00 00.",
                "methodes_acceptees": list(methodes),
            },
        )
        if not exigee and config.exige_depot:
            config.exige_depot = False
            config.save(update_fields=["exige_depot"])
        return config

    def _dossier_valide(self, investisseur=None):
        """Crée un dossier et l'amène à VALIDE via le circuit complet (API)."""
        investisseur = investisseur or self.inv
        self.client.force_authenticate(investisseur)
        rep = self.client.post(
            reverse("dossiers:dossier-list-create"), {"sgi": self.sgi_a.pk}
        )
        dossier_pk = rep.data["id"]
        url_valeurs = reverse("dossiers:dossier-valeurs", kwargs={"dossier_pk": dossier_pk})
        for champ, valeur in [
            (self.champs["p"], "Morale"),
            (self.champs["a"], "Awa Koné"),
            (self.champs["b"], "RCCM-2026-042"),
        ]:
            self.client.post(url_valeurs, {"champ": champ.pk, "valeur": valeur})
        ValeurChamp.objects.create(
            dossier_id=dossier_pk, champ=self.champs["f"], fichier="dossiers/x/y.pdf",
        )
        url_otp = reverse("dossiers:dossier-generer-otp", kwargs={"dossier_pk": dossier_pk})
        url_soumettre = reverse("dossiers:dossier-soumettre", kwargs={"dossier_pk": dossier_pk})
        # Signature posée via le service (pattern des tests unitaires) :
        # elle est indépendante du canal d'acheminement du code OTP.
        _signer_dossier(Dossier.objects.get(pk=dossier_pk))
        rep_soumettre = self.client.post(url_soumettre)
        if rep_soumettre.status_code != status.HTTP_200_OK:
            raise AssertionError(
                "soumission refusée "
                f"(soumettre={rep_soumettre.status_code}: {rep_soumettre.data})"
            )

        self.client.force_authenticate(self.agent_a)
        url_prendre = reverse("dossiers:dossier-prendre-en-charge", kwargs={"dossier_pk": dossier_pk})
        url_valider = reverse("dossiers:dossier-valider", kwargs={"dossier_pk": dossier_pk})
        rep_prendre = self.client.post(url_prendre)
        rep = self.client.post(url_valider)
        if rep.status_code != status.HTTP_200_OK:
            raise AssertionError(
                "validation refusée "
                f"(prendre={rep_prendre.status_code}: {rep_prendre.data}, "
                f"valider={rep.status_code}: {rep.data})"
            )
        return Dossier.objects.get(pk=dossier_pk)

    def _deposer_preuve(self, dossier_pk, *, nom="releve.png", contenu=_JPG):
        self.client.force_authenticate(self.inv)
        url = reverse("dossiers:dossier-depot-minimum", kwargs={"dossier_pk": dossier_pk})
        return self.client.post(
            url,
            {
                "montant_depose": "25000",
                "methode_paiement": "ORANGE_MONEY",
                "reference_transaction": "OM-88217",
                "preuve": SimpleUploadedFile(nom, contenu, content_type="image/png"),
            },
            format="multipart",
        )

    # ------------------------------------------------------------------
    # Configuration Admin SGI
    # ------------------------------------------------------------------

    def test_admin_active_l_exigence_et_config_devient_visible(self):
        self.client.force_authenticate(self.admin_a)
        url = reverse("sgi:admin-depots")
        rep = self.client.put(
            url,
            {
                "exige_depot": True,
                "montant_depot_min": 25000,
                "instructions": "Versez via Orange Money.",
                "methodes_acceptees": ["ORANGE_MONEY", "WAVE"],
            },
            format="json",
        )
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        self.assertTrue(rep.data["exige_depot"])
        self.assertEqual(rep.data["montant_depot_min"], 25000)

        config = ConfigDepotMinimum.objects.get(sgi=self.sgi_a)
        self.assertEqual(config.montant_depot_min, 25000)
        self.assertEqual(
            list(config.methodes_acceptees), ["ORANGE_MONEY", "WAVE"]
        )

        # La fiche publique expose l'exigence (l'investisseur l'anticipe).
        self.client.force_authenticate(self.inv)
        rep = self.client.get(reverse("sgi:sgi-fiche", kwargs={"pk": self.sgi_a.pk}))
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        self.assertTrue(rep.data["depot_minimum"]["exige_depot"])
        self.assertEqual(rep.data["depot_minimum"]["montant_depot_min"], 25000)
        libelles = {m["code"] for m in rep.data["depot_minimum"]["methodes_acceptees"]}
        self.assertTrue({"ORANGE_MONEY", "WAVE"} <= libelles)

    def test_config_incoherente_refusee(self):
        self.client.force_authenticate(self.admin_a)
        url = reverse("sgi:admin-depots")

        # Montant nul alors que le dépôt est exigé.
        rep = self.client.put(
            url,
            {"exige_depot": True, "montant_depot_min": 0, "methodes_acceptees": ["WAVE"]},
            format="json",
        )
        self.assertEqual(rep.status_code, status.HTTP_400_BAD_REQUEST)

        # Aucune méthode acceptée.
        rep = self.client.put(
            url,
            {"exige_depot": True, "montant_depot_min": 5000, "methodes_acceptees": []},
            format="json",
        )
        self.assertEqual(rep.status_code, status.HTTP_400_BAD_REQUEST)

        # Code de méthode inconnu.
        rep = self.client.put(
            url,
            {
                "exige_depot": True, "montant_depot_min": 5000,
                "methodes_acceptees": ["PAYPAL"],
            },
            format="json",
        )
        self.assertEqual(rep.status_code, status.HTTP_400_BAD_REQUEST)

    def test_seul_admin_sgi_configure(self):
        self.client.force_authenticate(self.agent_a)
        rep = self.client.put(
            reverse("sgi:admin-depots"),
            {"exige_depot": True, "montant_depot_min": 5000, "methodes_acceptees": ["WAVE"]},
            format="json",
        )
        self.assertEqual(rep.status_code, status.HTTP_403_FORBIDDEN)

    # ------------------------------------------------------------------
    # Création du dépôt à la validation
    # ------------------------------------------------------------------

    def test_depot_cree_a_la_validation_quand_exigence_active(self):
        self._config_depot()
        dossier = self._dossier_valide()

        depot = DepotMinimum.objects.get(dossier=dossier)
        self.assertEqual(depot.statut, DepotMinimum.Statut.EN_ATTENTE)
        self.assertEqual(depot.montant_requis, 25000)
        self.assertIn("Orange Money", depot.instructions)

    def test_pas_de_depot_sans_exigence(self):
        dossier = self._dossier_valide()
        self.assertFalse(DepotMinimum.objects.filter(dossier=dossier).exists())

    # ------------------------------------------------------------------
    # Parcours investisseur
    # ------------------------------------------------------------------

    def test_investisseur_consulte_son_depot(self):
        self._config_depot()
        dossier = self._dossier_valide(self.inv_b)

        self.client.force_authenticate(self.inv_b)
        url = reverse("dossiers:dossier-depot-minimum", kwargs={"dossier_pk": dossier.pk})
        rep = self.client.get(url)
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        self.assertEqual(rep.data["montant_requis"], "25000")
        self.assertEqual(rep.data["devise"], "FCFA")
        self.assertEqual(rep.data["statut"], DepotMinimum.Statut.EN_ATTENTE)
        self.assertEqual(len(rep.data["methodes_acceptees"]), 2)

    def test_investisseur_ne_voit_pas_le_depot_d_autrui(self):
        self._config_depot()
        dossier = self._dossier_valide(self.inv_b)
        self.client.force_authenticate(self.inv)  # pas propriétaire
        url = reverse("dossiers:dossier-depot-minimum", kwargs={"dossier_pk": dossier.pk})
        self.assertEqual(self.client.get(url).status_code, status.HTTP_403_FORBIDDEN)

    def test_depot_impossible_avant_validation(self):
        self._config_depot()
        self.client.force_authenticate(self.inv)
        rep = self.client.post(
            reverse("dossiers:dossier-list-create"), {"sgi": self.sgi_a.pk}
        )
        dossier_pk = rep.data["id"]
        url = reverse("dossiers:dossier-depot-minimum", kwargs={"dossier_pk": dossier_pk})
        self.assertEqual(self.client.get(url).status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(self.client.post(url, {}).status_code, status.HTTP_409_CONFLICT)

    def test_depot_de_preuve_valide_passe_en_preuve_deposee(self):
        self._config_depot()
        dossier = self._dossier_valide()
        rep = self._deposer_preuve(dossier.pk)
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        self.assertEqual(rep.data["statut"], DepotMinimum.Statut.PREUVE_DEPOSEE)
        self.assertEqual(rep.data["montant_depose"], "25000")
        self.assertEqual(rep.data["reference_transaction"], "OM-88217")

        depot = DepotMinimum.objects.get(dossier=dossier)
        self.assertIsNotNone(depot.preuve.name)
        self.assertTrue(depot.preuve.name.startswith("dossiers/depots/"))
        self.assertTrue(os.path.exists(default_storage.path(depot.preuve.name)))
        # Trace d'audit et notification.
        self.assertTrue(
            JournalAudit.objects.filter(action=JournalAudit.Action.DEPOT_PREUVE_DEPOSEE).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                utilisateur=self.agent_a, titre="Preuve de dépôt à vérifier"
            ).exists()
        )

    def test_montant_zero_refuse(self):
        self._config_depot()
        dossier = self._dossier_valide()
        self.client.force_authenticate(self.inv)
        url = reverse("dossiers:dossier-depot-minimum", kwargs={"dossier_pk": dossier.pk})
        rep = self.client.post(
            url,
            {
                "montant_depose": "0",
                "methode_paiement": "ORANGE_MONEY",
                "reference_transaction": "OM-1",
                "preuve": SimpleUploadedFile("r.png", _JPG),
            },
            format="multipart",
        )
        self.assertEqual(rep.status_code, status.HTTP_400_BAD_REQUEST)

    def test_methode_non_acceptee_refusee(self):
        self._config_depot(methodes=("WAVE",))
        dossier = self._dossier_valide()
        self.client.force_authenticate(self.inv)
        url = reverse("dossiers:dossier-depot-minimum", kwargs={"dossier_pk": dossier.pk})
        rep = self.client.post(
            url,
            {
                "montant_depose": "25000",
                "methode_paiement": "ORANGE_MONEY",
                "reference_transaction": "OM-1",
                "preuve": SimpleUploadedFile("r.png", _JPG),
            },
            format="multipart",
        )
        self.assertEqual(rep.status_code, status.HTTP_400_BAD_REQUEST)

    def test_fichier_falsifie_refuse_par_magic_bytes(self):
        self._config_depot()
        dossier = self._dossier_valide()
        # PNG annoncé, contenu exécutable.
        rep = self._deposer_preuve(dossier.pk, nom="preuve.png", contenu=b"MZ\x90\x00exe")
        self.assertEqual(rep.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(DepotMinimum.objects.get(dossier=dossier).preuve.name)

    def test_preuve_pdf_acceptee(self):
        self._config_depot()
        dossier = self._dossier_valide()
        rep = self._deposer_preuve(dossier.pk, nom="releve.pdf", contenu=_PDF)
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        self.assertTrue(DepotMinimum.objects.get(dossier=dossier).preuve.name.endswith(".pdf"))

    def test_depot_deux_fois_sans_verification_refuse(self):
        self._config_depot()
        dossier = self._dossier_valide()
        self.assertEqual(self._deposer_preuve(dossier.pk).status_code, status.HTTP_200_OK)
        # Second dépôt alors que PREUVE_DEPOSEE → 409.
        rep = self._deposer_preuve(dossier.pk, nom="autre.png")
        self.assertEqual(rep.status_code, status.HTTP_409_CONFLICT)

    # ------------------------------------------------------------------
    # Vérification par le personnel SGI
    # ------------------------------------------------------------------

    def test_approbation_ouvre_le_compte(self):
        self._config_depot()
        dossier = self._dossier_valide()
        self._deposer_preuve(dossier.pk)
        depot = DepotMinimum.objects.get(dossier=dossier)

        self.client.force_authenticate(self.agent_a)
        rep = self.client.post(
            reverse("dossiers:depot-verifier", kwargs={"pk": depot.pk}),
            {"approuver": True, "commentaire_agent": "Conforme."},
            format="json",
        )
        self.assertEqual(rep.status_code, status.HTTP_200_OK)

        depot.refresh_from_db()
        dossier.refresh_from_db()
        self.assertEqual(depot.statut, DepotMinimum.Statut.APPROUVE)
        self.assertIsNotNone(depot.date_verification)
        self.assertEqual(dossier.statut, Dossier.Statut.ACTIF)

        for action in (
            JournalAudit.Action.DEPOT_APPROUVE,
            JournalAudit.Action.ACTIVATION_COMPTE,
            JournalAudit.Action.TRANSITION_DOSSIER,
        ):
            self.assertTrue(
                JournalAudit.objects.filter(action=action).exists(),
                f"action {action} attendue dans le journal.",
            )
        self.assertTrue(
            Notification.objects.filter(
                utilisateur=self.inv, titre="Compte activé"
            ).exists()
        )

    def test_rejet_laisse_le_dossier_valide_et_permet_redepot(self):
        self._config_depot()
        dossier = self._dossier_valide()
        self._deposer_preuve(dossier.pk)
        depot = DepotMinimum.objects.get(dossier=dossier)

        self.client.force_authenticate(self.agent_a)
        rep = self.client.post(
            reverse("dossiers:depot-verifier", kwargs={"pk": depot.pk}),
            {"approuver": False, "commentaire_agent": "Référence introuvable."},
            format="json",
        )
        self.assertEqual(rep.status_code, status.HTTP_200_OK)

        depot.refresh_from_db()
        dossier.refresh_from_db()
        self.assertEqual(depot.statut, DepotMinimum.Statut.REJETE)
        self.assertEqual(dossier.statut, Dossier.Statut.VALIDE)
        self.assertTrue(
            Notification.objects.filter(
                utilisateur=self.inv, titre="Preuve de dépôt rejetée"
            ).exists()
        )

        # L'investisseur redépose une preuve corrigée.
        rep = self._deposer_preuve(dossier.pk, nom="releve2.png")
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        self.assertEqual(rep.data["statut"], DepotMinimum.Statut.PREUVE_DEPOSEE)

    def test_motif_de_rejet_obligatoire(self):
        self._config_depot()
        dossier = self._dossier_valide()
        self._deposer_preuve(dossier.pk)
        depot = DepotMinimum.objects.get(dossier=dossier)
        self.client.force_authenticate(self.agent_a)
        rep = self.client.post(
            reverse("dossiers:depot-verifier", kwargs={"pk": depot.pk}),
            {"approuver": False},
            format="json",
        )
        self.assertEqual(rep.status_code, status.HTTP_400_BAD_REQUEST)

    def test_verification_sans_preuve_deposee_refusee(self):
        self._config_depot()
        dossier = self._dossier_valide()
        depot = DepotMinimum.objects.get(dossier=dossier)  # EN_ATTENTE
        self.client.force_authenticate(self.agent_a)
        rep = self.client.post(
            reverse("dossiers:depot-verifier", kwargs={"pk": depot.pk}),
            {"approuver": True},
            format="json",
        )
        self.assertEqual(rep.status_code, status.HTTP_409_CONFLICT)

    def test_liste_depots_cloisonnee_a_la_sgi(self):
        self._config_depot()
        dossier_a = self._dossier_valide()
        self._deposer_preuve(dossier_a.pk)

        self.client.force_authenticate(self.admin_a)
        url = reverse("dossiers:depot-liste")
        rep = self.client.get(url)
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        self.assertEqual(len(rep.data), 1)
        self.assertEqual(rep.data[0]["dossier_reference"], dossier_a.reference)

        # L'investisseur n'a pas accès à la file agent.
        self.client.force_authenticate(self.inv)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_403_FORBIDDEN)

    # ------------------------------------------------------------------
    # Activation directe (SGI sans exigence) / blocage
    # ------------------------------------------------------------------

    def test_activation_directe_sans_exigence(self):
        dossier = self._dossier_valide()
        self.client.force_authenticate(self.agent_a)
        rep = self.client.post(
            reverse("dossiers:dossier-activer", kwargs={"dossier_pk": dossier.pk})
        )
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        dossier.refresh_from_db()
        self.assertEqual(dossier.statut, Dossier.Statut.ACTIF)

    def test_activation_bloquee_si_depot_exige_et_non_approuve(self):
        self._config_depot()
        dossier = self._dossier_valide()
        self.client.force_authenticate(self.agent_a)
        rep = self.client.post(
            reverse("dossiers:dossier-activer", kwargs={"dossier_pk": dossier.pk})
        )
        self.assertEqual(rep.status_code, status.HTTP_400_BAD_REQUEST)
        dossier.refresh_from_db()
        self.assertEqual(dossier.statut, Dossier.Statut.VALIDE)

    def test_activation_seulement_depuis_valide(self):
        dossier = self._dossier_valide()
        self.client.force_authenticate(self.inv)
        rep = self.client.post(
            reverse("dossiers:dossier-activer", kwargs={"dossier_pk": dossier.pk})
        )
        # Un investisseur n'est pas personnel SGI.
        self.assertEqual(rep.status_code, status.HTTP_403_FORBIDDEN)