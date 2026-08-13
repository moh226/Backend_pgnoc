"""Tests du domaine Dossiers — Étape 1A : calcul de la progression du dossier.

Vérifie `progression_pct` :
  - seul les champs obligatoires actifs comptent ;
  - un champ conditionnel n'est requis que si son parent déclencheur est
    positionné dans le dossier ;
  - le pourcentage est recalculé automatiquement à chaque saisie (API).
"""

import copy
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from comptes.models import Role, Utilisateur
from dossiers.models import ChampKYC, Dossier, EtapeKYC, ValeurChamp
from dossiers.services import calculer_progression_pct, generer_code_otp, poser_signature_otp
from dossiers.workflow import TRANSITIONS, transiter
from sgi.models import SGI


def _configurer_parcours(sgi):
    """Étape active avec : P (CHOIX_UNIQUE), A, B (conditionnel sur P), F (FICHIER)."""
    etape = EtapeKYC.objects.create(sgi=sgi, nom="Identité", ordre=1)
    p = ChampKYC.objects.create(
        etape=etape, code="type_personne", nom="Type de personne",
        type=ChampKYC.TypeChamp.CHOIX_UNIQUE,
        options_choix=["Morale", "Physique"],
    )
    a = ChampKYC.objects.create(
        etape=etape, code="nom", nom="Nom", type=ChampKYC.TypeChamp.TEXTE_COURT,
    )
    b = ChampKYC.objects.create(
        etape=etape, code="rccm", nom="Numéro RCCM",
        type=ChampKYC.TypeChamp.TEXTE_COURT,
        champ_parent=p, valeur_declencheur="Morale",
    )
    f = ChampKYC.objects.create(
        etape=etape, code="cnib", nom="Copie CNIB",
        type=ChampKYC.TypeChamp.FICHIER, formats_acceptes="pdf",
    )
    return {"p": p, "a": a, "b": b, "f": f, "etape": etape}


class ProgressionServiceTests(APITestCase):
    """Test unitaire du service `calculer_progression_pct`."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.investisseur = Utilisateur.objects.create_user("inv@example.com", "S3curise!2026")
        self.champs = _configurer_parcours(self.sgi)
        self.dossier = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi,
        )

    def _remplir(self, champ, valeur=None, fichier=""):
        return ValeurChamp.objects.create(
            dossier=self.dossier, champ=champ,
            valeur=valeur or "", fichier=fichier,
        )

    def test_dossier_vide_vaut_0(self):
        self.assertEqual(calculer_progression_pct(self.dossier), 0)

    def test_champ_conditionnel_ignore_tant_que_parent_vide(self):
        self._remplir(self.champs["a"], "Awa")
        # Requis : P, A, F (B ignoré, P vide) → 1/3
        self.assertEqual(calculer_progression_pct(self.dossier), 33)

    def test_champ_conditionnel_devient_requis_si_parent_declenche(self):
        self._remplir(self.champs["a"], "Awa")
        self._remplir(self.champs["p"], "Morale")
        # Requis : P, A, B, F → 2/4
        self.assertEqual(calculer_progression_pct(self.dossier), 50)
        self._remplir(self.champs["b"], "RCCM-123")
        self.assertEqual(calculer_progression_pct(self.dossier), 75)

    def test_champ_conditionnel_redevient_ignore_si_parent_change(self):
        self._remplir(self.champs["a"], "Awa")
        self._remplir(self.champs["p"], "Physique")
        # B non requis (déclencheur = 'Morale') : requiert P, A, F → 2/3
        self.assertEqual(calculer_progression_pct(self.dossier), 67)

    def test_parcours_entierement_rempli_vaut_100(self):
        self._remplir(self.champs["p"], "Morale")
        self._remplir(self.champs["a"], "Awa")
        self._remplir(self.champs["b"], "RCCM-123")
        self._remplir(self.champs["f"], fichier="dossiers/x/y.pdf")
        self.assertEqual(calculer_progression_pct(self.dossier), 100)


class ProgressionRecalculAPITests(APITestCase):
    """L'API recalcule la progression après chaque saisie de valeur."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.investisseur = Utilisateur.objects.create_user("inv@example.com", "S3curise!2026")
        self.champs = _configurer_parcours(self.sgi)
        self.dossier = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi,
        )
        self.client.force_authenticate(self.investisseur)
        self.url = reverse(
            "dossiers:dossier-valeurs",
            kwargs={"dossier_pk": self.dossier.pk},
        )

    def test_creation_dossier_part_a_zero(self):
        self.assertEqual(
            Dossier.objects.get(pk=self.dossier.pk).progression_pct, 0
        )

    def test_post_valeur_recalcule_la_progression(self):
        reponse = self.client.post(
            self.url,
            {"champ": self.champs["a"].pk, "valeur": "Awa"},
        )
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED)

        dossier = Dossier.objects.get(pk=self.dossier.pk)
        self.assertEqual(dossier.progression_pct, 33)

    def test_upsert_recalcule_aussi(self):
        self.client.post(self.url, {"champ": self.champs["a"].pk, "valeur": "Awa"})
        reponse = self.client.post(
            self.url,
            {"champ": self.champs["a"].pk, "valeur": "Awa Koné"},
        )
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        # La valeur est écrasée, pas dupliquée : progression inchangée.
        self.assertEqual(ValeurChamp.objects.filter(dossier=self.dossier).count(), 1)
        self.assertEqual(
            Dossier.objects.get(pk=self.dossier.pk).progression_pct, 33
        )


