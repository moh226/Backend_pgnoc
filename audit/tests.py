"""Étape 3A/3B — Journal d'audit : modèle, service, branchements, lecture (CREPMF §8.3)."""

import csv
import io

import django.db as django_db
from django.db import connection, transaction
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase, APIRequestFactory

from audit.models import JournalAudit
from audit.services import journaliser
from comptes.models import Role, Utilisateur
from dossiers.models import Dossier, ValeurChamp
from dossiers.tests import _signer_dossier
from dossiers.workflow import transiter
from sgi.models import SGI


class JournalisationTransitionTests(APITestCase):
    """Toute transition de dossier produit une trace avant/après."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.investisseur = Utilisateur.objects.create_user("inv@example.com", "S3curise!2026")
        role_agent = Role.objects.filter(code="AGENT_SGI").first()
        self.agent = Utilisateur.objects.create_user(
            "agent@example.com", "S3curise!2026", sgi=self.sgi, role=role_agent,
        )
        self.dossier = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi,
        )

    def _remplir_complet(self):
        from dossiers.models import ChampKYC, EtapeKYC

        etape = EtapeKYC.objects.create(sgi=self.sgi, nom="Identité", ordre=1)
        champ = ChampKYC.objects.create(
            etape=etape, code="nom", nom="Nom", type=ChampKYC.TypeChamp.TEXTE_COURT,
        )
        ValeurChamp.objects.create(dossier=self.dossier, champ=champ, valeur="Awa")

    def test_transition_par_le_workflow_genere_une_trace(self):
        self._remplir_complet()
        _signer_dossier(self.dossier)
        transiter(self.dossier, Dossier.Statut.SOUMIS, utilisateur=self.investisseur)

        trace = JournalAudit.objects.filter(
            action=JournalAudit.Action.TRANSITION_DOSSIER,
        ).get()
        self.assertEqual(trace.entite_concernee, "Dossier")
        self.assertEqual(trace.entite_id, str(self.dossier.pk))
        self.assertEqual(trace.utilisateur_id, self.investisseur.pk)
        self.assertEqual(trace.avant["statut"], Dossier.Statut.BROUILLON)
        self.assertEqual(trace.apres["statut"], Dossier.Statut.SOUMIS)
        self.assertIsNotNone(trace.apres["date_soumission"])

    def test_transition_echouee_ne_produit_aucune_trace(self):
        # Soumission interdite (dossier vide) : rien ne doit être journalisé.
        with self.assertRaises(Exception):
            transiter(self.dossier, Dossier.Statut.SOUMIS)
        self.assertEqual(
            JournalAudit.objects.filter(
                action=JournalAudit.Action.TRANSITION_DOSSIER,
            ).count(),
            0,
        )

    def test_chronologie_des_transitions_completes(self):
        self._remplir_complet()
        _signer_dossier(self.dossier)
        transiter(self.dossier, Dossier.Statut.SOUMIS, utilisateur=self.investisseur)
        transiter(
            self.dossier, Dossier.Statut.EN_INSTRUCTION,
            agent=self.agent, utilisateur=self.agent,
        )
        transiter(self.dossier, Dossier.Statut.REJETE, agent=self.agent,
                  motif_rejet="Pièce illisible", utilisateur=self.agent)

        traces = list(JournalAudit.objects.filter(
            action=JournalAudit.Action.TRANSITION_DOSSIER,
        ).order_by("date_action"))
        self.assertEqual(len(traces), 3)
        self.assertEqual(
            [t.apres["statut"] for t in traces],
            [Dossier.Statut.SOUMIS, Dossier.Statut.EN_INSTRUCTION, Dossier.Statut.REJETE],
        )
        derniere = traces[-1]
        self.assertEqual(derniere.apres["motif_rejet"], "Pièce illisible")


class JournalisationConnexionInscriptionTests(APITestCase):
    """Connexion et inscription sont tracées avec IP / User-Agent."""

    def test_connexion_reussie_genere_une_trace(self):
        Utilisateur.objects.create_user("inv@example.com", "S3curise!2026")
        reponse = self.client.post(
            reverse("comptes:login"),
            {"email": "inv@example.com", "password": "S3curise!2026"},
        )
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)

        trace = JournalAudit.objects.filter(action=JournalAudit.Action.CONNEXION).get()
        self.assertEqual(trace.utilisateur.email, "inv@example.com")
        self.assertFalse(trace.apres["email"] != "inv@example.com")

    def test_connexion_avec_ip_et_user_agent(self):
        Utilisateur.objects.create_user("inv@example.com", "S3curise!2026")
        self.client.post(
            reverse("comptes:login"),
            {"email": "inv@example.com", "password": "S3curise!2026"},
            HTTP_USER_AGENT="pytest-agent/1.0",
        )
        trace = JournalAudit.objects.get(action=JournalAudit.Action.CONNEXION)
        self.assertEqual(trace.user_agent, "pytest-agent/1.0")
        self.assertIsNotNone(trace.ip_address)

    def test_inscription_genere_une_trace(self):
        reponse = self.client.post(
            reverse("comptes:register"),
            {
                "email": "nouveau@example.com",
                "password": "S3curise!2026",
                "password_confirmation": "S3curise!2026",
            },
        )
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)
        trace = JournalAudit.objects.filter(action=JournalAudit.Action.INSCRIPTION).get()
        utilisateur = Utilisateur.objects.get(email="nouveau@example.com")
        self.assertEqual(trace.entite_id, str(utilisateur.pk))
        self.assertEqual(trace.apres["role"], Role.Code.INVESTISSEUR)

    def test_creation_dossier_par_api_genere_une_trace(self):
        sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        investisseur = Utilisateur.objects.create_user("inv@example.com", "S3curise!2026")
        self.client.force_authenticate(investisseur)
        reponse = self.client.post(
            reverse("dossiers:dossier-list-create"), {"sgi": sgi.pk},
        )
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)

        trace = JournalAudit.objects.filter(
            action=JournalAudit.Action.CREATION_DOSSIER,
        ).get()
        self.assertEqual(trace.entite_id, str(reponse.data["id"]))


class JournalImmuabiliteTests(APITestCase):
    """INSERT ONLY : aucune modification ni suppression possible."""

    def setUp(self):
        self.utilisateur = Utilisateur.objects.create_user("inv@example.com", "S3curise!2026")
        self.trace = JournalAudit.objects.create(
            utilisateur=self.utilisateur,
            action=JournalAudit.Action.CONNEXION,
            entite_concernee="Utilisateur",
            entite_id=str(self.utilisateur.pk),
        )

    def test_modification_refusee(self):
        self.trace.user_agent = "modifie"
        with self.assertRaises(ValueError):
            self.trace.save()

    def test_suppression_refusee(self):
        with self.assertRaises(ValueError):
            self.trace.delete()
        self.assertTrue(JournalAudit.objects.filter(pk=self.trace.pk).exists())

    def test_update_masse_refuse(self):
        with self.assertRaises(ValueError):
            JournalAudit.objects.filter(pk=self.trace.pk).update(user_agent="masse")
        with self.assertRaises(ValueError):
            JournalAudit.objects.all().delete()

    def test_sql_brut_interdit_par_le_trigger_postgres(self):
        """Le verrou est porté par la base, pas seulement par l'ORM.

        Un UPDATE ou DELETE envoyé directement en SQL (bulk, scripts,
        maintenance) déclenche l'exception PostgreSQL dédiée. Le bloc
        `atomic` doit sortir PAR exception (assertRaises à l'extérieur)
        pour que Django rétablisse le savepoint, faute de quoi la
        transaction reste « aborted ».
        """
        with self.assertRaises(django_db.Error):
            with transaction.atomic():
                with connection.cursor() as curseur:
                    curseur.execute(
                        "UPDATE audit_journalaudit SET user_agent='brut' "
                        "WHERE id = %s",
                        [str(self.trace.pk)],
                    )
        with self.assertRaises(django_db.Error):
            with transaction.atomic():
                with connection.cursor() as curseur:
                    curseur.execute(
                        "DELETE FROM audit_journalaudit WHERE id = %s",
                        [str(self.trace.pk)],
                    )
        self.assertTrue(JournalAudit.objects.filter(pk=self.trace.pk).exists())

    def test_insert_via_orm_toujours_possible(self):
        trace = JournalAudit.objects.create(
            utilisateur=self.utilisateur,
            action=JournalAudit.Action.CONNEXION,
            entite_concernee="Utilisateur",
            entite_id=str(self.utilisateur.pk),
        )
        self.assertTrue(JournalAudit.objects.filter(pk=trace.pk).exists())


class JournalLectureAdminGeneralTests(APITestCase):
    """3B : UC23 — lecture du journal réservée à l'Admin Général."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        role_admin_general = Role.objects.get(code=Role.Code.ADMIN_GENERAL)
        role_agent = Role.objects.get(code=Role.Code.AGENT_SGI)
        self.admin_general = Utilisateur.objects.create_user(
            "super@example.com", "S3curise!2026", role=role_admin_general,
        )
        self.agent = Utilisateur.objects.create_user(
            "agent@example.com", "S3curise!2026", sgi=self.sgi, role=role_agent,
        )
        self.utilisateur = Utilisateur.objects.create_user("inv@example.com", "S3curise!2026")
        journaliser(
            self.utilisateur, JournalAudit.Action.CONNEXION,
            "Utilisateur", str(self.utilisateur.pk),
            apres={"email": "inv@example.com"},
        )
        journaliser(
            self.agent, JournalAudit.Action.CONNEXION,
            "Utilisateur", str(self.agent.pk), apres={"email": "agent@example.com"},
        )
        self.url = reverse("audit:journal-list")

    def test_non_admin_general_refuse(self):
        self.client.force_authenticate(self.agent)
        self.assertEqual(
            self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN,
        )

    def test_admin_general_voit_toutes_les_traces_paginees(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self.client.get(self.url)
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.assertEqual(reponse.data["count"], 2)
        self.assertEqual(reponse.data["results"][0]["action_libelle"], "Connexion")

    def test_filtre_par_email_et_action(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self.client.get(self.url, {"email": "agent@"})
        self.assertEqual(reponse.data["count"], 1)
        self.assertEqual(reponse.data["results"][0]["utilisateur_email"], "agent@example.com")

        reponse = self.client.get(self.url, {"action": "INSCRIPTION"})
        self.assertEqual(reponse.data["count"], 0)

    def test_filtre_par_entite_et_dates(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self.client.get(self.url, {"entite_concernee": "Utilisateur"})
        self.assertEqual(reponse.data["count"], 2)
        reponse = self.client.get(self.url, {"date_debut": "2026-01-01", "date_fin": "2026-12-31"})
        self.assertEqual(reponse.data["count"], 2)
        reponse = self.client.get(self.url, {"date_debut": "1999-01-01", "date_fin": "1999-12-31"})
        self.assertEqual(reponse.data["count"], 0)
        reponse = self.client.get(self.url, {"date_debut": "nimporte"})
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_export_csv(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self.client.get(reverse("audit:journal-export"))
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.assertEqual(reponse["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment;", reponse["Content-Disposition"])

        lecteur = csv.reader(io.StringIO(reponse.content.decode("utf-8")))
        lignes = list(lecteur)
        self.assertEqual(lignes[0][0], "date_action")
        self.assertEqual(len(lignes), 3)  # en-tête + 2 traces
        corps = lignes[1:]
        self.assertEqual({l[1] for l in corps}, {"inv@example.com", "agent@example.com"})

    def test_export_csv_respecte_les_filtres(self):
        self.client.force_authenticate(self.admin_general)
        reponse = self.client.get(
            reverse("audit:journal-export"), {"email": "agent@"},
        )
        lignes = list(csv.reader(io.StringIO(reponse.content.decode("utf-8"))))
        self.assertEqual(len(lignes), 2)  # en-tête + 1 trace
        self.assertEqual(lignes[1][1], "agent@example.com")

    def test_export_csv_neutralise_les_formules(self):
        """Un en-tête HTTP forgé par un attaquant (User-Agent amorcé par
        `=`/`+`/`-`/`@`) ne doit jamais devenir une formule exécutable à
        l'ouverture du CSV dans Excel : la cellule est préfixée."""
        requete = APIRequestFactory().get("/")
        requete.META["HTTP_USER_AGENT"] = "=cmd|'/C calc'!A0"
        journaliser(
            self.agent, JournalAudit.Action.TRANSITION_DOSSIER,
            "Dossier", "abc", apres={"motif_rejet": "=2+2"},
            requete=requete,
        )
        self.client.force_authenticate(self.admin_general)
        contenu = self.client.get(
            reverse("audit:journal-export"),
        ).content.decode("utf-8")
        self.assertIn("'=cmd|'/C calc'!A0", contenu)