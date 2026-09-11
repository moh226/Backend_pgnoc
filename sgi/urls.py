from django.urls import path

from sgi.views import SGIFicheAPIView, SGIListAPIView
from sgi.views_admin import (
    AdminSGIDashboardAPIView,
    ConfigDepotMinimumAdminAPIView,
    ConventionTarifaireAdminAPIView,
    PresentationAdminAPIView,
)

app_name = "sgi"

urlpatterns = [
    path("", SGIListAPIView.as_view(), name="sgi-list"),
    path("<uuid:pk>/", SGIFicheAPIView.as_view(), name="sgi-fiche"),
    path("admin/dashboard/", AdminSGIDashboardAPIView.as_view(), name="admin-dashboard"),
    path("admin/conventions/", ConventionTarifaireAdminAPIView.as_view(), name="admin-conventions"),
    path("admin/presentations/", PresentationAdminAPIView.as_view(), name="admin-presentations"),
    path("admin/depots/", ConfigDepotMinimumAdminAPIView.as_view(), name="admin-depots"),
]