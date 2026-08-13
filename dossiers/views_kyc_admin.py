"""Paramétrage du parcours KYC par l'Admin SGI (UC15).

Règles appliquées :
- Seul un `ADMIN_SGI` accède à ces routes.
- Tout est cloisonné à sa SGI : querysets filtrés, donc tout accès
  (lecture, modification, suppression) au contenu d'une autre SGI
  renvoie 404.
- Suppression garde-fou : une étape dont un champ porte déjà des valeurs
  saisies (ou pointée par un dossier en cours) et un champ porteur de
  valeurs ou parent d'autres champs sont inaltérables → 409 ; la
  désactivation (`actif=false`) reste le levier métier.
"""

from django.db.models.deletion import ProtectedError
from rest_framework import generics, permissions, status
from rest_framework.response import Response

from comptes.permissions import EstAdminSGI
from dossiers.models import ChampKYC, Dossier, EtapeKYC, ValeurChamp
from dossiers.serializers_kyc_admin import (
    ChampKYCAdminSerializer, EtapeKYCAdminSerializer,
)


class EtapeKYCListCreateAPIView(generics.ListCreateAPIView):
    """Étapes du parcours de ma SGI (GET) / création (POST)."""

    serializer_class = EtapeKYCAdminSerializer
    permission_classes = (permissions.IsAuthenticated, EstAdminSGI)

    def get_queryset(self):
        return EtapeKYC.objects.filter(sgi_id=self.request.user.sgi_id)


class EtapeKYCRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    """Détail / modification / suppression d'une étape de ma SGI."""

    serializer_class = EtapeKYCAdminSerializer
    permission_classes = (permissions.IsAuthenticated, EstAdminSGI)

    def get_queryset(self):
        return EtapeKYC.objects.filter(sgi_id=self.request.user.sgi_id)

    def delete(self, request, *args, **kwargs):
        etape = self.get_object()
        if ValeurChamp.objects.filter(champ__etape=etape).exists():
            return Response(
                {"detail": "Des valeurs ont déjà été saisies pour cette "
                           "étape : désactivez-la plutôt que de la supprimer."},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            etape.delete()
        except ProtectedError:
            return Response(
                {"detail": "Cette étape est référencée par des dossiers en "
                           "cours : désactivez-la plutôt que de la supprimer."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChampKYCListCreateAPIView(generics.ListCreateAPIView):
    """Champs de mes étapes (GET, filtrable par `etape=`) / création (POST)."""

    serializer_class = ChampKYCAdminSerializer
    permission_classes = (permissions.IsAuthenticated, EstAdminSGI)

    def get_queryset(self):
        qs = ChampKYC.objects.filter(etape__sgi_id=self.request.user.sgi_id)
        etape = self.request.query_params.get("etape")
        if etape:
            qs = qs.filter(etape_id=etape)
        return qs.select_related("etape", "champ_parent")


class ChampKYCRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    """Détail / modification / suppression d'un champ de ma SGI."""

    serializer_class = ChampKYCAdminSerializer
    permission_classes = (permissions.IsAuthenticated, EstAdminSGI)

    def get_queryset(self):
        return ChampKYC.objects.filter(etape__sgi_id=self.request.user.sgi_id)

    def delete(self, request, *args, **kwargs):
        champ = self.get_object()
        if ValeurChamp.objects.filter(champ_id=champ.pk).exists():
            return Response(
                {"detail": "Des valeurs ont déjà été saisies pour ce champ : "
                           "désactivez-le plutôt que de le supprimer."},
                status=status.HTTP_409_CONFLICT,
            )
        if ChampKYC.objects.filter(champ_parent_id=champ.pk).exists():
            return Response(
                {"detail": "Ce champ est parent d'autres champs : "
                           "désactivez-le plutôt que de le supprimer."},
                status=status.HTTP_409_CONFLICT,
            )
        champ.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)