"""Tests de la page d'accueil : public (vitrine) et édition Admin Général.

GET  /api/accueil/                      → blocs actifs + publiés
GET  /api/admin-general/accueil/        → tous les blocs (édition)
PATCH /api/admin-general/accueil/<type> → édition d'un bloc
POST /api/admin-general/accueil/ordre/  → ordre, activation, publication
"""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accueil.models import BlocAccueil
from audit.models import JournalAudit
from comptes.models import Role, Utilisateur


class AccueilPublicAPITests(APITestCase):
    """GET /api/accueil/"""

    url = reverse("accueil:public")

    def setUp(self):
        self.bloc = BlocAccueil.objects.get(type=BlocAccueil.TypeBloc.HERO)
        self.bloc.actif = True
        self.bloc.date_publication = __import__("django").utils.timezone.now()
        self.bloc.save(update_fields=["actif", "date_publication"])

    def test_acces_public_sans_authentification(self):
        reponse = self.client.get(self.url)
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)

    def test_ne_renvoie_que_les_blocs_actifs_et_publies(self):
        brouillon = BlocAccueil.objects.get(type=BlocAccueil.TypeBloc.FAQ)
        brouillon.date_publication = None
        brouillon.save(update_fields=["date_publication"])
        inactif = BlocAccueil.objects.get(type=BlocAccueil.TypeBloc.ETAPES)
        inactif.actif = False
        inactif.date_publication = __import__("django").utils.timezone.now()
        inactif.save(update_fields=["actif", "date_publication"])

        reponse = self.client.get(self.url)

        types = {bloc["type"] for bloc in reponse.data}
        self.assertIn(BlocAccueil.TypeBloc.HERO, types)
        self.assertNotIn(BlocAccueil.TypeBloc.FAQ, types)
        self.assertNotIn(BlocAccueil.TypeBloc.ETAPES, types)

    def test_blocs_ordonnes(self):
        autres = BlocAccueil.objects.exclude(pk=self.bloc.pk)
        for i, bloc in enumerate(autres):
            bloc.actif = True
            bloc.date_publication = __import__("django").utils.timezone.now()
            bloc.ordre = i + 1
            bloc.save(update_fields=["actif", "date_publication", "ordre"])

        reponse = self.client.get(self.url)

        ordres = [bloc["type"] for bloc in reponse.data]
        self.assertEqual(
            ordres,
            sorted(ordres, key=lambda t: BlocAccueil.objects.get(type=t).ordre),
        )

    def test_renvoie_contenu_et_calls_to_action(self):
        reponse = self.client.get(self.url)

        hero = next(bloc for bloc in reponse.data if bloc["type"] == "HERO")
        self.assertEqual(hero["contenu"]["cta_principal"], "S'inscrire")
        self.assertEqual(hero["contenu"]["lien_principal"], "/inscription")


class AccueilAdminAPITests(APITestCase):
    """Édition par l'Admin Général uniquement."""

    def setUp(self):
        self.admin_general = Utilisateur.objects.create_user(
            "admin@example.com", "S3curise!2026",
            role=Role.Code.ADMIN_GENERAL,
        )
        self.investisseur = Utilisateur.objects.create_user(
            "inv@example.com", "S3curise!2026",
        )
        self.bloc = BlocAccueil.objects.get(type=BlocAccueil.TypeBloc.HERO)

    def test_liste_reservee_admin_general(self):
        self.client.force_authenticate(self.investisseur)
        reponse = self.client.get(reverse("administration:accueil-list"))
        self.assertEqual(reponse.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.admin_general)
        reponse = self.client.get(reverse("administration:accueil-list"))
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.assertEqual(len(reponse.data), 8)

    def test_patch_bloc_met_a_jour_contenu_et_audit(self):
        self.client.force_authenticate(self.admin_general)

        reponse = self.client.patch(
            reverse("administration:accueil-detail", args=["HERO"]),
            {"titre": "Nouveau titre", "contenu": {"cta_principal": "Commencer"}},
            format="json",
        )

        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.bloc.refresh_from_db()
        self.assertEqual(self.bloc.titre, "Nouveau titre")
        self.assertEqual(self.bloc.contenu["cta_principal"], "Commencer")
        traces = JournalAudit.objects.filter(
            action=JournalAudit.Action.MODIFICATION_ACCUEIL,
        )
        self.assertEqual(traces.count(), 1)
        self.assertEqual(traces.first().apres["titre"], "Nouveau titre")

    def test_patch_refuse_contenu_mal_structure(self):
        self.client.force_authenticate(self.admin_general)

        reponse = self.client.patch(
            reverse("administration:accueil-detail", args=["ETAPES"]),
            {"contenu": {"etapes": "pas une liste"}},
            format="json",
        )

        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_type_inconnu_404(self):
        self.client.force_authenticate(self.admin_general)

        reponse = self.client.patch(
            reverse("administration:accueil-detail", args=["INEXISTANT"]),
            {"titre": "X"},
            format="json",
        )

        self.assertEqual(reponse.status_code, status.HTTP_404_NOT_FOUND)

    def test_ordonnancement_activation_et_publication(self):
        self.client.force_authenticate(self.admin_general)

        reponse = self.client.post(
            reverse("administration:accueil-ordre"),
            {
                "blocs": [
                    {"type": "FAQ", "actif": False, "ordre": 0},
                    {"type": "HERO", "actif": True, "ordre": 1},
                ],
                "publier": True,
            },
            format="json",
        )

        self.assertEqual(reponse.status_code, status.HTTP_204_NO_CONTENT)
        self.bloc.refresh_from_db()
        faq = BlocAccueil.objects.get(type=BlocAccueil.TypeBloc.FAQ)
        self.assertEqual(self.bloc.ordre, 1)
        self.assertIsNotNone(self.bloc.date_publication)
        self.assertFalse(faq.actif)
        self.assertIsNone(faq.date_publication)

    def test_ordre_refuse_type_inconnu(self):
        self.client.force_authenticate(self.admin_general)

        reponse = self.client.post(
            reverse("administration:accueil-ordre"),
            {"blocs": [{"type": "GHOST", "actif": True, "ordre": 0}]},
            format="json",
        )

        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_publication_puis_vue_publique(self):
        self.client.force_authenticate(self.admin_general)
        self.client.post(
            reverse("administration:accueil-ordre"),
            {"publier": True},
            format="json",
        )

        reponse = self.client.get(reverse("accueil:public"))
        self.assertEqual(len(reponse.data), 8)