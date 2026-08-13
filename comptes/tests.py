"""Tests d'API pour l'app comptes (inscription, connexion, cloisonnement)."""

from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.core.cache import cache
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from comptes.models import ProfilAdminGeneral, Role, Utilisateur
from sgi.models import SGI


class InscriptionInvestisseurAPITests(APITestCase):
    """POST /api/comptes/register/"""

    url = reverse("comptes:register")

    def test_inscription_cree_un_investisseur_avec_profil(self):
        donnees = {
            "email": "inv@example.com",
            "prenom": "Awa",
            "nom": "Koné",
            "password": "S3curise!2026",
            "password_confirmation": "S3curise!2026",
        }
        reponse = self.client.post(self.url, donnees)

        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)
        utilisateur = Utilisateur.objects.get(email="inv@example.com")
        self.assertEqual(utilisateur.role.code, Role.Code.INVESTISSEUR)
        self.assertTrue(hasattr(utilisateur, "profil_investisseur"))
        self.assertTrue(utilisateur.check_password("S3curise!2026"))
        # Aucune donnée sensible dans la réponse.
        self.assertNotIn("password", reponse.data)
        self.assertNotIn("password_confirmation", reponse.data)

    def test_inscription_mots_de_passe_differents(self):
        donnees = {
            "email": "inv@example.com",
            "password": "S3curise!2026",
            "password_confirmation": "Autre!2026",
        }
        reponse = self.client.post(self.url, donnees)

        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password_confirmation", reponse.data)

    def test_inscription_email_deja_utilise(self):
        Utilisateur.objects.create_user("inv@example.com", "S3curise!2026")
        donnees = {
            "email": "INV@example.com",  # insensible à la casse
            "password": "S3curise!2026",
            "password_confirmation": "S3curise!2026",
        }
        reponse = self.client.post(self.url, donnees)

        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", reponse.data)


