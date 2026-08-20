"""Tests d'API « moi » : profil personnel et changement de mot de passe.

GET/PATCH /api/comptes/moi/profil/
POST /api/comptes/moi/mot-de-passe/
"""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from audit.models import JournalAudit
from comptes.models import Role, Utilisateur
from sgi.models import SGI


class ProfilMoiAPITests(APITestCase):
    """GET/PATCH /api/comptes/moi/profil/"""

    url = reverse("comptes:moi-profil")

    def setUp(self):
        self.investisseur = Utilisateur.objects.create_user(
            "inv@example.com", "S3curise!2026",
        )
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.agent = Utilisateur.objects.create_user(
            "agent@example.com", "S3curise!2026",
            role=Role.Code.AGENT_SGI, sgi=self.sgi,
        )
        self.admin_sgi = Utilisateur.objects.create_user(
            "admin@sgi.example.com", "S3curise!2026",
            role=Role.Code.ADMIN_SGI, sgi=self.sgi,
        )
        self.admin_general = Utilisateur.objects.create_user(
            "admin@example.com", "S3curise!2026",
            role=Role.Code.ADMIN_GENERAL,
        )
        self.agent.profil_agent_sgi.matricule = "AGT-001"
        self.agent.profil_agent_sgi.save(update_fields=["matricule"])
        self.admin_sgi.profil_admin_sgi.fonction = "Responsable Conformité"
        self.admin_sgi.profil_admin_sgi.save(update_fields=["fonction"])

    def test_get_necessite_authentification(self):
        reponse = self.client.get(self.url)
        self.assertEqual(reponse.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_investisseur_renvoie_type_personne(self):
        self.client.force_authenticate(self.investisseur)

        reponse = self.client.get(self.url)

        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.assertIn("type_personne", reponse.data)
        self.assertNotIn("matricule", reponse.data)
        self.assertNotIn("fonction", reponse.data)
        self.assertEqual(reponse.data["role"], Role.Code.INVESTISSEUR)
        self.assertIsNone(reponse.data["sgi"])

    def test_get_agent_renvoie_matricule(self):
        self.client.force_authenticate(self.agent)

        reponse = self.client.get(self.url)

        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.assertNotIn("type_personne", reponse.data)
        self.assertEqual(reponse.data["matricule"], "AGT-001")
        self.assertEqual(reponse.data["sgi"], "SGI Alpha")

    def test_get_admin_sgi_renvoie_fonction(self):
        self.client.force_authenticate(self.admin_sgi)

        reponse = self.client.get(self.url)

        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.assertNotIn("matricule", reponse.data)
        self.assertEqual(reponse.data["fonction"], "Responsable Conformité")

    def test_patch_investisseur_met_a_jour_prenom_et_type_personne(self):
        self.client.force_authenticate(self.investisseur)

        reponse = self.client.patch(
            self.url,
            {"prenom": "Awa", "nom": "Koné", "type_personne": "MORALE"},
            format="json",
        )

        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.investisseur.refresh_from_db()
        self.assertEqual(self.investisseur.prenom, "Awa")
        self.assertEqual(self.investisseur.nom, "Koné")
        self.assertEqual(
            self.investisseur.profil_investisseur.type_personne, "MORALE"
        )

    def test_patch_investisseur_empeche_type_personne_invalide(self):
        self.client.force_authenticate(self.investisseur)

        reponse = self.client.patch(
            self.url, {"type_personne": "ALIEN"}, format="json",
        )

        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_ignore_champ_d_un_autre_role(self):
        self.client.force_authenticate(self.agent)

        reponse = self.client.patch(
            self.url, {"type_personne": "MORALE", "matricule": "AGT-002"},
            format="json",
        )

        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.profil_agent_sgi.matricule, "AGT-002")

    def test_patch_email_est_ignore(self):
        self.client.force_authenticate(self.investisseur)

        reponse = self.client.patch(self.url, {"email": "pirate@example.com"})

        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.investisseur.refresh_from_db()
        self.assertEqual(self.investisseur.email, "inv@example.com")

    def test_patch_trace_une_entree_audit(self):
        self.client.force_authenticate(self.investisseur)

        self.client.patch(self.url, {"prenom": "Awa"}, format="json")

        traces = JournalAudit.objects.filter(
            utilisateur=self.investisseur,
            action=JournalAudit.Action.MODIFICATION_PROFIL,
        )
        self.assertEqual(traces.count(), 1)
        self.assertEqual(traces.first().avant["prenom"], "")
        self.assertEqual(traces.first().apres["prenom"], "Awa")


