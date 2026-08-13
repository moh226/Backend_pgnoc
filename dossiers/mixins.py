"""Mixins réutilisables pour les vues DRF de l'app dossiers."""

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response

from dossiers.models import Dossier


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
            dossier = get_object_or_404(Dossier, pk=self.kwargs["dossier_pk"])
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

        Utilisation : `if conflit := self.verifier_dossier_modifiable(dossier): return conflit`
        """
        if dossier.statut not in (
            Dossier.Statut.BROUILLON,
            Dossier.Statut.REJETE,
        ):
            return Response(
                {
                    "detail": "Ce dossier n'est plus modifiable (statut différent de BROUILLON/REJETE).",
                },
                status=status.HTTP_409_CONFLICT,
            )
        return None