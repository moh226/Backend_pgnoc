"""Routes de l'espace Admin Général (étape 3F)."""

from django.urls import path

from administration.views import (
    DashboardAPIView,
    SGIListCreateAPIView,
    SGIRetrieveUpdateAPIView,
    UtilisateurListCreateAPIView,
    UtilisateurRetrieveUpdateAPIView,
)

app_name = "administration"

urlpatterns = [
    path("dashboard/", DashboardAPIView.as_view(), name="dashboard"),
    path("sgi/", SGIListCreateAPIView.as_view(), name="sgi-list-create"),
    path("sgi/<uuid:pk>/", SGIRetrieveUpdateAPIView.as_view(), name="sgi-detail"),
    path(
        "utilisateurs/",
        UtilisateurListCreateAPIView.as_view(),
        name="utilisateurs-list-create",
    ),
    path(
        "utilisateurs/<uuid:pk>/",
        UtilisateurRetrieveUpdateAPIView.as_view(),
        name="utilisateurs-detail",
    ),
]