class ChangerMotDePasseAPITests(APITestCase):
    """POST /api/comptes/moi/mot-de-passe/"""

    url = reverse("comptes:moi-mot-de-passe")

    def setUp(self):
        self.utilisateur = Utilisateur.objects.create_user(
            "inv@example.com", "S3curise!2026",
        )

    def test_necessite_authentification(self):
        reponse = self.client.post(
            self.url,
            {
                "ancien_mot_de_passe": "S3curise!2026",
                "nouveau_mot_de_passe": "Autre!MotDePasse2026",
                "confirmation": "Autre!MotDePasse2026",
            },
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_ancien_mot_de_passe_incorrect(self):
        self.client.force_authenticate(self.utilisateur)

        reponse = self.client.post(
            self.url,
            {
                "ancien_mot_de_passe": "Mauvais!2026",
                "nouveau_mot_de_passe": "Autre!MotDePasse2026",
                "confirmation": "Autre!MotDePasse2026",
            },
            format="json",
        )

        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("ancien_mot_de_passe", reponse.data)
        self.utilisateur.refresh_from_db()
        self.assertTrue(self.utilisateur.check_password("S3curise!2026"))

    def test_confirmation_differente(self):
        self.client.force_authenticate(self.utilisateur)

        reponse = self.client.post(
            self.url,
            {
                "ancien_mot_de_passe": "S3curise!2026",
                "nouveau_mot_de_passe": "Autre!MotDePasse2026",
                "confirmation": "PasLeMeme!2026",
            },
            format="json",
        )

        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("confirmation", reponse.data)

    def test_changement_reussi_et_tokens_revoques(self):
        self.client.force_authenticate(self.utilisateur)

        reponse = self.client.post(
            self.url,
            {
                "ancien_mot_de_passe": "S3curise!2026",
                "nouveau_mot_de_passe": "Autre!MotDePasse2026",
                "confirmation": "Autre!MotDePasse2026",
            },
            format="json",
        )

        self.assertEqual(reponse.status_code, status.HTTP_204_NO_CONTENT)
        self.utilisateur.refresh_from_db()
        self.assertTrue(self.utilisateur.check_password("Autre!MotDePasse2026"))
        self.assertFalse(self.utilisateur.check_password("S3curise!2026"))

    def test_changement_revoque_le_refresh_token_avant(self):
        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken.for_user(self.utilisateur)
        self.client.force_authenticate(self.utilisateur)
        self.client.post(
            self.url,
            {
                "ancien_mot_de_passe": "S3curise!2026",
                "nouveau_mot_de_passe": "Autre!MotDePasse2026",
                "confirmation": "Autre!MotDePasse2026",
            },
            format="json",
        )

        reponse = self.client.post(
            reverse("comptes:login-refresh"),
            {"refresh": str(refresh)},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_changement_trace_une_entree_audit(self):
        self.client.force_authenticate(self.utilisateur)

        self.client.post(
            self.url,
            {
                "ancien_mot_de_passe": "S3curise!2026",
                "nouveau_mot_de_passe": "Autre!MotDePasse2026",
                "confirmation": "Autre!MotDePasse2026",
            },
            format="json",
        )

        traces = JournalAudit.objects.filter(
            utilisateur=self.utilisateur,
            action=JournalAudit.Action.CHANGEMENT_MOT_DE_PASSE,
        )
        self.assertEqual(traces.count(), 1)