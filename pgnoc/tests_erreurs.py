"""Expérience utilisateur des erreurs API — enveloppe unifiée {code, message}.

Objectif : garantir que TOUTE erreur, quel que soit son chemin (validation
DRF, 401/403/404, conflit métier, throttle…), sort dans le même format
avec un message français actionnable — le frontend affiche `message`
tel quel et branche sa logique sur `code`.
"""

import uuid

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from comptes.models import Role, Utilisateur
from dossiers.models import ChampKYC, Dossier, EtapeKYC, ValeurChamp
from dossiers.tests import _signer_dossier
from dossiers.workflow import transiter
from sgi.models import SGI


class _Parcours:
    """Fixtures partagées : une SGI, un parcours KYC, un dossier."""

    def setUp(self):
        super().setUp()
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.investisseur = Utilisateur.objects.create_user(
            "inv@example.com", "S3curise!2026"
        )
        self.etape = EtapeKYC.objects.create(
            sgi=self.sgi, nom="Identité", ordre=1
        )
        self.champ_type = ChampKYC.objects.create(
            etape=self.etape, code="type_personne", nom="Type de personne",
            type=ChampKYC.TypeChamp.CHOIX_UNIQUE,
            options_choix=["Morale", "Physique"],
        )
        self.champ_nom = ChampKYC.objects.create(
            etape=self.etape, code="nom", nom="Nom",
            type=ChampKYC.TypeChamp.TEXTE_COURT,
        )
        self.champ_cnib = ChampKYC.objects.create(
            etape=self.etape, code="cnib", nom="Copie CNIB",
            type=ChampKYC.TypeChamp.FICHIER, formats_acceptes="pdf",
        )
        self.dossier = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi,
        )

    def _remplir_tout(self):
        ValeurChamp.objects.create(
            dossier=self.dossier, champ=self.champ_type, valeur="Physique"
        )
        ValeurChamp.objects.create(
            dossier=self.dossier, champ=self.champ_nom, valeur="Awa Koné"
        )
        ValeurChamp.objects.create(
            dossier=self.dossier, champ=self.champ_cnib,
            fichier="dossiers/x/cnib.pdf",
        )


