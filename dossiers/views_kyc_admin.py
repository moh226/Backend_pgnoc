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
from django.core.exceptions import ValidationError
from rest_framework import generics, permissions, status
from rest_framework.response import Response

from audit.models import JournalAudit
from audit.services import journaliser
from comptes.permissions import EstAdminSGI
from dossiers.models import ChampKYC, Dossier, EtapeKYC, ValeurChamp
from dossiers.serializers_kyc_admin import (
    ChampKYCAdminSerializer, EtapeKYCAdminSerializer,
)


def _apercu_etape(etape):
    return {"nom": etape.nom, "ordre": etape.ordre, "actif": etape.actif}


def _apercu_champ(champ):
    return {
        "code": champ.code,
        "nom": champ.nom,
        "type": champ.type,
        "obligatoire": champ.obligatoire,
        "actif": champ.actif,
        "champ_parent": str(champ.champ_parent_id) if champ.champ_parent_id else None,
    }


def _est_uuid_valide(valeur):
    """True si `valeur` est un UUID bien formé (évite un 500 sur filtre)."""
    import uuid

    try:
        uuid.UUID(str(valeur))
        return True
    except (ValueError, AttributeError):
        return False


class EtapeKYCListCreateAPIView(generics.ListCreateAPIView):
    """Étapes du parcours de ma SGI (GET) / création (POST)."""

    serializer_class = EtapeKYCAdminSerializer
    permission_classes = (permissions.IsAuthenticated, EstAdminSGI)

    def get_queryset(self):
        return EtapeKYC.objects.filter(sgi_id=self.request.user.sgi_id)

    def perform_create(self, serializer):
        etape = serializer.save()
        journaliser(
            self.request.user,
            JournalAudit.Action.CREATION_ETAPE_KYC,
            "EtapeKYC", str(etape.pk),
            apres=_apercu_etape(etape),
            requete=self.request,
        )


class EtapeKYCRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    """Détail / modification / suppression d'une étape de ma SGI."""

    serializer_class = EtapeKYCAdminSerializer
    permission_classes = (permissions.IsAuthenticated, EstAdminSGI)

    def get_queryset(self):
        return EtapeKYC.objects.filter(sgi_id=self.request.user.sgi_id)

    def perform_update(self, serializer):
        etape = serializer.save()
        journaliser(
            self.request.user,
            JournalAudit.Action.MODIFICATION_ETAPE_KYC,
            "EtapeKYC", str(etape.pk),
            apres=_apercu_etape(etape),
            requete=self.request,
        )

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
        journaliser(
            request.user,
            JournalAudit.Action.SUPPRESSION_ETAPE_KYC,
            "EtapeKYC", str(etape.pk),
            avant=_apercu_etape(etape),
            requete=request,
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
            if not _est_uuid_valide(etape):
                raise ValidationError(
                    {"etape": "Le paramètre `etape` doit être un identifiant UUID valide."}
                )
            qs = qs.filter(etape_id=etape)
        return qs.select_related("etape", "champ_parent")

    def perform_create(self, serializer):
        champ = serializer.save()
        journaliser(
            self.request.user,
            JournalAudit.Action.CREATION_CHAMP_KYC,
            "ChampKYC", str(champ.pk),
            apres=_apercu_champ(champ),
            requete=self.request,
        )


class ChampKYCRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    """Détail / modification / suppression d'un champ de ma SGI."""

    serializer_class = ChampKYCAdminSerializer
    permission_classes = (permissions.IsAuthenticated, EstAdminSGI)

    def get_queryset(self):
        return ChampKYC.objects.filter(etape__sgi_id=self.request.user.sgi_id)

    def perform_update(self, serializer):
        champ = serializer.save()
        journaliser(
            self.request.user,
            JournalAudit.Action.MODIFICATION_CHAMP_KYC,
            "ChampKYC", str(champ.pk),
            apres=_apercu_champ(champ),
            requete=self.request,
        )

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
        journaliser(
            request.user,
            JournalAudit.Action.SUPPRESSION_CHAMP_KYC,
            "ChampKYC", str(champ.pk),
            avant=_apercu_champ(champ),
            requete=request,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)