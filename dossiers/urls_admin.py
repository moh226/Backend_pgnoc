"""Routes URL admin KYC (montées sous /api/v1/admin/kyc/)."""

from django.urls import path

from dossiers.views_kyc_admin import (
    ChampKYCListCreateAPIView,
    ChampKYCRetrieveUpdateDestroyAPIView,
    EtapeKYCListCreateAPIView,
    EtapeKYCRetrieveUpdateDestroyAPIView,
)

app_name = "dossiers_admin"

urlpatterns = [
    path("etapes/", EtapeKYCListCreateAPIView.as_view(), name="etapes"),
    path("etapes/<uuid:pk>/", EtapeKYCRetrieveUpdateDestroyAPIView.as_view(), name="etape"),
    path("champs/", ChampKYCListCreateAPIView.as_view(), name="champs"),
    path("champs/<uuid:pk>/", ChampKYCRetrieveUpdateDestroyAPIView.as_view(), name="champ"),
]