class SoumissionAPITests(APITestCase):
    """1C : UC10 — endpoint de soumission d'un dossier."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.investisseur = Utilisateur.objects.create_user("inv@example.com", "S3curise!2026")
        self.autre = Utilisateur.objects.create_user("autre@example.com", "S3curise!2026")
        self.champs = _configurer_parcours(self.sgi)
        self.dossier = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi,
        )
        self.url = reverse(
            "dossiers:dossier-soumettre",
            kwargs={"dossier_pk": self.dossier.pk},
        )
        self.client.force_authenticate(self.investisseur)

    def _remplir_complet(self):
        for champ, valeur in [
            (self.champs["p"], "Morale"),
            (self.champs["a"], "Awa"),
            (self.champs["b"], "RCCM-123"),
        ]:
            ValeurChamp.objects.create(dossier=self.dossier, champ=champ, valeur=valeur)
        ValeurChamp.objects.create(
            dossier=self.dossier, champ=self.champs["f"], fichier="dossiers/x/y.pdf",
        )

    def test_soumission_incomplet_refusee(self):
        reponse = self.client.post(self.url)
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.statut, Dossier.Statut.BROUILLON)

    def test_soumission_complete_reussit(self):
        self._remplir_complet()
        reponse = self.client.post(self.url)
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.statut, Dossier.Statut.SOUMIS)
        self.assertIsNotNone(self.dossier.date_soumission)
        self.assertEqual(self.dossier.version, 1)

    def test_signature_otp_bout_en_bout(self):
        """Génération OTP → signature avec preuve serveur chaînée."""
        self._remplir_complet()
        url_otp = reverse("dossiers:dossier-generer-otp",
                          kwargs={"dossier_pk": self.dossier.pk})
        url_signer = reverse("dossiers:dossier-signer",
                             kwargs={"dossier_pk": self.dossier.pk})

        reponse = self.client.post(url_otp)
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        code = reponse.data["code"]
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

        reponse = self.client.post(url_signer, {"otp_code": code}, format="json")
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.type_signature, Dossier.TypeSignature.OTP)
        self.assertTrue(self.dossier.donnee_signature.startswith("sha256:"))
        self.assertEqual(len(self.dossier.donnee_signature), 7 + 64)
        self.assertIsNotNone(self.dossier.date_signature)
        self.assertIsNotNone(self.dossier.ip_signature)
        # Le code est consommé (à usage unique).
        self.assertEqual(self.dossier.otp_hash, "")

    def test_signature_otp_erreur_sans_code_genere(self):
        self._remplir_complet()
        reponse = self.client.post(
            reverse("dossiers:dossier-signer", kwargs={"dossier_pk": self.dossier.pk}),
            {"otp_code": "123456"},
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_signature_otp_code_invalide_refuse_et_code_restant_valide(self):
        self._remplir_complet()
        url_otp = reverse("dossiers:dossier-generer-otp",
                          kwargs={"dossier_pk": self.dossier.pk})
        url_signer = reverse("dossiers:dossier-signer",
                             kwargs={"dossier_pk": self.dossier.pk})
        code = self.client.post(url_otp).data["code"]

        reponse = self.client.post(url_signer, {"otp_code": "000000"}, format="json")
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.type_signature, "")

        # Le vrai code reste utilisable (3 tentatives côté front classiques).
        reponse = self.client.post(url_signer, {"otp_code": code}, format="json")
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)

    def test_signature_otp_expire_refusee(self):
        self._remplir_complet()
        url_otp = reverse("dossiers:dossier-generer-otp",
                          kwargs={"dossier_pk": self.dossier.pk})
        url_signer = reverse("dossiers:dossier-signer",
                             kwargs={"dossier_pk": self.dossier.pk})
        code = self.client.post(url_otp).data["code"]

        self.dossier.otp_expiration = timezone.now() - timedelta(minutes=1)
        self.dossier.save(update_fields=["otp_expiration"])

        reponse = self.client.post(url_signer, {"otp_code": code}, format="json")
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.otp_hash, "")

    def test_signature_otp_a_usage_unique(self):
        self._remplir_complet()
        url_otp = reverse("dossiers:dossier-generer-otp",
                          kwargs={"dossier_pk": self.dossier.pk})
        url_signer = reverse("dossiers:dossier-signer",
                             kwargs={"dossier_pk": self.dossier.pk})
        code = self.client.post(url_otp).data["code"]
        self.client.post(url_signer, {"otp_code": code}, format="json")
        reponse = self.client.post(url_signer, {"otp_code": code}, format="json")
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_la_soumission_n_accepte_plus_de_signature_en_clair(self):
        """L'ancien flux (texte libre) est fermé : la donnée est ignorée."""
        self._remplir_complet()
        reponse = self.client.post(
            self.url,
            {"type_signature": Dossier.TypeSignature.OTP, "donnee_signature": "trx-2026"},
        )
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.type_signature, "")
        self.assertEqual(self.dossier.donnee_signature, "")

    def test_resoumission_apres_soumission_refusee(self):
        self._remplir_complet()
        self.client.post(self.url)
        reponse = self.client.post(self.url)
        self.assertEqual(reponse.status_code, status.HTTP_409_CONFLICT)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.statut, Dossier.Statut.SOUMIS)

    def test_un_autre_investisseur_ne_soumet_pas(self):
        self._remplir_complet()
        self.client.force_authenticate(self.autre)
        reponse = self.client.post(self.url)
        self.assertEqual(reponse.status_code, status.HTTP_403_FORBIDDEN)


