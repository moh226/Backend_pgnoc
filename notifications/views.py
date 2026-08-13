"""Endpoints de lecture des notifications du destinataire connecté.

L'accès est systématiquement cloxé à `request.user` : un utilisateur ne
peut jamais voir ni marquer les notifications d'autrui. La création est
réservée au service métier (`notifications.services.notifier_transition`).
"""

from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view, inline_serializer
from rest_framework import generics, permissions, serializers
from rest_framework.response import Response

from notifications.models import Notification
from notifications.serializers import NotificationSerializer


def _parse_bool(valeur):
    return str(valeur).lower() in ("1", "true", "oui", "yes")


@extend_schema_view(
    get=extend_schema(
        parameters=[
            OpenApiParameter(
                "non_lues", OpenApiTypes.STR, OpenApiParameter.QUERY,
                description="`true` pour ne garder que les non lues.",
            ),
        ],
    ),
)
class NotificationListAPIView(generics.ListAPIView):
    """Liste paginée de mes notifications (filtrées par `non_lues=true`)."""

    serializer_class = NotificationSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_queryset(self):
        qs = Notification.objects.filter(utilisateur=self.request.user)
        if _parse_bool(self.request.query_params.get("non_lues", "")):
            qs = qs.filter(lue=False)
        return qs


class NotificationNonLuesAPIView(generics.GenericAPIView):
    """Nombre de notifications non lues (badge du frontend)."""

    serializer_class = serializers.Serializer
    permission_classes = (permissions.IsAuthenticated,)

    @extend_schema(
        responses={200: inline_serializer(
            "CompteNonLues",
            {"compte": serializers.IntegerField()},
        )},
    )
    def get(self, request):
        compte = Notification.objects.filter(
            utilisateur=request.user, lue=False,
        ).count()
        return Response({"compte": compte})


class NotificationMarquerLueAPIView(generics.GenericAPIView):
    """Marque une notification comme lue (destinataire uniquement).

    POST /api/notifications/<id>/marquer-lue/
    """

    serializer_class = NotificationSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, pk):
        notification = get_object_or_404(
            Notification, pk=pk, utilisateur=request.user,
        )
        if not notification.lue:
            notification.lue = True
            notification.save(update_fields=["lue"])
        return Response(self.get_serializer(notification).data)