class ConnexionAPITests(APITestCase):
    """POST /api/comptes/login/"""

    url = reverse("comptes:login")

    def setUp(self):
        self.utilisateur = Utilisateur.objects.create_user(
            "inv@example.com", "S3curise!2026"
        )

    def test_connexion_renvoie_tokens_et_utilisateur(self):
        reponse = self.client.post(
            self.url, {"email": "inv@example.com", "password": "S3curise!2026"}
        )

        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.assertIn("access", reponse.data)
        self.assertIn("refresh", reponse.data)
        self.assertEqual(reponse.data["utilisateur"]["email"], "inv@example.com")

    def test_connexion_identifiants_invalides(self):
        reponse = self.client.post(
            self.url, {"email": "inv@example.com", "password": "mauvais"}
        )

        self.assertEqual(reponse.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_echec_de_connexion_journalise(self):
        from audit.models import JournalAudit
        self.client.post(self.url, {"email": "inconnu@example.com", "password": "x"})

        traces = JournalAudit.objects.filter(
            action=JournalAudit.Action.CONNEXION, entite_concernee="Connexion",
        )
        self.assertTrue(traces.exists())
        self.assertIn("inconnu@example.com", traces.first().apres["email"])


class VisibilitePourQuerySetTests(TestCase):
    """`Utilisateur.objects.visible_pour()` : cloisonnement multi-tenant.

    Testé au niveau du queryset (et non d'un endpoint de diagnostic) :
    c'est cette brique qui est réutilisée par toutes les vues métier.
    """

    @classmethod
    def setUpTestData(cls):
        cls.sgi_a = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        cls.sgi_b = SGI.objects.create(nom="SGI Beta", code_sgi="SGIB")
        cls.investisseur = Utilisateur.objects.create_user(
            "inv@example.com", "S3curise!2026"
        )
        cls.agent_a = Utilisateur.objects.create_user(
            "agent-a@example.com", "S3curise!2026",
            role=Role.Code.AGENT_SGI, sgi=cls.sgi_a,
        )
        cls.agent_b = Utilisateur.objects.create_user(
            "agent-b@example.com", "S3curise!2026",
            role=Role.Code.AGENT_SGI, sgi=cls.sgi_b,
        )
        cls.admin_general = Utilisateur.objects.create_user(
            "admin@example.com", "S3curise!2026", role=Role.Code.ADMIN_GENERAL,
        )

    def test_investisseur_ne_voit_que_lui_meme(self):
        emails = list(
            Utilisateur.objects.visible_pour(self.investisseur).values_list(
                "email", flat=True
            )
        )

        self.assertEqual(emails, ["inv@example.com"])

    def test_agent_ne_voit_que_sa_sgi(self):
        emails = set(
            Utilisateur.objects.visible_pour(self.agent_a).values_list(
                "email", flat=True
            )
        )

        self.assertEqual(emails, {"agent-a@example.com"})

    def test_admin_general_voit_tout(self):
        self.assertEqual(
            Utilisateur.objects.visible_pour(self.admin_general).count(),
            Utilisateur.objects.count(),
        )


class CreationProfilViaSauvegardeTests(TestCase):
    """Le Profil* doit être créé même sans passer par le manager.

    Régression : une création via l'admin Django appelle directement
    `Model.save()` (et non `create_user`). Le signal `post_save` doit
    garantir la création du profil dans ce cas aussi.
    """

    def test_profil_cree_lors_d_une_sauvegarde_directe(self):
        role = Role.objects.get(code=Role.Code.ADMIN_GENERAL)
        utilisateur = Utilisateur(email="admin-direct@example.com", role=role)
        utilisateur.set_password("S3curise!2026")
        # Sauvegarde directe, comme le ferait le formulaire de l'admin Django.
        utilisateur.save()

        self.assertTrue(hasattr(utilisateur, "profil_admin_general"))
        self.assertEqual(
            ProfilAdminGeneral.objects.filter(utilisateur=utilisateur).count(), 1
        )

    def test_resauvegarde_ne_duplique_pas_le_profil(self):
        utilisateur = Utilisateur.objects.create_user(
            "inv@example.com", "S3curise!2026"
        )
        utilisateur.prenom = "Awa"
        utilisateur.save()  # ne doit pas recréer/dupliquer le profil

        self.assertTrue(hasattr(utilisateur, "profil_investisseur"))


class ThrottlingConnexionAPITests(APITestCase):
    """Le endpoint de connexion doit être limité en débit (anti brute-force)."""

    url = reverse("comptes:login")

    def setUp(self):
        # Isole l'état du throttling (stocké en cache) des autres tests.
        cache.clear()
        Utilisateur.objects.create_user("inv@example.com", "S3curise!2026")

    def tearDown(self):
        cache.clear()

    def test_login_bloque_apres_depassement_du_quota(self):
        donnees = {"email": "inv@example.com", "password": "mauvais"}
        # Taux par défaut : 5/min. Les 5 premières requêtes passent (401),
        # la 6e doit être rejetée par le throttling (429).
        for _ in range(5):
            self.client.post(self.url, donnees)
        reponse = self.client.post(self.url, donnees)

        self.assertEqual(
            reponse.status_code, status.HTTP_429_TOO_MANY_REQUESTS
        )


class _FakeReponse:
    """Fake de requests.Response : raise_for_status() sans effet + .json()."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


GOOGLE_CONFIG = {
    "GOOGLE_OAUTH_CLIENT_ID": "fake-client-id.apps.googleusercontent.com",
    "GOOGLE_OAUTH_CLIENT_SECRET": "fake-client-secret",
    "GOOGLE_OAUTH_CALLBACK_URL": "http://testserver/api/comptes/oauth/google/callback/",
    "GOOGLE_OAUTH_FRONT_REDIRECT": "http://localhost:5173/oauth",
}

IDENTITE_GOOGLE = {
    "sub": "google-sub-123",
    "email": "google@example.com",
    "email_verified": True,
    "given_name": "Awa",
    "family_name": "Koné",
}


@override_settings(**GOOGLE_CONFIG)
class GoogleOAuthAPITests(APITestCase):
    """Flux « Se connecter avec Google » (Authorization Code Flow côté backend)."""

    url_login = reverse("comptes:google-login")
    url_callback = reverse("comptes:google-callback")

    def _initialiser_etat(self):
        """Simule l'étape 1 : /login/ redirige vers Google avec un state.

        Le vrai navigateur reçoit `state` dans l'URL de redirection vers
        Google, puis le renvoie au callback ; on fait pareil ici, en ne
        lisant PAS `client.session` (cette lecture écrase le cookie de
        session du client de test).
        """
        reponse = self.client.get(self.url_login)
        self.assertEqual(reponse.status_code, 302)
        return parse_qs(urlsplit(reponse["Location"]).query)["state"][0]

    def test_login_redirige_vers_google_avec_bons_parametres(self):
        reponse = self.client.get(self.url_login)

        self.assertEqual(reponse.status_code, 302)
        url = urlsplit(reponse["Location"])
        self.assertEqual(url.netloc, "accounts.google.com")
        parametres = parse_qs(url.query)
        self.assertEqual(parametres["response_type"], ["code"])
        self.assertEqual(parametres["scope"], ["openid email profile"])
        self.assertEqual(parametres["client_id"], [GOOGLE_CONFIG["GOOGLE_OAUTH_CLIENT_ID"]])

    def test_login_sans_configuration_renvoie_500(self):
        with override_settings(GOOGLE_OAUTH_CLIENT_ID=""):
            reponse = self.client.get(self.url_login)
            self.assertEqual(reponse.status_code, 500)

    @mock.patch("comptes.oauth.requests.get", return_value=_FakeReponse(IDENTITE_GOOGLE))
    @mock.patch(
        "comptes.oauth.requests.post",
        return_value=_FakeReponse({"access_token": "fake-access", "id_token": "x"}),
    )
    def test_callback_cree_le_compte_et_retourne_les_tokens(self, mock_post, mock_get):
        state = self._initialiser_etat()
        reponse = self.client.get(self.url_callback, {"code": "auth-code", "state": state})

        self.assertEqual(reponse.status_code, 302)
        self.assertIn("access=", reponse["Location"])
        self.assertIn("refresh=", reponse["Location"])
        self.assertNotIn("error=", reponse["Location"])

        utilisateur = Utilisateur.objects.get(email="google@example.com")
        self.assertEqual(utilisateur.role.code, Role.Code.INVESTISSEUR)
        self.assertEqual(utilisateur.profil_investisseur.google_id, "google-sub-123")
        # Un mot de passe aléatoire inutilisable, jamais null.
        self.assertTrue(utilisateur.password)

    @mock.patch("comptes.oauth.requests.get", return_value=_FakeReponse(IDENTITE_GOOGLE))
    @mock.patch(
        "comptes.oauth.requests.post",
        return_value=_FakeReponse({"access_token": "fake-access", "id_token": "x"}),
    )
    def test_callback_relie_un_compte_existant_sans_doublon(self, mock_post, mock_get):
        existant = Utilisateur.objects.create_user("google@example.com", "S3curise!2026")
        self.assertIsNone(existant.profil_investisseur.google_id)

        state = self._initialiser_etat()
        reponse = self.client.get(self.url_callback, {"code": "auth-code", "state": state})

        self.assertEqual(reponse.status_code, 302)
        self.assertNotIn("error=", reponse["Location"])
        self.assertEqual(Utilisateur.objects.count(), 1)
        existant.refresh_from_db()
        self.assertEqual(existant.profil_investisseur.google_id, "google-sub-123")

    def test_callback_refuse_un_state_invalide(self):
        # Pas d'étape 1 : aucun state en session, la requête est forgée.
        reponse = self.client.get(self.url_callback, {"code": "auth-code", "state": "bidon"})

        self.assertEqual(reponse.status_code, 302)
        self.assertIn("error=etat_invalide", reponse["Location"])
        self.assertEqual(Utilisateur.objects.count(), 0)

    @mock.patch("comptes.oauth.requests.get", return_value=_FakeReponse(IDENTITE_GOOGLE))
    @mock.patch(
        "comptes.oauth.requests.post",
        return_value=_FakeReponse({"access_token": "fake-access", "id_token": "x"}),
    )
    def test_callback_refuse_un_compte_lie_a_un_autre_google(self, mock_post, mock_get):
        existant = Utilisateur.objects.create_user("google@example.com", "S3curise!2026")
        existant.profil_investisseur.google_id = "autre-google-sub"
        existant.profil_investisseur.save(update_fields=["google_id"])

        state = self._initialiser_etat()
        reponse = self.client.get(self.url_callback, {"code": "auth-code", "state": state})

        self.assertEqual(reponse.status_code, 302)
        self.assertIn("error=compte_conflit", reponse["Location"])
        self.assertEqual(
            Utilisateur.objects.get(email="google@example.com").profil_investisseur.google_id,
            "autre-google-sub",
        )

    @mock.patch(
        "comptes.oauth.requests.get",
        return_value=_FakeReponse({**IDENTITE_GOOGLE, "email_verified": False}),
    )
    @mock.patch(
        "comptes.oauth.requests.post",
        return_value=_FakeReponse({"access_token": "fake-access", "id_token": "x"}),
    )
    def test_callback_refuse_un_email_non_verifie(self, mock_post, mock_get):
        """Un email Google non vérifié ne peut pas prendre le contrôle d'un compte."""
        Utilisateur.objects.create_user("google@example.com", "S3curise!2026")

        state = self._initialiser_etat()
        reponse = self.client.get(self.url_callback, {"code": "auth-code", "state": state})

        self.assertEqual(reponse.status_code, 302)
        self.assertIn("error=email_non_verifie", reponse["Location"])
        self.assertIsNone(
            Utilisateur.objects.get(email="google@example.com").profil_investisseur.google_id,
        )

    @mock.patch(
        "comptes.oauth.requests.get",
        return_value=_FakeReponse({**IDENTITE_GOOGLE, "email_verified": True}),
    )
    @mock.patch(
        "comptes.oauth.requests.post",
        return_value=_FakeReponse({"access_token": "fake-access", "id_token": "x"}),
    )
    def test_callback_refuse_le_relais_d_un_compte_privilegie(self, mock_post, mock_get):
        """Un compte AGENT_SGI ne peut jamais être connecté via Google."""
        role_agent = Role.objects.get(code=Role.Code.AGENT_SGI)
        sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        privilegie = Utilisateur.objects.create_user(
            "google@example.com", "S3curise!2026", sgi=sgi, role=role_agent,
        )

        state = self._initialiser_etat()
        reponse = self.client.get(self.url_callback, {"code": "auth-code", "state": state})

        self.assertEqual(reponse.status_code, 302)
        self.assertIn("error=compte_conflit", reponse["Location"])
        privilegie.refresh_from_db()
        self.assertEqual(privilegie.role.code, Role.Code.AGENT_SGI)


class AgentsAdminSGIAPITests(APITestCase):
    """UC18 : gestion des comptes Agent SGI par l'Admin SGI."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.autre_sgi = SGI.objects.create(nom="SGI Bêta", code_sgi="SGIB")
        role_admin = Role.objects.get(code=Role.Code.ADMIN_SGI)
        role_agent = Role.objects.get(code=Role.Code.AGENT_SGI)
        self.admin = Utilisateur.objects.create_user(
            "admin@example.com", "S3curise!2026", sgi=self.sgi, role=role_admin,
        )
        self.admin_autre = Utilisateur.objects.create_user(
            "adminb@example.com", "S3curise!2026", sgi=self.autre_sgi, role=role_admin,
        )
        self.agent_existant = Utilisateur.objects.create_user(
            "agent@example.com", "S3curise!2026", sgi=self.sgi, role=role_agent,
        )
        self.client.force_authenticate(self.admin)
        self.url = reverse("comptes:agents-list-create")
        self.url_detail = reverse("comptes:agents-detail", kwargs={"pk": self.agent_existant.pk})

    def test_creation_agent_avec_mot_de_passe_fourni(self):
        reponse = self.client.post(
            self.url,
            {
                "email": "nouveau.agent@example.com",
                "prenom": "Issa",
                "nom": "Diallo",
                "matricule": "MAT-001",
                "mot_de_passe": "Initial!2026",
            },
        )
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)
        agent = Utilisateur.objects.get(email="nouveau.agent@example.com")
        self.assertEqual(agent.role.code, Role.Code.AGENT_SGI)
        self.assertEqual(agent.sgi_id, self.sgi.pk)
        self.assertTrue(agent.check_password("Initial!2026"))
        self.assertEqual(agent.profil_agent_sgi.matricule, "MAT-001")

    def test_creation_agent_sans_mot_de_passe_genere_et_renvoie(self):
        reponse = self.client.post(self.url, {"email": "agent2@example.com"})
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)
        self.assertIn("mot_de_passe_initial", reponse.data)
        self.assertTrue(reponse.data["mot_de_passe_initial"])
        agent = Utilisateur.objects.get(email="agent2@example.com")
        self.assertTrue(agent.check_password(reponse.data["mot_de_passe_initial"]))
        # Jamais réaffiché en GET (une seule remise)
        reponse_get = self.client.get(self.url_detail.replace(str(self.agent_existant.pk), str(agent.pk)))
        self.assertNotIn("mot_de_passe_initial", reponse_get.data)

    def test_la_sgi_est_toujours_celle_de_l_admin(self):
        reponse = self.client.post(
            self.url, {"email": "agent3@example.com", "sgi": self.autre_sgi.pk},
        )
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)
        agent = Utilisateur.objects.get(email="agent3@example.com")
        self.assertEqual(agent.sgi_id, self.sgi.pk)

    def test_email_duplique_refuse(self):
        self.client.post(self.url, {"email": "agent@example.com"})
        self.assertEqual(
            Utilisateur.objects.filter(email="agent@example.com").count(), 1,
        )

    def test_liste_cloisonnee_a_la_sgi(self):
        reponse = self.client.get(self.url)
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        emails = [e["email"] for e in reponse.data["results"]]
        self.assertIn("agent@example.com", emails)
        self.assertNotIn("admin@example.com", emails)

    def test_bascule_inactive_sans_suppression(self):
        reponse = self.client.patch(self.url_detail, {"is_active": False})
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.agent_existant.refresh_from_db()
        self.assertFalse(self.agent_existant.is_active)
        self.assertTrue(Utilisateur.objects.filter(pk=self.agent_existant.pk).exists())

    def test_delete_toujours_refuse(self):
        reponse = self.client.delete(self.url_detail)
        self.assertEqual(reponse.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertTrue(Utilisateur.objects.filter(pk=self.agent_existant.pk).exists())

    def test_admin_d_une_autre_sgi_ne_voit_ni_ne_modifie_pas(self):
        self.client.force_authenticate(self.admin_autre)
        reponse_liste = self.client.get(self.url)
        self.assertEqual(reponse_liste.data["count"], 0)
        reponse_detail = self.client.patch(self.url_detail, {"is_active": False})
        self.assertEqual(reponse_detail.status_code, status.HTTP_404_NOT_FOUND)

    def test_roles_non_autorises(self):
        role_investisseur = Role.objects.get(code=Role.Code.INVESTISSEUR)
        investisseur = Utilisateur.objects.create_user(
            "inv@example.com", "S3curise!2026", role=role_investisseur,
        )
        self.client.force_authenticate(investisseur)
        self.assertEqual(
            self.client.post(self.url, {"email": "x@example.com"}).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.client.force_authenticate(self.agent_existant)
        self.assertEqual(
            self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN,
        )


class VerrouCoherenceRoleSGITests(TestCase):
    """Trigger PostgreSQL (migration 0006) : même règle que `clean()`.

    Le verrou s'applique au niveau base, indépendamment de l'API : une
    écriture directe en violation est refusée (IntegrityError).
    """

    def test_agent_sans_sgi_refuse(self):
        with self.assertRaises(DatabaseError):
            # Écriture ORM brute (sans full_clean) : seul le trigger
            # peut encore refuser cette insertion.
            role_agent = Role.objects.get(code=Role.Code.AGENT_SGI)
            Utilisateur.objects.create(
                email="agent-orphelin@example.com", role=role_agent, sgi=None,
            )

    def test_investisseur_avec_sgi_refuse(self):
        sgi = SGI.objects.create(nom="Alpha", code_sgi="SGIATRG")
        with self.assertRaises(DatabaseError):
            role_inv = Role.objects.get(code=Role.Code.INVESTISSEUR)
            Utilisateur.objects.create(
                email="inv-sgi@example.com", role=role_inv, sgi=sgi,
            )


class CreationAgentAtomiqueTests(APITestCase):
    """Un échec de création du profil ne laisse jamais de compte orphelin."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="Alpha", code_sgi="SGIATOM")
        role_admin = Role.objects.get(code=Role.Code.ADMIN_SGI)
        self.admin = Utilisateur.objects.create_user(
            "admin@example.com", "S3curise!2026", sgi=self.sgi, role=role_admin,
        )
        self.client.force_authenticate(self.admin)
        self.url = reverse("comptes:agents-list-create")

    def test_echec_profil_annule_la_creation_du_compte(self):
        self.client.raise_request_exception = False
        with mock.patch(
            "comptes.models.ProfilAgentSGI.save",
            side_effect=Exception("panne simulée"),
        ):
            reponse = self.client.post(self.url, {"email": "orphelin@example.com"})
        self.assertEqual(reponse.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertFalse(
            Utilisateur.objects.filter(email="orphelin@example.com").exists(),
        )
