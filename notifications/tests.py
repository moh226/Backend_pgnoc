"""Étape 3C — Notifications : génération automatique (§7) + endpoints."""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from comptes.models import Role, Utilisateur
from dossiers.models import ChampKYC, Dossier, EtapeKYC, ValeurChamp
from dossiers.tests import _signer_dossier
from dossiers.workflow import transiter
from notifications.models import Notification
from sgi.models import SGI


class GenerationNotificationsTests(APITestCase):
    """Chaque transition crée les notifications attendues (§7)."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.investisseur = Utilisateur.objects.create_user("inv@example.com", "S3curise!2026")
        role_agent = Role.objects.filter(code="AGENT_SGI").first()
        role_admin = Role.objects.filter(code="ADMIN_SGI").first()
        self.agent_a = Utilisateur.objects.create_user(
            "agent.a@example.com", "S3curise!2026", sgi=self.sgi, role=role_agent,
        )
        self.agent_b = Utilisateur.objects.create_user(
            "agent.b@example.com", "S3curise!2026", sgi=self.sgi, role=role_agent,
        )
        self.admin = Utilisateur.objects.create_user(
            "admin@example.com", "S3curise!2026", sgi=self.sgi, role=role_admin,
        )
        self.autre_sgi = SGI.objects.create(nom="SGI Bêta", code_sgi="SGIB")
        Utilisateur.objects.create_user(
            "agent.autre@example.com", "S3curise!2026",
            sgi=self.autre_sgi, role=role_agent,
        )
        self.dossier = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi,
        )
        etape = EtapeKYC.objects.create(sgi=self.sgi, nom="Identité", ordre=1)
        champ = ChampKYC.objects.create(
            etape=etape, code="nom", nom="Nom", type=ChampKYC.TypeChamp.TEXTE_COURT,
        )
        ValeurChamp.objects.create(dossier=self.dossier, champ=champ, valeur="Awa")

    def test_soumission_notifie_les_agents_et_admin_de_la_sgi(self):
        _signer_dossier(self.dossier)
        transiter(self.dossier, Dossier.Statut.SOUMIS, utilisateur=self.investisseur)

        notifs = Notification.objects.filter(
            type_notif=Notification.TypeNotif.DOSSIER,
        )
        self.assertEqual(notifs.count(), 3)  # agent.a, agent.b, admin
        destinataires = set(notifs.values_list("utilisateur__email", flat=True))
        self.assertEqual(
            destinataires,
            {"agent.a@example.com", "agent.b@example.com", "admin@example.com"},
        )
        self.assertNotIn("agent.autre@example.com", destinataires)
        self.assertIn("soumis", notifs.first().message.lower())

    def test_transition_instruction_notifie_l_investisseur(self):
        _signer_dossier(self.dossier)
        transiter(self.dossier, Dossier.Statut.SOUMIS, utilisateur=self.investisseur)
        transiter(
            self.dossier, Dossier.Statut.EN_INSTRUCTION,
            agent=self.agent_a, utilisateur=self.agent_a,
        )
        notification = Notification.objects.get(utilisateur=self.investisseur)
        self.assertIn("instruction", notification.message.lower())

    def test_rejet_notifie_l_investisseur_avec_motif(self):
        _signer_dossier(self.dossier)
        transiter(self.dossier, Dossier.Statut.SOUMIS, utilisateur=self.investisseur)
        transiter(
            self.dossier, Dossier.Statut.EN_INSTRUCTION,
            agent=self.agent_a, utilisateur=self.agent_a,
        )
        transiter(
            self.dossier, Dossier.Statut.REJETE, agent=self.agent_a,
            motif_rejet="CNIB illisible", utilisateur=self.agent_a,
        )
        notification = Notification.objects.get(
            utilisateur=self.investisseur, titre="Dossier rejeté",
        )
        self.assertIn("CNIB illisible", notification.message)


class EndpointsNotificationsTests(APITestCase):
    """Liste, compteur non lues, marquage — cloisonnés au destinataire."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.investisseur = Utilisateur.objects.create_user("inv@example.com", "S3curise!2026")
        self.autre = Utilisateur.objects.create_user("autre@example.com", "S3curise!2026")
        self.notification = Notification.objects.create(
            utilisateur=self.investisseur,
            titre="Dossier validé",
            message="Votre dossier PGNOC-2026-000001 a été validé.",
        )
        self.client.force_authenticate(self.investisseur)
        self.url = reverse("notifications:liste")

    def test_liste_paginee_des_miennes(self):
        reponse = self.client.get(self.url)
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.assertEqual(reponse.data["count"], 1)
        self.assertEqual(reponse.data["results"][0]["titre"], "Dossier validé")
        self.assertIn("type_libelle", reponse.data["results"][0])

    def test_filtre_non_lues(self):
        reponse = self.client.get(self.url, {"non_lues": "true"})
        self.assertEqual(reponse.data["count"], 1)
        self.notification.lue = True
        self.notification.save(update_fields=["lue"])
        reponse = self.client.get(self.url, {"non_lues": "true"})
        self.assertEqual(reponse.data["count"], 0)

    def test_compteur_non_lues(self):
        reponse = self.client.get(reverse("notifications:non-lues"))
        self.assertEqual(reponse.data["compte"], 1)

    def test_marquer_lue(self):
        reponse = self.client.post(
            reverse("notifications:marquer-lue", kwargs={"pk": self.notification.pk}),
        )
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.assertTrue(reponse.data["lue"])
        self.notification.refresh_from_db()
        self.assertTrue(self.notification.lue)

    def test_on_ne_voit_ni_marque_les_notifications_des_autres(self):
        self.client.force_authenticate(self.autre)
        reponse = self.client.get(self.url)
        self.assertEqual(reponse.data["count"], 0)
        reponse = self.client.post(
            reverse("notifications:marquer-lue", kwargs={"pk": self.notification.pk}),
        )
        self.assertEqual(reponse.status_code, status.HTTP_404_NOT_FOUND)