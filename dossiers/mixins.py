"""Mixins réutilisables pour les vues DRF de l'app dossiers."""

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response

from dossiers.models import Dossier, ValeurChamp
from pgnoc.erreurs import erreur


class ChampCorrigeableMixin:
    """En REJETE, seuls les champs signalés par l'agent sont corrigeables (UC12).

    L'investisseur peut corriger un dossier rejeté, mais uniquement sur
    les champs que l'agent a commentés lors de la relecture : les
    autres champs ont été jugés conformes et restent figés.
    """

    def verifier_champ_corrigeable(self, dossier, champ):
        """Retourne une Response 403 si le champ n'est pas corrigeable, sinon None.

        Utilisation : `if conflit := self.verifier_champ_corrigeable(dossier, champ): return conflit`

        Règle UC12 : en REJETE, sont modifiables
          - les champs signalés par l'agent (commentaire de relecture) ;
          - les champs OBLIGATOIREs dont la valeur n'a JAMAIS été relue
            par l'agent : soit sans valeur, soit avec une valeur créée
            APRÈS le rejet (champ ajouté/activé par la SGI après coup,
            ou champ resté vide). Sans cette ouverture, le dossier serait
            définitivement bloqué : la progression exige 100 % mais ces
            champs seraient interdits d'accès. L'agent n'ayant rien jugé
            sur une valeur inexistante au moment de sa relecture, aucune
            décision n'est contournée. Le « créée après le rejet » se
            teste par date_creation (auto_now_add : immuable lors des
            update_or_create successifs) contre date_decision : la
            valeur reste donc éditable AUTANT DE FOIS que nécessaire
            tant qu'elle n'est pas passée par une relecture (régression
            historique : seule la première écriture passait, toute
            retouche était ensuite refusée). Les champs facultatifs,
            eux, restent figés : on ne ré-ouvre pas la saisie générale
            après rejet.

        L'erreur embarque le champ fautif (`champ`) ET la liste des
        champs à corriger (`champs_a_corriger`, avec le commentaire de
        l'agent) : le frontend peut verrouiller visuellement le bon
        champ et proposer un parcours guidé vers les corrections.
        """
        if dossier.statut != Dossier.Statut.REJETE:
            return None
        existante = ValeurChamp.objects.filter(dossier=dossier, champ=champ).first()
        if existante and existante.commentaire_agent:
            return None
        if not existante and champ.obligatoire:
            return None
        if (
            existante
            and champ.obligatoire
            and dossier.date_decision
            and existante.date_creation
            and existante.date_creation > dossier.date_decision
        ):
            return None
        return erreur(
            "CHAMP_VERROUILLE",
            (
                f"Le champ « {champ.nom} » a été jugé conforme lors de la "
                "relecture : il n'est pas modifiable. Seuls les champs "
                "signalés par l'agent peuvent être corrigés."
            ),
            status.HTTP_403_FORBIDDEN,
            champ=champ.nom,
            champs_a_corriger=self.champs_a_corriger(dossier),
        )

    @staticmethod
    def champs_a_corriger(dossier):
        """Champs signalés par l'agent (commentaire de relecture) sur CE dossier.

        Chaque entrée porte le nom du champ ET le commentaire de l'agent :
        l'investisseur voit ce qui est attendu de lui sans avoir à
        deviner ni re-parcourir tout le formulaire.
        """
        valeurs = (
            ValeurChamp.objects.filter(dossier=dossier)
            .exclude(commentaire_agent="")
            .select_related("champ")
        )
        return [
            {
                "champ": v.champ.nom,
                "code": v.champ.code,
                "commentaire_agent": v.commentaire_agent,
            }
            for v in valeurs
        ]


class DossierProprietaireMixin:
    """Factorise l'accès à un Dossier imbriqué dans l'URL et sa vérification de propriété.

    Réutilisé par toute vue montée sous `/dossiers/<dossier_pk>/...`
    où seul l'investisseur propriétaire peut agir (remplissage de
    champs, upload de fichiers, et bientôt soumission — Étape 2.5).
    """

    def get_dossier(self):
        """Récupère le dossier et vérifie que l'utilisateur en est propriétaire.

        Mis en cache sur l'instance de la vue : DRF appelle souvent
        cette méthode plusieurs fois dans un même cycle de requête
        (ex : `get_serializer_context()` puis `create()`/`post()`) —
        le cache évite une requête SQL redondante à chaque appel.
        """
        if not hasattr(self, "_dossier_cache"):
            dossier_pk = self.kwargs.get("dossier_pk") or self.kwargs.get("pk")
            dossier = get_object_or_404(Dossier, pk=dossier_pk)
            if dossier.utilisateur_id != self.request.user.id:
                self.permission_denied(self.request, message="Ce dossier ne vous appartient pas.")
            self._dossier_cache = dossier
        return self._dossier_cache

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["dossier"] = self.get_dossier()
        return context

    def verifier_dossier_modifiable(self, dossier):
        """Retourne une Response 409 si le dossier n'est pas éditable, sinon None.

        Un dossier est éditable dans deux cas : BROUILLON (première
        saisie) et REJETE (corrections demandées par l'agent, UC12).

        Le message nomme le statut ACTUEL en clair (« Validé », « En
        instruction »…) : l'utilisateur comprend pourquoi l'action est
        refusée au lieu d'un code technique du type « statut différent
        de BROUILLON/REJETE » qui ne dit rien de SON dossier.

        Utilisation : `if conflit := self.verifier_dossier_modifiable(dossier): return conflit`
        """
        if dossier.statut not in (
            Dossier.Statut.BROUILLON,
            Dossier.Statut.REJETE,
        ):
            return erreur(
                "DOSSIER_NON_MODIFIABLE",
                (
                    f"Ce dossier n'est plus modifiable : il est actuellement "
                    f"« {dossier.get_statut_display()} »."
                ),
                status.HTTP_409_CONFLICT,
                statut_actuel=dossier.statut,
            )
        return None