class EnveloppeUnifieeTests(_Parcours, APITestCase):
    """Erreurs « standard » : même format, message français, code stable."""

    def test_non_authentifie_message_francais(self):
        reponse = self.client.get(
            reverse("dossiers:etapes-kyc"), {"sgi": str(self.sgi.pk)}
        )
        self.assertEqual(reponse.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(reponse.data["code"], "NON_AUTHENTIFIE")
        self.assertIn("connecté", reponse.data["message"])
        self.assertEqual(reponse.data["detail"], reponse.data["message"])

    def test_introuvable_message_francais(self):
        self.client.force_authenticate(self.investisseur)
        reponse = self.client.get(
            reverse("dossiers:dossier-detail", kwargs={"pk": uuid.uuid4()})
        )
        self.assertEqual(reponse.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(reponse.data["code"], "INTROUVABLE")
        self.assertEqual(reponse.data["message"], "Ressource introuvable.")

    def test_methode_non_autorisee_message_francais(self):
        self.client.force_authenticate(self.investisseur)
        reponse = self.client.put(
            reverse("dossiers:dossier-detail", kwargs={"pk": self.dossier.pk})
        )
        self.assertEqual(reponse.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(reponse.data["code"], "METHODE_NON_AUTORISEE")
        # Message français, sans verbe anglais (« not allowed »).
        self.assertIn("autoris", reponse.data["message"].lower())
        self.assertNotIn("not allowed", reponse.data["message"].lower())

    def test_page_inexistante_message_francais(self):
        self.client.force_authenticate(self.investisseur)
        reponse = self.client.get(
            reverse("dossiers:dossier-list-create"), {"page": "999"}
        )
        self.assertEqual(reponse.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(reponse.data["code"], "INTROUVABLE")
        self.assertIn("page", reponse.data["message"].lower())

    def test_validation_drf_normalisee_avec_champs(self):
        """Une ValidationError DRF sort en {code, message, champs}, plus
        jamais en dict brut `{champ: [...]}` ni en liste nue."""
        self.client.force_authenticate(self.investisseur)
        reponse = self.client.post(
            reverse("dossiers:dossier-valeurs",
                    kwargs={"dossier_pk": self.dossier.pk}),
            {"valeur": "sans champ"},
        )
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(reponse.data["code"], "VALIDATION_INVALIDE")
        self.assertIn("champ", reponse.data["champs"])


class SoumissionIncompleteTests(_Parcours, APITestCase):
    """Le point UX majeur : l'erreur de soumission GUIDE l'investisseur."""

    def test_soumission_liste_les_champs_manquants(self):
        ValeurChamp.objects.create(
            dossier=self.dossier, champ=self.champ_type, valeur="Physique"
        )
        ValeurChamp.objects.create(
            dossier=self.dossier, champ=self.champ_nom, valeur="Awa Koné"
        )
        # Copie CNIB volontairement absente.

        self.client.force_authenticate(self.investisseur)
        reponse = self.client.post(
            reverse("dossiers:dossier-soumettre",
                    kwargs={"dossier_pk": self.dossier.pk})
        )

        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(reponse.data["code"], "DOSSIER_INCOMPLET")
        # Le message nomme le champ fautif...
        self.assertIn("Copie CNIB", reponse.data["message"])
        # ...et le contexte structuré permet le parcours guidé (étape +
        # champ), exactement ce que le bouton « Continuer » du frontend
        # consomme pour amener l'utilisateur au champ à remplir.
        manquants = reponse.data["champs_manquants"]
        self.assertEqual(len(manquants), 1)
        self.assertEqual(manquants[0]["champ"], "Copie CNIB")
        self.assertEqual(manquants[0]["etape"], "Identité")
        self.assertEqual(manquants[0]["code"], "cnib")

    def test_workflow_liste_aussi_les_champs_manquants(self):
        """Filet de sécurité : même message riche côté machine à états
        (chemin utilisé par une éventuelle double soumission concurrente)."""
        from dossiers.workflow import _verifier_avant_soumission
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError) as contexte:
            _verifier_avant_soumission(self.dossier)
        self.assertIn("Copie CNIB", str(contexte.exception))


class ChampVerrouilleTests(_Parcours, APITestCase):
    """UC12 : l'erreur de champ verrouillé nomme le champ ET liste les
    corrections attendues (celles commentées par l'agent)."""

    def setUp(self):
        super().setUp()
        role_agent = Role.objects.filter(code="AGENT_SGI").first()
        self.agent = Utilisateur.objects.create_user(
            "agent@example.com", "S3curise!2026",
            sgi=self.sgi, role=role_agent,
        )
        self._remplir_tout()
        _signer_dossier(self.dossier)
        transiter(self.dossier, Dossier.Statut.SOUMIS)
        transiter(self.dossier, Dossier.Statut.EN_INSTRUCTION, agent=self.agent)
        # L'agent demande une correction sur « Nom » uniquement.
        valeur_nom = ValeurChamp.objects.get(
            dossier=self.dossier, champ=self.champ_nom
        )
        valeur_nom.commentaire_agent = "Nom de famille illisible sur la CNIB."
        valeur_nom.save(update_fields=["commentaire_agent"])
        transiter(
            self.dossier, Dossier.Statut.REJETE,
            agent=self.agent, motif_rejet="Documents à corriger",
            utilisateur=self.agent,
        )

    def test_champ_conforme_est_verrouille_avec_contexte(self):
        self.client.force_authenticate(self.investisseur)
        reponse = self.client.post(
            reverse("dossiers:dossier-valeurs",
                    kwargs={"dossier_pk": self.dossier.pk}),
            {"champ": self.champ_type.pk, "valeur": "Morale"},
        )

        self.assertEqual(reponse.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(reponse.data["code"], "CHAMP_VERROUILLE")
        # Le champ fautif est nommé dans le message ET en contexte.
        self.assertIn("Type de personne", reponse.data["message"])
        self.assertEqual(reponse.data["champ"], "Type de personne")
        # La liste des corrections attendues embarque le commentaire de
        # l'agent : l'investisseur sait QUOI corriger et POURQUOI.
        corrections = reponse.data["champs_a_corriger"]
        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections[0]["champ"], "Nom")
        self.assertEqual(
            corrections[0]["commentaire_agent"],
            "Nom de famille illisible sur la CNIB.",
        )

    def test_champ_signale_reste_modifiable(self):
        self.client.force_authenticate(self.investisseur)
        reponse = self.client.post(
            reverse("dossiers:dossier-valeurs",
                    kwargs={"dossier_pk": self.dossier.pk}),
            {"champ": self.champ_nom.pk, "valeur": "Awa KONE"},
        )
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)


class StatutIncompatibleTests(_Parcours, APITestCase):
    """Les conflits de statut nomment le statut ACTUEL en clair."""

    def test_soumission_d_un_dossier_valide(self):
        self._remplir_tout()
        _signer_dossier(self.dossier)
        role_agent = Role.objects.filter(code="AGENT_SGI").first()
        agent = Utilisateur.objects.create_user(
            "agent@example.com", "S3curise!2026",
            sgi=self.sgi, role=role_agent,
        )
        transiter(self.dossier, Dossier.Statut.SOUMIS)
        transiter(self.dossier, Dossier.Statut.EN_INSTRUCTION, agent=agent)
        transiter(self.dossier, Dossier.Statut.VALIDE, agent=agent)

        self.client.force_authenticate(self.investisseur)
        reponse = self.client.post(
            reverse("dossiers:dossier-soumettre",
                    kwargs={"dossier_pk": self.dossier.pk})
        )
        self.assertEqual(reponse.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(reponse.data["code"], "DOSSIER_NON_SOUMETTABLE")
        # Le statut humain (« Validé »), pas le code technique.
        self.assertIn("Validé", reponse.data["message"])
        self.assertEqual(reponse.data["statut_actuel"], Dossier.Statut.VALIDE)
