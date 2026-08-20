"""Vues de la page d'accueil : rendu public + édition Admin Général.

La page d'accueil est la vitrine publique de PGNOC-TI. Elle n'est
éditable que par l'Admin Général (`EstAdminGeneral`) : chaque écriture
est tracée dans le journal d'audit (avant/après, IP, User-Agent).

Le public ne voit jamais de brouillon : un bloc n'apparaît qu'une
fois actif ET publié (`date_publication` positionnée).
"""

from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.response import Response

from accueil.models import BlocAccueil
from accueil.serializers import (
    BlocAccueilAdminSerializer,
    BlocAccueilOrdreSerializer,
    BlocAccueilPublicSerializer,
)
from audit.models import JournalAudit
from audit.services import journaliser
from comptes.permissions import EstAdminGeneral


def _snapshot_bloc(bloc):
    return {
        "type": bloc.type,
        "titre": bloc.titre,
        "actif": bloc.actif,
        "ordre": bloc.ordre,
        "publie": bloc.est_publie(),
        "contenu": bloc.contenu,
    }


class AccueilPublicAPIView(generics.ListAPIView):
    """GET /api/accueil/ — page d'accueil publique.

    Blocs actifs et publiés uniquement, dans l'ordre d'affichage.
    Endpoint public (AllowAny) : aucune donnée personnelle.
    """

    permission_classes = (permissions.AllowAny,)
    serializer_class = BlocAccueilPublicSerializer
    # Liste courte et ordonnée (≤ 8 blocs) : pas de pagination.
    pagination_class = None

    def get_queryset(self):
        return BlocAccueil.objects.filter(
            actif=True, date_publication__isnull=False,
        )


class BlocAccueilAdminListAPIView(generics.ListAPIView):
    """GET /api/admin-general/accueil/ — tous les blocs (brouillons inclus)."""

    permission_classes = (permissions.IsAuthenticated, EstAdminGeneral)
    serializer_class = BlocAccueilAdminSerializer
    pagination_class = None

    def get_queryset(self):
        return BlocAccueil.objects.all()


class BlocAccueilAdminDetailAPIView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /api/admin-general/accueil/<type>/?type= — un bloc.

    PATCH partiel : titre, contenu (JSON validé par type), image
    (multipart), actif, ordre. Le type d'un bloc est immuable.
    """

    permission_classes = (permissions.IsAuthenticated, EstAdminGeneral)
    serializer_class = BlocAccueilAdminSerializer

    def get_object(self):
        return generics.get_object_or_404(
            BlocAccueil.objects.all(), type=self.kwargs["type_bloc"],
        )

    def perform_update(self, serializer):
        import sys

        print(
            f"[DEBUG-ACCUEIL] content_type={self.request.content_type} "
            f"POST_keys={sorted(self.request.POST.keys())} "
            f"FILES_keys={list(self.request.FILES.keys())}",
            file=sys.stderr,
            flush=True,
        )
        avant = _snapshot_bloc(self.get_object())
        bloc = serializer.save()
        apres = _snapshot_bloc(bloc)
        if avant != apres:
            journaliser(
                self.request.user,
                JournalAudit.Action.MODIFICATION_ACCUEIL,
                "BlocAccueil",
                bloc.type,
                avant=avant,
                apres=apres,
                requete=self.request,
            )


class BlocAccueilOrdreAPIView(generics.GenericAPIView):
    """POST /api/admin-general/accueil/ordre/ — ordre, activation, publication.

    Corps : {"blocs": [{"type": "HERO", "actif": true, "ordre": 0}, …],
             "publier": false}

    - `blocs` met à jour ordre + actif de chaque bloc ;
    - `publier: true` positionne `date_publication` sur tous les blocs
      actifs (passage brouillon → public, horodaté et tracé).
    """

    permission_classes = (permissions.IsAuthenticated, EstAdminGeneral)
    serializer_class = BlocAccueilOrdreSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        donnees = serializer.validated_data

        if donnees.get("blocs"):
            for element in donnees["blocs"]:
                BlocAccueil.objects.filter(type=element["type"]).update(
                    ordre=element["ordre"],
                    actif=element["actif"],
                )

        if donnees.get("publier"):
            BlocAccueil.objects.filter(actif=True).update(
                date_publication=timezone.now(),
            )

        journaliser(
            self.request.user,
            JournalAudit.Action.MODIFICATION_ACCUEIL,
            "BlocAccueil",
            "page-accueil",
            apres={
                "blocs": donnees.get("blocs", []),
                "publier": donnees.get("publier", False),
            },
            requete=self.request,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)