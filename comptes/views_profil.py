"""Vues « moi » : profil personnel et sécurité (paramètres).

Ces endpoints concernent le compte de l'utilisateur connecté,
quel que soit son rôle — c'est le socle de la page Paramètres.

Chaque action sensible (modification de profil, changement de mot
de passe) est tracée dans le journal d'audit (conformité CREPMF,
§8.3).
"""

from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from audit.models import JournalAudit
from audit.services import journaliser
from comptes.serializers import (
    ChangerMotDePasseSerializer,
    ProfilMoiSerializer,
)


class ProfilMoiAPIView(generics.RetrieveUpdateAPIView):
    """GET/PATCH du profil de l'utilisateur connecté.

    GET  /api/comptes/moi/profil/   → profil complet du rôle
    PATCH /api/comptes/moi/profil/  → prénom, nom + champ métier du rôle
    """

    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = ProfilMoiSerializer

    def get_object(self):
        return self.request.user

    def perform_update(self, serializer):
        avant = {
            "prenom": self.request.user.prenom,
            "nom": self.request.user.nom,
        }
        utilisateur = serializer.save()
        journaliser(
            utilisateur,
            JournalAudit.Action.MODIFICATION_PROFIL,
            "Utilisateur",
            str(utilisateur.pk),
            avant=avant,
            apres={"prenom": utilisateur.prenom, "nom": utilisateur.nom},
            requete=self.request,
        )


class ChangerMotDePasseAPIView(APIView):
    """POST /api/comptes/moi/mot-de-passe/ — change le mot de passe.

    Vérifie l'ancien mot de passe, puis révoque tous les refresh
    tokens encore en circulation de l'utilisateur (rotation forcée) :
    toute autre session est déconnectée à son prochain renouvellement.
    Les access tokens restent valides jusqu'à expiration naturelle
    (15 min) ; le frontend reconnecte explicitement l'utilisateur.
    """

    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request):
        serializer = ChangerMotDePasseSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)

        utilisateur = request.user
        utilisateur.set_password(serializer.validated_data["nouveau_mot_de_passe"])
        utilisateur.save(update_fields=["password"])

        for jeton in OutstandingToken.objects.filter(user=utilisateur):
            BlacklistedToken.objects.get_or_create(token=jeton)

        journaliser(
            utilisateur,
            JournalAudit.Action.CHANGEMENT_MOT_DE_PASSE,
            "Utilisateur",
            str(utilisateur.pk),
            apres={"mot_de_passe_changed": True},
            requete=request,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)