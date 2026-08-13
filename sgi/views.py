from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions

from sgi.models import SGI
from sgi.serializers import SGIFicheSerializer, SGIPublicSerializer


class SGIListAPIView(generics.ListAPIView):
    """Liste les SGI actives, pour que l'investisseur en sélectionne une.

    GET /api/sgi/
    """

    queryset = SGI.objects.filter(est_active=True)
    serializer_class = SGIPublicSerializer
    permission_classes = (permissions.IsAuthenticated,)


class SGIFicheAPIView(generics.RetrieveAPIView):
    """Fiche d'adhésion d'une SGI : présentation + état de la convention (UC01).

    GET /api/sgi/<uuid:pk>/
    """

    queryset = SGI.objects.filter(est_active=True)
    serializer_class = SGIFicheSerializer
    permission_classes = (permissions.IsAuthenticated,)