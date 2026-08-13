"""Tests de l'espace Admin Général (étape 3F) : UC20 (SGI), UC21
(utilisateurs), UC22 (tableau de bord) — plus le blocage des
soumissions quand une SGI partenaire est suspendue.
"""

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from audit.models import JournalAudit
from audit.services import journaliser
from comptes.models import Role, Utilisateur
from dossiers.models import Dossier
from dossiers.tests import _configurer_parcours
from sgi.models import SGI

MOT_DE_PASSE = "S3curise!2026"


class EspaceAdminGeneralTestBase(APITestCase):
    def setUp(self):
        self.sgi_a = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.sgi_b = SGI.objects.create(nom="SGI Bêta", code_sgi="SGIB")

        self.admin_general = Utilisateur.objects.create_user(
            "super@example.com", MOT_DE_PASSE, role=Role.Code.ADMIN_GENERAL,
        )
        self.admin_general_b = Utilisateur.objects.create_user(
            "super2@example.com", MOT_DE_PASSE, role=Role.Code.ADMIN_GENERAL,
        )
        self.admin_sgi = Utilisateur.objects.create_user(
            "admsgi@example.com", MOT_DE_PASSE,
            role=Role.Code.ADMIN_SGI, sgi=self.sgi_a,
        )
        self.agent = Utilisateur.objects.create_user(
            "agent@example.com", MOT_DE_PASSE,
            role=Role.Code.AGENT_SGI, sgi=self.sgi_a,
        )
        self.investisseur = Utilisateur.objects.create_user(
            "inv@example.com", MOT_DE_PASSE,
        )


