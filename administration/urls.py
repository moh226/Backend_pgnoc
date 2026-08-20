"""Routes de l'espace Admin Général (étape 3F)."""

from django.urls import path

from accueil.views import (
    BlocAccueilAdminDetailAPIView,
    BlocAccueilAdminListAPIView,
    BlocAccueilOrdreAPIView,
)
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
    path("accueil/", BlocAccueilAdminListAPIView.as_view(), name="accueil-list"),
    path(
        "accueil/ordre/",
        BlocAccueilOrdreAPIView.as_view(),
        name="accueil-ordre",
    ),
    path(
        "accueil/<str:type_bloc>/",
        BlocAccueilAdminDetailAPIView.as_view(),
        name="accueil-detail",
    ),
]