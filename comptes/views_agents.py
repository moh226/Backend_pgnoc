"""Gestion des comptes Agent SGI par l'Admin SGI (UC18).

Règles portées par ces vues :
  - réservé à un Admin SGI (jamais à un agent, un investisseur ou un
    admin général) ;
  - cloisonnement strict : l'admin ne voit et ne manipule QUE les
    agents de SA SGI (`get_queryset` filtré par `sgi_id`) ;
  - jamais de suppression destructive : la désactivation passe par
    `is_active=False` (le compte reste traçable, l'authentification
    est refusée par SimpleJWT/Django).
"""

from rest_framework import generics, permissions

from comptes.models import Role, Utilisateur
from comptes.permissions import EstAdminSGI
from comptes.serializers import AgentSerializer


class AgentListCreateAPIView(generics.ListCreateAPIView):
    """Liste (cloisonnée) et création d'agents par l'Admin SGI.

    GET  /api/comptes/agents/
    POST /api/comptes/agents/
    """

    serializer_class = AgentSerializer
    permission_classes = (permissions.IsAuthenticated, EstAdminSGI)

    def get_queryset(self):
        return Utilisateur.objects.filter(
            role__code=Role.Code.AGENT_SGI,
            sgi_id=self.request.user.sgi_id,
        ).select_related("profil_agent_sgi")


class AgentRetrieveUpdateAPIView(generics.RetrieveUpdateAPIView):
    """Détail / mise à jour (bascule active) d'un agent de SA SGI.

    GET   /api/comptes/agents/<id>/
    PATCH /api/comptes/agents/<id>/   (ex: {"is_active": false})

    Pas de DELETE : on n'écrase jamais un compte, on le désactive.
    """

    serializer_class = AgentSerializer
    permission_classes = (permissions.IsAuthenticated, EstAdminSGI)

    def get_queryset(self):
        return Utilisateur.objects.filter(
            role__code=Role.Code.AGENT_SGI,
            sgi_id=self.request.user.sgi_id,
        ).select_related("profil_agent_sgi")