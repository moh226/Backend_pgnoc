"""Étape 3D — Paramétrage KYC par l'Admin SGI (UC15)."""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from comptes.models import Role, Utilisateur
from dossiers.models import ChampKYC, Dossier, EtapeKYC, ValeurChamp
from sgi.models import SGI


def _creer_sgi(nom, code):
    return SGI.objects.create(nom=nom, code_sgi=code)


def _admin(email, sgi):
    role_admin = Role.objects.filter(code="ADMIN_SGI").first()
    return Utilisateur.objects.create_user(
        email, "S3curise!2026", sgi=sgi, role=role_admin,
    )


def _investisseur(email):
    return Utilisateur.objects.create_user(email, "S3curise!2026")


class ParametrageEtapeKYCTests(APITestCase):
    """CRUD des étapes : cloisonnement SGI, unicité d'ordre, 409 protégés."""

    def setUp(self):
        self.sgi_a = _creer_sgi("SGI Alpha", "SGIA")
        self.sgi_b = _creer_sgi("SGI Bêta", "SGIB")
        self.admin_a = _admin("admin.a@example.com", self.sgi_a)
        self.admin_b = _admin("admin.b@example.com", self.sgi_b)
        self.etape_b = EtapeKYC.objects.create(sgi=self.sgi_b, nom="Étrangère", ordre=1)
        self.client.force_authenticate(self.admin_a)

    def test_creation_etape_claque_sur_ma_sgi(self):
        reponse = self.client.post(
            reverse("dossiers:admin-etapes-kyc"),
            {"nom": "Identité", "ordre": 1},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)
        etape = EtapeKYC.objects.get(pk=reponse.data["id"])
        self.assertEqual(etape.sgi_id, self.sgi_a.pk)
        self.assertTrue(etape.actif)

    def test_la_sgi_est_ignoree_si_envoyee_par_le_client(self):
        reponse = self.client.post(
            reverse("dossiers:admin-etapes-kyc"),
            {"nom": "Identité", "ordre": 1, "sgi": str(self.sgi_b.pk)},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)
        self.assertEqual(EtapeKYC.objects.latest("date_creation").sgi_id, self.sgi_a.pk)

    def test_liste_cloisonnee_aux_etapes_de_ma_sgi(self):
        EtapeKYC.objects.create(sgi=self.sgi_a, nom="Identité", ordre=1)
        reponse = self.client.get(reverse("dossiers:admin-etapes-kyc"))
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        etapes = {e["nom"] for e in reponse.data["results"]}
        self.assertEqual(etapes, {"Identité"})

    def test_admin_b_ne_voit_pas_les_etapes_de_a(self):
        EtapeKYC.objects.create(sgi=self.sgi_a, nom="Identité", ordre=1)
        self.client.force_authenticate(self.admin_b)
        reponse = self.client.get(reverse("dossiers:admin-etapes-kyc"))
        self.assertEqual(reponse.data["count"], 1)
        self.assertEqual(reponse.data["results"][0]["nom"], "Étrangère")

    def test_patch_et_delete_sur_etape_etrangere_renvoient_404(self):
        patch = self.client.patch(
            reverse("dossiers:admin-etape-kyc", kwargs={"pk": self.etape_b.pk}),
            {"nom": "Piraté"},
            format="json",
        )
        suppression = self.client.delete(
            reverse("dossiers:admin-etape-kyc", kwargs={"pk": self.etape_b.pk}),
        )
        self.assertEqual(patch.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(suppression.status_code, status.HTTP_404_NOT_FOUND)

    def test_ordre_duplique_refuse(self):
        EtapeKYC.objects.create(sgi=self.sgi_a, nom="Identité", ordre=1)
        reponse = self.client.post(
            reverse("dossiers:admin-etapes-kyc"),
            {"nom": "Documents", "ordre": 1},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_suppression_etape_avec_valeurs_refusee_409(self):
        etape = EtapeKYC.objects.create(sgi=self.sgi_a, nom="Identité", ordre=1)
        champ = ChampKYC.objects.create(
            etape=etape, code="nom", nom="Nom", type=ChampKYC.TypeChamp.TEXTE_COURT,
        )
        dossier = Dossier.objects.create(
            utilisateur=_investisseur("inv@example.com"), sgi=self.sgi_a,
        )
        ValeurChamp.objects.create(dossier=dossier, champ=champ, valeur="Awa")
        reponse = self.client.delete(
            reverse("dossiers:admin-etape-kyc", kwargs={"pk": etape.pk}),
        )
        self.assertEqual(reponse.status_code, status.HTTP_409_CONFLICT)

    def test_suppression_etape_vide_ok(self):
        reponse = self.client.delete(
            reverse("dossiers:admin-etape-kyc", kwargs={"pk": self.etape_b.pk}),
        )
        self.assertEqual(reponse.status_code, status.HTTP_404_NOT_FOUND)
        etape = EtapeKYC.objects.create(sgi=self.sgi_a, nom="Identité", ordre=1)
        reponse = self.client.delete(
            reverse("dossiers:admin-etape-kyc", kwargs={"pk": etape.pk}),
        )
        self.assertEqual(reponse.status_code, status.HTTP_204_NO_CONTENT)

    def test_desactivation_retire_l_etape_du_parcours_investisseur(self):
        etape = EtapeKYC.objects.create(sgi=self.sgi_a, nom="Identité", ordre=1)
        reponse = self.client.patch(
            reverse("dossiers:admin-etape-kyc", kwargs={"pk": etape.pk}),
            {"actif": False},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.client.force_authenticate(_investisseur("inv@example.com"))
        reponse = self.client.get(
            reverse("dossiers:etapes-kyc"), {"sgi": str(self.sgi_a.pk)},
        )
        self.assertEqual(reponse.data["count"], 0)

    def test_seul_un_admin_sgi_peut_parametrer(self):
        self.client.force_authenticate(_investisseur("inv@example.com"))
        reponse = self.client.get(reverse("dossiers:admin-etapes-kyc"))
        self.assertEqual(reponse.status_code, status.HTTP_403_FORBIDDEN)


class ParametrageChampKYCTests(APITestCase):
    """CRUD des champs : cloisonnement, conditionnalité, 409 garde-fous."""

    def setUp(self):
        self.sgi_a = _creer_sgi("SGI Alpha", "SGIA")
        self.sgi_b = _creer_sgi("SGI Bêta", "SGIB")
        self.admin_a = _admin("admin.a@example.com", self.sgi_a)
        self.etape_a = EtapeKYC.objects.create(sgi=self.sgi_a, nom="Identité", ordre=1)
        self.etape_b = EtapeKYC.objects.create(sgi=self.sgi_b, nom="Identité", ordre=1)
        self.url_liste = reverse("dossiers:admin-champs-kyc")
        self.client.force_authenticate(self.admin_a)

    def test_creation_champ_sur_ma_sgi(self):
        reponse = self.client.post(
            self.url_liste,
            {"etape": str(self.etape_a.pk), "code": "nom", "nom": "Nom",
             "type": ChampKYC.TypeChamp.TEXTE_COURT},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)
        self.assertEqual(reponse.data["etape"], self.etape_a.pk)

    def test_creation_champ_sur_etape_etrangere_refusee(self):
        reponse = self.client.post(
            self.url_liste,
            {"etape": str(self.etape_b.pk), "code": "nom", "nom": "Nom",
             "type": ChampKYC.TypeChamp.TEXTE_COURT},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_champ_conditionnel_oblige_a_un_declencheur(self):
        pere = ChampKYC.objects.create(
            etape=self.etape_a, code="type", nom="Type",
            type=ChampKYC.TypeChamp.CHOIX_UNIQUE,
            options_choix=["Morale", "Physique"],
        )
        reponse = self.client.post(
            self.url_liste,
            {"etape": str(self.etape_a.pk), "code": "rccm", "nom": "RCCM",
             "type": ChampKYC.TypeChamp.TEXTE_COURT,
             "champ_parent": str(pere.pk)},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)
        reponse = self.client.post(
            self.url_liste,
            {"etape": str(self.etape_a.pk), "code": "rccm", "nom": "RCCM",
             "type": ChampKYC.TypeChamp.TEXTE_COURT,
             "champ_parent": str(pere.pk), "valeur_declencheur": "Morale"},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)

    def test_champ_parent_d_une_autre_etape_refuse(self):
        pere_autre = ChampKYC.objects.create(
            etape=self.etape_b, code="type", nom="Type",
            type=ChampKYC.TypeChamp.CHOIX_UNIQUE,
            options_choix=["Morale", "Physique"],
        )
        reponse = self.client.post(
            self.url_liste,
            {"etape": str(self.etape_a.pk), "code": "rccm", "nom": "RCCM",
             "type": ChampKYC.TypeChamp.TEXTE_COURT,
             "champ_parent": str(pere_autre.pk), "valeur_declencheur": "Morale"},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_modification_champ(self):
        champ = ChampKYC.objects.create(
            etape=self.etape_a, code="nom", nom="Nom",
            type=ChampKYC.TypeChamp.TEXTE_COURT,
        )
        reponse = self.client.patch(
            reverse("dossiers:admin-champ-kyc", kwargs={"pk": champ.pk}),
            {"obligatoire": True, "actif": False},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        champ.refresh_from_db()
        self.assertTrue(champ.obligatoire)
        self.assertFalse(champ.actif)

    def test_champ_etranger_404(self):
        champ_etranger = ChampKYC.objects.create(
            etape=self.etape_b, code="nom", nom="Nom",
            type=ChampKYC.TypeChamp.TEXTE_COURT,
        )
        reponse = self.client.patch(
            reverse("dossiers:admin-champ-kyc", kwargs={"pk": champ_etranger.pk}),
            {"nom": "Piraté"},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_404_NOT_FOUND)

    def test_champ_parent_nul_accepte(self):
        reponse = self.client.post(
            self.url_liste,
            {"etape": str(self.etape_a.pk), "code": "nom", "nom": "Nom",
             "type": ChampKYC.TypeChamp.TEXTE_COURT,
             "champ_parent": None, "valeur_declencheur": None},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(reponse.data["champ_parent"])

    def test_filtre_par_etape(self):
        for code in ("nom", "prenom", "telephone"):
            ChampKYC.objects.create(
                etape=self.etape_a, code=code, nom=code,
                type=ChampKYC.TypeChamp.TEXTE_COURT,
            )
        reponse = self.client.get(self.url_liste, {"etape": str(self.etape_a.pk)})
        self.assertEqual(reponse.data["count"], 3)
        self.assertEqual(reponse.data["count"], 3)

    def test_suppression_champ_avec_valeurs_refusee_409(self):
        champ = ChampKYC.objects.create(
            etape=self.etape_a, code="nom", nom="Nom",
            type=ChampKYC.TypeChamp.TEXTE_COURT,
        )
        dossier = Dossier.objects.create(
            utilisateur=_investisseur("inv@example.com"), sgi=self.sgi_a,
        )
        ValeurChamp.objects.create(dossier=dossier, champ=champ, valeur="Awa")
        reponse = self.client.delete(
            reverse("dossiers:admin-champ-kyc", kwargs={"pk": champ.pk}),
        )
        self.assertEqual(reponse.status_code, status.HTTP_409_CONFLICT)

    def test_suppression_champ_parent_refusee_409(self):
        pere = ChampKYC.objects.create(
            etape=self.etape_a, code="type", nom="Type",
            type=ChampKYC.TypeChamp.CHOIX_UNIQUE,
            options_choix=["Morale", "Physique"],
        )
        ChampKYC.objects.create(
            etape=self.etape_a, code="rccm", nom="RCCM",
            type=ChampKYC.TypeChamp.TEXTE_COURT,
            champ_parent=pere, valeur_declencheur="Morale",
        )
        reponse = self.client.delete(
            reverse("dossiers:admin-champ-kyc", kwargs={"pk": pere.pk}),
        )
        self.assertEqual(reponse.status_code, status.HTTP_409_CONFLICT)

    def test_suppression_champ_sans_valeurs_ok(self):
        champ = ChampKYC.objects.create(
            etape=self.etape_a, code="nom", nom="Nom",
            type=ChampKYC.TypeChamp.TEXTE_COURT,
        )
        reponse = self.client.delete(
            reverse("dossiers:admin-champ-kyc", kwargs={"pk": champ.pk}),
        )
        self.assertEqual(reponse.status_code, status.HTTP_204_NO_CONTENT)