class CircuitAgentAPITests(APITestCase):
    """1D/1E/1F : prise en charge, relecture (commentaires), décision."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.investisseur = Utilisateur.objects.create_user("inv@example.com", "S3curise!2026")
        role_agent = Role.objects.filter(code="AGENT_SGI").first()
        self.agent = Utilisateur.objects.create_user(
            "agent@example.com", "S3curise!2026", sgi=self.sgi, role=role_agent,
        )
        self.agent_b = Utilisateur.objects.create_user(
            "agent-collegue@example.com", "S3curise!2026", sgi=self.sgi, role=role_agent,
        )
        self.admin_sgi = Utilisateur.objects.create_user(
            "admsgi@example.com", "S3curise!2026", sgi=self.sgi,
            role=Role.objects.filter(code="ADMIN_SGI").first(),
        )
        self.investisseur_b = Utilisateur.objects.create_user("invb@example.com", "S3curise!2026")
        self.champs = _configurer_parcours(self.sgi)
        self.dossier = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi,
        )

    def _soumettre(self):
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
        transiter(self.dossier, Dossier.Statut.SOUMIS)

    def _url(self, name):
        return reverse(name, kwargs={"dossier_pk": self.dossier.pk})

    def test_prise_en_charge_succes(self):
        self._soumettre()
        self.client.force_authenticate(self.agent)
        reponse = self.client.post(self._url("dossiers:dossier-prendre-en-charge"))
        self.assertEqual(reponse.status_code, status.HTTP_200_OK)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.statut, Dossier.Statut.EN_INSTRUCTION)
        self.assertEqual(self.dossier.agent_id, self.agent.id)
        self.assertIsNotNone(self.dossier.date_instruction)

    def test_investisseur_ou_etranger_refuse_la_prise_en_charge(self):
        self._soumettre()
        self.client.force_authenticate(self.investisseur)
        self.assertEqual(
            self.client.post(self._url("dossiers:dossier-prendre-en-charge")).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        role_agent = Role.objects.filter(code="AGENT_SGI").first()
        autre_sgi = SGI.objects.create(nom="SGI Bêta", code_sgi="SGIB")
        agent_autre = Utilisateur.objects.create_user(
            "agentb@example.com", "S3curise!2026", sgi=autre_sgi, role=role_agent,
        )
        self.client.force_authenticate(agent_autre)
        self.assertEqual(
            self.client.post(self._url("dossiers:dossier-prendre-en-charge")).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_prise_en_charge_hors_soumis_refusee(self):
        self.client.force_authenticate(self.agent)
        self.assertEqual(
            self.client.post(self._url("dossiers:dossier-prendre-en-charge")).status_code,
            status.HTTP_409_CONFLICT,
        )

    def test_validation_sans_signature_refusee_puis_ok(self):
        self._soumettre()
        self.client.force_authenticate(self.agent)
        self.client.post(self._url("dossiers:dossier-prendre-en-charge"))

        rep = self.client.post(self._url("dossiers:dossier-valider"))
        self.assertEqual(rep.status_code, status.HTTP_400_BAD_REQUEST)

        # Le dossier SOUMIS est figé : la signature doit avoir été posée
        # AVANT la soumission. On vérifie le circuit complet sur un
        # second dossier signé dans les règles.
        dossier_signe = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi,
        )
        for champ, valeur in [
            (self.champs["p"], "Morale"),
            (self.champs["a"], "Awa"),
            (self.champs["b"], "RCCM-123"),
        ]:
            ValeurChamp.objects.create(
                dossier=dossier_signe, champ=champ, valeur=valeur,
            )
        ValeurChamp.objects.create(
            dossier=dossier_signe, champ=self.champs["f"], fichier="dossiers/x/y.pdf",
        )
        transiter(dossier_signe, Dossier.Statut.SOUMIS)
        transiter(dossier_signe, Dossier.Statut.EN_INSTRUCTION, agent=self.agent)
        rep = self.client.post(
            reverse("dossiers:dossier-valider",
                    kwargs={"dossier_pk": dossier_signe.pk}),
        )
        self.assertEqual(rep.status_code, status.HTTP_400_BAD_REQUEST)

        # Signature OTP posée (via la base, comme le ferait l'API avant
        # soumission), puis nouveau dossier complet signé → validation OK.
        dossier_signable = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi,
        )
        for champ, valeur in [
            (self.champs["p"], "Morale"),
            (self.champs["a"], "Awa"),
            (self.champs["b"], "RCCM-123"),
        ]:
            ValeurChamp.objects.create(
                dossier=dossier_signable, champ=champ, valeur=valeur,
            )
        ValeurChamp.objects.create(
            dossier=dossier_signable, champ=self.champs["f"], fichier="dossiers/x/y.pdf",
        )
        code = generer_code_otp(dossier_signable)
        poser_signature_otp(dossier_signable, code)
        transiter(dossier_signable, Dossier.Statut.SOUMIS)
        transiter(dossier_signable, Dossier.Statut.EN_INSTRUCTION, agent=self.agent)
        rep = self.client.post(
            reverse("dossiers:dossier-valider",
                    kwargs={"dossier_pk": dossier_signable.pk}),
        )
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        dossier_signable.refresh_from_db()
        self.assertEqual(dossier_signable.statut, Dossier.Statut.VALIDE)

    def test_rejet_necessite_motif_puis_rejette(self):
        self._soumettre()
        self.client.force_authenticate(self.agent)
        self.client.post(self._url("dossiers:dossier-prendre-en-charge"))

        rep = self.client.post(self._url("dossiers:dossier-rejeter"))
        self.assertEqual(rep.status_code, status.HTTP_400_BAD_REQUEST)

        rep = self.client.post(
            self._url("dossiers:dossier-rejeter"), {"motif_rejet": "CNIB illisible"},
        )
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.statut, Dossier.Statut.REJETE)
        self.assertEqual(self.dossier.motif_rejet, "CNIB illisible")

    def test_seul_l_agent_assigne_ou_l_admin_sgi_decide(self):
        """La décision est liée à l'instruction (revue : décision liée à l'agent)."""
        self._soumettre()
        self.client.force_authenticate(self.agent)
        self.client.post(self._url("dossiers:dossier-prendre-en-charge"))

        # Un collègue de la même SGI ne peut pas décider à la place
        # de l'agent qui a instruit le dossier.
        self.client.force_authenticate(self.agent_b)
        rep = self.client.post(self._url("dossiers:dossier-valider"))
        self.assertEqual(rep.status_code, status.HTTP_400_BAD_REQUEST)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.statut, Dossier.Statut.EN_INSTRUCTION)

        # L'admin SGI de la même SGI garde la main (supervision) :
        # son rejet avec motif est accepté.
        self.client.force_authenticate(self.admin_sgi)
        rep = self.client.post(
            self._url("dossiers:dossier-rejeter"), {"motif_rejet": "Pièce illisible"},
        )
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.statut, Dossier.Statut.REJETE)

    def test_commentaire_agent_sur_valeur(self):
        self._soumettre()
        self.client.force_authenticate(self.agent)
        self.client.post(self._url("dossiers:dossier-prendre-en-charge"))

        valeur_a = ValeurChamp.objects.get(dossier=self.dossier, champ=self.champs["a"])
        rep = self.client.post(
            self._url("dossiers:dossier-commenter"),
            {"valeur": valeur_a.id, "commentaire": "Merci de reformuler le nom exactement comme sur la CNIB."},
        )
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        valeur_a.refresh_from_db()
        self.assertEqual(
            valeur_a.commentaire_agent,
            "Merci de reformuler le nom exactement comme sur la CNIB.",
        )
        self.assertFalse(valeur_a.est_corrige)

    def test_commentaire_hors_instruction_refuse(self):
        self._soumettre()
        self.client.force_authenticate(self.agent)
        valeur_a = ValeurChamp.objects.get(dossier=self.dossier, champ=self.champs["a"])
        rep = self.client.post(
            self._url("dossiers:dossier-commenter"),
            {"valeur": valeur_a.id, "commentaire": "À corriger"},
        )
        self.assertEqual(rep.status_code, status.HTTP_409_CONFLICT)

    def test_investisseur_corrige_apres_rejet_signalant_a_relecture(self):
        self._soumettre()
        self.client.force_authenticate(self.agent)
        rep = self.client.post(self._url("dossiers:dossier-prendre-en-charge"))
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        valeur_a = ValeurChamp.objects.get(dossier=self.dossier, champ=self.champs["a"])
        rep = self.client.post(
            self._url("dossiers:dossier-commenter"),
            {"valeur": valeur_a.id, "commentaire": "À reformuler"},
        )
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        # L'agent rejette le dossier : l'investisseur peut le corriger puis
        # le resoumettre (UC12).
        rep = self.client.post(
            self._url("dossiers:dossier-rejeter"), {"motif_rejet": "Nom à reformuler"},
        )
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        dossier = Dossier.objects.get(pk=self.dossier.pk)
        self.assertEqual(dossier.statut, Dossier.Statut.REJETE)

        self.client.force_authenticate(self.investisseur)
        url_valeurs = reverse("dossiers:dossier-valeurs", kwargs={"dossier_pk": self.dossier.pk})
        rep = self.client.post(url_valeurs, {"champ": self.champs["a"].pk, "valeur": "Awa Koné"})
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        valeur_a.refresh_from_db()
        self.assertEqual(valeur_a.valeur, "Awa Koné")
        self.assertTrue(valeur_a.est_corrige)
        self.assertEqual(valeur_a.commentaire_agent, "À reformuler")

        # Resoumission (1G) : le dossier repart en SOUMIS, version incrémentée.
        url_soumettre = self._url("dossiers:dossier-soumettre")
        self.client.force_authenticate(self.investisseur)
        rep = self.client.post(url_soumettre)
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.statut, Dossier.Statut.SOUMIS)
        self.assertEqual(self.dossier.version, 2)


class FileAttenteFiltresTests(APITestCase):
    """2A : UC13 — filtres et tri de la file d'attente."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.investisseur = Utilisateur.objects.create_user("inv@example.com", "S3curise!2026")
        self.investisseur_b = Utilisateur.objects.create_user("beta@example.com", "S3curise!2026")
        role_agent = Role.objects.filter(code="AGENT_SGI").first()
        self.agent = Utilisateur.objects.create_user(
            "agent@example.com", "S3curise!2026", sgi=self.sgi, role=role_agent,
        )
        self.client.force_authenticate(self.agent)
        self.url = reverse("dossiers:dossier-list-create")

    def _dossier(self, utilisateur, statut, date_soumission=None):
        dossier = Dossier.objects.create(utilisateur=utilisateur, sgi=self.sgi)
        if statut != Dossier.Statut.BROUILLON:
            dossier.statut = statut
            dossier.date_soumission = date_soumission or timezone.now()
            dossier.save(update_fields=["statut", "date_soumission"])
        return dossier

    def test_filtre_par_statut_multiples(self):
        self._dossier(self.investisseur, Dossier.Statut.SOUMIS)
        self._dossier(self.investisseur, Dossier.Statut.EN_INSTRUCTION)
        self._dossier(self.investisseur, Dossier.Statut.BROUILLON)

        rep = self.client.get(self.url, {"statut": ["SOUMIS", "EN_INSTRUCTION"]})
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        self.assertEqual(rep.data["count"], 2)

        rep = self.client.get(self.url, {"statut": ["BROUILLON"]})
        self.assertEqual(rep.data["count"], 1)

    def test_filtre_par_reference_et_email(self):
        d1 = self._dossier(self.investisseur, Dossier.Statut.SOUMIS)
        d2 = self._dossier(self.investisseur_b, Dossier.Statut.SOUMIS)

        rep = self.client.get(self.url, {"recherche": "beta@example.com"})
        self.assertEqual(rep.data["count"], 1)
        self.assertEqual(rep.data["results"][0]["id"], str(d2.pk))

        # Recherche par référence (fragment en minuscules/majuscules)
        rep = self.client.get(
            self.url, {"recherche": d1.reference.split("-")[-1]},
        )
        self.assertEqual(rep.data["count"], 1)
        self.assertEqual(rep.data["results"][0]["id"], str(d1.pk))

    def test_filt_par_dates_de_soumission(self):
        self._dossier(self.investisseur, Dossier.Statut.SOUMIS, "2026-01-10T08:00:00Z")
        self._dossier(self.investisseur, Dossier.Statut.SOUMIS, "2026-01-20T08:00:00Z")
        self._dossier(self.investisseur, Dossier.Statut.SOUMIS, "2026-02-01T08:00:00Z")

        rep = self.client.get(
            self.url, {"date_debut": "2026-01-15", "date_fin": "2026-01-31"},
        )
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        self.assertEqual(rep.data["count"], 1)

    def test_date_invalide_donne_400(self):
        rep = self.client.get(self.url, {"date_debut": "pas-une-date"})
        self.assertEqual(rep.status_code, status.HTTP_400_BAD_REQUEST)

    def test_tri_anciens(self):
        d1 = self._dossier(self.investisseur, Dossier.Statut.SOUMIS, "2026-01-10T08:00:00Z")
        d2 = self._dossier(self.investisseur, Dossier.Statut.SOUMIS, "2026-01-05T08:00:00Z")
        rep = self.client.get(self.url, {"tri": "anciens"})
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        resultats = [e["id"] for e in rep.data["results"]]
        # Le plus anciennement soumis (d2) doit arriver en premier (FCFS).
        attendu = [str(id_) for id_ in (
            Dossier.objects.filter(pk__in=[d1.pk, d2.pk])
            .order_by("date_soumission").values_list("pk", flat=True)
        )]
        self.assertEqual(resultats[:2], attendu)

    def test_cloisonnement_conserve_avec_filtres(self):
        autre_sgi = SGI.objects.create(nom="SGI Bêta", code_sgi="SGIB")
        Dossier.objects.create(utilisateur=self.investisseur, sgi=autre_sgi)
        self._dossier(self.investisseur, Dossier.Statut.SOUMIS)
        rep = self.client.get(self.url, {"statut": "SOUMIS"})
        self.assertEqual(rep.data["count"], 1)
        self.assertEqual(str(rep.data["results"][0]["sgi"]), str(self.sgi.pk))