class UC20GestionsSGIAPITests(EspaceAdminGeneralTestBase):
    """Catalogue des SGI partenaires : liste, création, suspension."""

    def test_acces_reserve_a_l_admin_general(self):
        for utilisateur in (self.investisseur, self.agent, self.admin_sgi):
            self.client.force_authenticate(utilisateur)
            reponse = self.client.get(reverse("administration:sgi-list-create"))
            self.assertEqual(reponse.status_code, 403)

    def test_creation_sgi_journalisee(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self.client.post(
            reverse("administration:sgi-list-create"),
            {"nom": "SGI Gamma", "code_sgi": "SGIG"},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)
        self.assertEqual(reponse.data["nom"], "SGI Gamma")

        trace = JournalAudit.objects.filter(
            action=JournalAudit.Action.CREATION_SGI,
            entite_concernee="SGI",
            utilisateur=self.admin_general,
        ).first()
        self.assertIsNotNone(trace)
        self.assertEqual(trace.apres["code_sgi"], "SGIG")

    def test_creation_sgi_code_reglementaire_unique(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self.client.post(
            reverse("administration:sgi-list-create"),
            {"nom": "Doublon", "code_sgi": self.sgi_a.code_sgi},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_liste_sgi_avec_compteurs_internes(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self.client.get(reverse("administration:sgi-list-create"))
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        resultats = reponse.data["results"]
        # SGI Alpha : admin SGI + agent ; SGI Bêta : aucun compte.
        self.assertEqual(resultats[0]["nom"], "SGI Alpha")
        self.assertEqual(resultats[0]["nb_utilisateurs"], 2)
        self.assertEqual(resultats[0]["nb_dossiers"], 0)

    def test_desactivation_sgi_journalisee(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self.client.patch(
            reverse("administration:sgi-detail", kwargs={"pk": self.sgi_a.pk}),
            {"est_active": False},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.sgi_a.refresh_from_db()
        self.assertFalse(self.sgi_a.est_active)

        trace = JournalAudit.objects.filter(
            action=JournalAudit.Action.MODIFICATION_SGI,
            entite_id=str(self.sgi_a.pk),
        ).first()
        self.assertIsNotNone(trace)
        self.assertTrue(trace.avant["est_active"])
        self.assertFalse(trace.apres["est_active"])

    def test_pas_de_suppression_destructive(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self.client.delete(
            reverse("administration:sgi-detail", kwargs={"pk": self.sgi_a.pk}),
        )
        self.assertEqual(reponse.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertTrue(SGI.objects.filter(pk=self.sgi_a.pk).exists())

    def test_soumission_refusee_si_sgi_suspendue(self):
        """UC20 → UC10 : une SGI désactivée ne reçoit plus de dossiers."""
        self.client.force_authenticate(self.investisseur)
        champs = _configurer_parcours(self.sgi_a)
        dossier = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi_a,
        )
        for champ, valeur in [
            (champs["p"], "Morale"),
            (champs["a"], "Awa"),
            (champs["b"], "RCCM-123"),
        ]:
            Dossier.objects.get(pk=dossier.pk).valeurs_champs.create(champ=champ, valeur=valeur)

        self.sgi_a.est_active = False
        self.sgi_a.save(update_fields=["est_active"])

        reponse = self.client.post(
            reverse("dossiers:dossier-soumettre", kwargs={"dossier_pk": dossier.pk}),
        )
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)
        dossier.refresh_from_db()
        self.assertEqual(dossier.statut, Dossier.Statut.BROUILLON)


class UC21GestionsUtilisateursAPITests(EspaceAdminGeneralTestBase):
    """Comptes internes : création, rôles, activation, garde-fous."""

    def _creer_agent(self, **extra):
        donnees = {
            "email": "nouvel-agent@example.com",
            "prenom": "Kadi",
            "nom": "Traoré",
            "role": Role.Code.AGENT_SGI,
            "sgi": str(self.sgi_a.pk),
            "mot_de_passe": MOT_DE_PASSE,
        }
        donnees.update(extra)
        return self.client.post(
            reverse("administration:utilisateurs-list-create"), donnees, format="json",
        )

    def test_creation_compte_interne_avec_mot_de_passe_hache(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self._creer_agent()
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)

        utilisateur = Utilisateur.objects.get(email="nouvel-agent@example.com")
        self.assertTrue(utilisateur.check_password(MOT_DE_PASSE))
        self.assertNotEqual(utilisateur.password, MOT_DE_PASSE)
        trace = JournalAudit.objects.filter(
            action=JournalAudit.Action.CREATION_UTILISATEUR,
            utilisateur=self.admin_general,
        ).first()
        self.assertIsNotNone(trace)
        self.assertEqual(trace.apres["role"], Role.Code.AGENT_SGI)

    def test_mot_de_passe_initial_obligatoire(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self._creer_agent(mot_de_passe="")
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_compte_investisseur_interdit_ici(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self._creer_agent(role=Role.Code.INVESTISSEUR, sgi="")
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_role_sgi_coherent_impose(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self._creer_agent(sgi="")
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_email_deja_pris_refuse(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self._creer_agent(email=self.agent.email)
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_liste_filtree_par_role_et_activation(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self.client.get(
            reverse("administration:utilisateurs-list-create"),
            {"role": Role.Code.AGENT_SGI, "actif": "true"},
        )
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        emails = {row["email"] for row in reponse.data["results"]}
        self.assertEqual(emails, {"agent@example.com"})

    def test_desactivation_compte_journalisee(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self.client.patch(
            reverse("administration:utilisateurs-detail", kwargs={"pk": self.agent.pk}),
            {"is_active": False},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.agent.refresh_from_db()
        self.assertFalse(self.agent.is_active)
        trace = JournalAudit.objects.filter(
            action=JournalAudit.Action.MODIFICATION_UTILISATEUR,
            entite_id=str(self.agent.pk),
        ).first()
        self.assertIsNotNone(trace)
        self.assertTrue(trace.avant["is_active"])
        self.assertFalse(trace.apres["is_active"])

    def test_auto_desactivation_refusee(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self.client.patch(
            reverse("administration:utilisateurs-detail",
                    kwargs={"pk": self.admin_general.pk}),
            {"is_active": False},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_changement_de_son_propre_role_refuse(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self.client.patch(
            reverse("administration:utilisateurs-detail",
                    kwargs={"pk": self.admin_general.pk}),
            {"role": Role.Code.AGENT_SGI, "sgi": str(self.sgi_a.pk)},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_dernier_admin_general_protege(self):
        """On peut rétrograder un admin tant qu'il en reste un actif…"""
        self.client.force_authenticate(self.admin_general)
        reponse = self.client.patch(
            reverse("administration:utilisateurs-detail",
                    kwargs={"pk": self.admin_general_b.pk}),
            {"is_active": False},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)

        # …mais jamais le dernier : le système garde toujours un gouvernail.
        reponse = self.client.patch(
            reverse("administration:utilisateurs-detail",
                    kwargs={"pk": self.admin_general.pk}),
            {"is_active": False},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)
        self.admin_general.refresh_from_db()
        self.assertTrue(self.admin_general.is_active)

    def test_changement_de_role_journalise(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self.client.patch(
            reverse("administration:utilisateurs-detail",
                    kwargs={"pk": self.admin_general_b.pk}),
            {"role": Role.Code.AGENT_SGI, "sgi": str(self.sgi_b.pk)},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        trace = JournalAudit.objects.filter(
            action=JournalAudit.Action.MODIFICATION_UTILISATEUR,
            entite_id=str(self.admin_general_b.pk),
        ).latest("date_action")
        self.assertEqual(trace.avant["role"], Role.Code.ADMIN_GENERAL)
        self.assertEqual(trace.apres["role"], Role.Code.AGENT_SGI)


class UC22TableauDeBordAPITests(EspaceAdminGeneralTestBase):
    """Agrégats globaux de pilotage (consultation seule)."""

    def setUp(self):
        super().setUp()
        self.dossier_brouillon = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi_a,
        )
        self.dossier_soumis = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi_b,
            statut=Dossier.Statut.SOUMIS, date_soumission=timezone.now(),
        )
        journaliser(
            self.agent, JournalAudit.Action.TRANSITION_DOSSIER,
            "Dossier", str(self.dossier_soumis.pk),
            apres={"statut": Dossier.Statut.SOUMIS},
        )

    def test_acces_reserve_a_l_admin_general(self):
        self.client.force_authenticate(self.investisseur)
        reponse = self.client.get(reverse("administration:dashboard"))
        self.assertEqual(reponse.status_code, 403)

    def test_dashboard_chiffres_coherents(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self.client.get(reverse("administration:dashboard"))
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)

        dossiers = reponse.data["dossiers"]
        self.assertEqual(dossiers["total"], 2)
        self.assertEqual(
            dossiers["par_statut"][Dossier.Statut.BROUILLON], 1,
        )
        self.assertEqual(dossiers["par_statut"][Dossier.Statut.SOUMIS], 1)
        self.assertEqual(dossiers["soumis_aujourd_hui"], 1)

        sgi = reponse.data["sgi"]
        self.assertEqual(sgi["total"], 2)
        self.assertEqual(sgi["actives"], 2)
        self.assertEqual(sgi["sans_convention_publiee"], 2)

        utilisateurs = reponse.data["utilisateurs"]
        self.assertEqual(utilisateurs["par_role"][Role.Code.ADMIN_GENERAL], 2)
        self.assertEqual(utilisateurs["par_role"][Role.Code.INVESTISSEUR], 1)

        self.assertEqual(len(reponse.data["activite_recente"]), 1)
        self.assertEqual(
            reponse.data["activite_recente"][0]["action"],
            JournalAudit.Action.TRANSITION_DOSSIER,
        )