class CycleCompletAPITests(APITestCase):
    """1H : bout-en-bout du cycle de vie complet, uniquement via l'API."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.investisseur = Utilisateur.objects.create_user("inv@example.com", "S3curise!2026")
        self.investisseur_b = Utilisateur.objects.create_user("invb@example.com", "S3curise!2026")
        role_agent = Role.objects.filter(code="AGENT_SGI").first()
        self.agent = Utilisateur.objects.create_user(
            "agent@example.com", "S3curise!2026", sgi=self.sgi, role=role_agent,
        )
        self.champs = _configurer_parcours(self.sgi)

    def test_cycle_creation_a_validation(self):
        # 1. Création (UC03)
        self.client.force_authenticate(self.investisseur)
        rep = self.client.post(
            reverse("dossiers:dossier-list-create"),
            {"sgi": self.sgi.pk},
        )
        self.assertEqual(rep.status_code, status.HTTP_201_CREATED)
        dossier_pk = rep.data["id"]
        url_valeurs = reverse("dossiers:dossier-valeurs", kwargs={"dossier_pk": dossier_pk})
        url_soumission = reverse("dossiers:dossier-soumettre", kwargs={"dossier_pk": dossier_pk})

        # 2. Remplissage des valeurs via l'API
        for champ, valeur in [
            (self.champs["p"], "Morale"),
            (self.champs["a"], "Awa Koné"),
            (self.champs["b"], "RCCM-2026-042"),
        ]:
            self.assertEqual(
                self.client.post(url_valeurs, {"champ": champ.pk, "valeur": valeur}).status_code,
                status.HTTP_201_CREATED,
            )
        # Dernier champ (justificatif FICHIER) via la base : l'upload réel
        # exige un stockage objet (MinIO) absent des tests unitaires.
        ValeurChamp.objects.create(
            dossier_id=dossier_pk, champ=self.champs["f"], fichier="dossiers/x/y.pdf",
        )
        dossier = Dossier.objects.get(pk=dossier_pk)
        # La valeur stockée date du dernier recalcul ; le workflow soumet
        # en recalculant à chaud : on vérifie donc le service directement.
        self.assertEqual(calculer_progression_pct(dossier), 100)

        # 3. Signature OTP (preuve serveur) puis soumission (UC10)
        url_otp = reverse("dossiers:dossier-generer-otp", kwargs={"dossier_pk": dossier_pk})
        url_signer = reverse("dossiers:dossier-signer", kwargs={"dossier_pk": dossier_pk})
        rep = self.client.post(url_otp)
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        code = rep.data["code"]
        rep = self.client.post(
            url_signer, {"otp_code": code}, format="json",
        )
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        self.assertTrue(rep.data["donnee_signature"].startswith("sha256:"))
        rep = self.client.post(url_soumission)
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        dossier.refresh_from_db()
        self.assertEqual(dossier.statut, Dossier.Statut.SOUMIS)

        # 4. Prise en charge (UC14)
        self.client.force_authenticate(self.agent)
        rep = self.client.post(
            reverse("dossiers:dossier-prendre-en-charge", kwargs={"dossier_pk": dossier_pk})
        )
        self.assertEqual(rep.status_code, status.HTTP_200_OK)

        # 5. Validation (UC14) → état terminal
        rep = self.client.post(
            reverse("dossiers:dossier-valider", kwargs={"dossier_pk": dossier_pk})
        )
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        dossier.refresh_from_db()
        self.assertEqual(dossier.statut, Dossier.Statut.VALIDE)
        self.assertIsNotNone(dossier.date_instruction)
        self.assertIsNotNone(dossier.date_decision)
        self.assertEqual(dossier.agent_id, self.agent.id)

    def test_cloisonnement_listes_investisseurs(self):
        d1 = Dossier.objects.create(utilisateur=self.investisseur, sgi=self.sgi)
        d2 = Dossier.objects.create(utilisateur=self.investisseur_b, sgi=self.sgi)

        self.client.force_authenticate(self.investisseur)
        rep = self.client.get(reverse("dossiers:dossier-list-create"))
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        identifiants = [e["id"] for e in rep.data["results"]]
        self.assertIn(str(d1.pk), identifiants)
        self.assertNotIn(str(d2.pk), identifiants)

        # L'investisseur ne peut pas lire le détail du dossier d'autrui.
        rep = self.client.get(
            reverse("dossiers:dossier-detail", kwargs={"pk": d2.pk})
        )
        self.assertEqual(rep.status_code, status.HTTP_403_FORBIDDEN)

    def test_cloisonnement_agent_etrangere_liste_vide(self):
        autre_sgi = SGI.objects.create(nom="SGI Bêta", code_sgi="SGIB")
        role_agent = Role.objects.filter(code="AGENT_SGI").first()
        agent_b = Utilisateur.objects.create_user(
            "agentb@example.com", "S3curise!2026", sgi=autre_sgi, role=role_agent,
        )
        Dossier.objects.create(utilisateur=self.investisseur, sgi=self.sgi)

        self.client.force_authenticate(agent_b)
        rep = self.client.get(reverse("dossiers:dossier-list-create"))
        self.assertEqual(rep.status_code, status.HTTP_200_OK)
        self.assertEqual(rep.data["count"], 0)


class MachineAEtatsTests(APITestCase):
    """Étape 1B : transitions autorisées, sauts interdits, préconditions."""

    def setUp(self):
        self.sgi = SGI.objects.create(nom="SGI Alpha", code_sgi="SGIA")
        self.investisseur = Utilisateur.objects.create_user("inv@example.com", "S3curise!2026")
        role_agent = Role.objects.filter(code="AGENT_SGI").first()
        self.agent = Utilisateur.objects.create_user(
            "agent@example.com", "S3curise!2026", sgi=self.sgi, role=role_agent,
        )
        self.autre_sgi = SGI.objects.create(nom="SGI Bêta", code_sgi="SGIB")
        role_agent = Role.objects.filter(code="AGENT_SGI").first()
        self.agent_autre_sgi = Utilisateur.objects.create_user(
            "autre@example.com", "S3curise!2026", sgi=self.autre_sgi, role=role_agent,
        )

        self.champs = _configurer_parcours(self.sgi)
        self.dossier = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi,
        )

    def _progression_100(self):
        for champ, valeur in [
            (self.champs["p"], "Morale"),
            (self.champs["a"], "Awa"),
            (self.champs["b"], "RCCM-123"),
        ]:
            ValeurChamp.objects.create(
                dossier=self.dossier, champ=champ, valeur=valeur,
            )
        ValeurChamp.objects.create(
            dossier=self.dossier, champ=self.champs["f"], fichier="dossiers/x/y.pdf",
        )

    def test_graphe_des_transitions(self):
        self.assertEqual(
            TRANSITIONS,
            {
                Dossier.Statut.BROUILLON: {Dossier.Statut.SOUMIS},
                Dossier.Statut.SOUMIS: {Dossier.Statut.EN_INSTRUCTION},
                Dossier.Statut.EN_INSTRUCTION: {Dossier.Statut.VALIDE, Dossier.Statut.REJETE},
                Dossier.Statut.REJETE: {Dossier.Statut.SOUMIS},
                Dossier.Statut.VALIDE: set(),
            },
        )

    def test_soumission_impossible_si_incomplet(self):
        with self.assertRaises(ValidationError):
            transiter(self.dossier, Dossier.Statut.SOUMIS)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.statut, Dossier.Statut.BROUILLON)

    def test_soumission_apres_remplissage_complet(self):
        self._progression_100()
        transiter(self.dossier, Dossier.Statut.SOUMIS)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.statut, Dossier.Statut.SOUMIS)
        self.assertIsNotNone(self.dossier.date_soumission)
        self.assertEqual(self.dossier.version, 1)

    def test_saut_illegal_so_umis_vers_valide_refuse(self):
        self._progression_100()
        transiter(self.dossier, Dossier.Statut.SOUMIS)
        with self.assertRaises(ValidationError):
            transiter(self.dossier, Dossier.Statut.VALIDE, agent=self.agent)
        # Pire : saut direct BROUILLON→VALIDE, sans passer par l'instruction
        autre = Dossier.objects.create(
            utilisateur=self.investisseur, sgi=self.sgi,
        )
        for champ, valeur in [
            (self.champs["p"], "Morale"),
            (self.champs["a"], "Awa"),
            (self.champs["b"], "RCCM-123"),
        ]:
            ValeurChamp.objects.create(dossier=autre, champ=champ, valeur=valeur)
        ValeurChamp.objects.create(
            dossier=autre, champ=self.champs["f"], fichier="dossiers/x/y.pdf",
        )
        with self.assertRaises(ValidationError):
            transiter(autre, Dossier.Statut.VALIDE, agent=self.agent)

    def test_noop_meme_statut_refuse(self):
        with self.assertRaises(ValidationError):
            transiter(self.dossier, Dossier.Statut.BROUILLON)  # pas de no-op

    def test_prise_en_charge_agent_etrangere_refusee(self):
        self._progression_100()
        transiter(self.dossier, Dossier.Statut.SOUMIS)
        with self.assertRaises(ValidationError):
            transiter(self.dossier, Dossier.Statut.EN_INSTRUCTION, agent=self.agent_autre_sgi)
        # refus sans agent également
        with self.assertRaises(ValidationError):
            transiter(self.dossier, Dossier.Statut.EN_INSTRUCTION)

    def test_prise_en_charge_valide(self):
        self._progression_100()
        transiter(self.dossier, Dossier.Statut.SOUMIS)
        transiter(self.dossier, Dossier.Statut.EN_INSTRUCTION, agent=self.agent)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.statut, Dossier.Statut.EN_INSTRUCTION)
        self.assertEqual(self.dossier.agent_id, self.agent.id)
        self.assertIsNotNone(self.dossier.date_instruction)

    def test_decision_validation_necessite_signature(self):
        self._progression_100()
        transiter(self.dossier, Dossier.Statut.SOUMIS)
        transiter(self.dossier, Dossier.Statut.EN_INSTRUCTION, agent=self.agent)
        with self.assertRaises(ValidationError):
            transiter(self.dossier, Dossier.Statut.VALIDE, agent=self.agent)
        # Signature OTP posée dans les règles (preuve serveur) → OK
        code = generer_code_otp(self.dossier)
        poser_signature_otp(self.dossier, code)
        transiter(self.dossier, Dossier.Statut.VALIDE, agent=self.agent)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.statut, Dossier.Statut.VALIDE)
        self.assertIsNotNone(self.dossier.date_decision)

    def test_rejet_necessite_motif(self):
        self._progression_100()
        transiter(self.dossier, Dossier.Statut.SOUMIS)
        transiter(self.dossier, Dossier.Statut.EN_INSTRUCTION, agent=self.agent)
        with self.assertRaises(ValidationError):
            transiter(self.dossier, Dossier.Statut.REJETE, agent=self.agent)
        transiter(self.dossier, Dossier.Statut.REJETE, agent=self.agent, motif_rejet="Pièce illisible")
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.statut, Dossier.Statut.REJETE)
        self.assertEqual(self.dossier.motif_rejet, "Pièce illisible")

    def test_resoumission_apres_rejet_incrmente_version(self):
        self._progression_100()
        transiter(self.dossier, Dossier.Statut.SOUMIS)
        transiter(self.dossier, Dossier.Statut.EN_INSTRUCTION, agent=self.agent)
        transiter(self.dossier, Dossier.Statut.REJETE, agent=self.agent, motif_rejet="Justificatif abimé")
        transiter(self.dossier, Dossier.Statut.SOUMIS)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.statut, Dossier.Statut.SOUMIS)
        self.assertEqual(self.dossier.version, 2)
        self.assertIsNone(self.dossier.agent_id)
        self.assertIsNone(self.dossier.date_instruction)
        self.assertIsNone(self.dossier.date_decision)