from django.urls import path

from sgi.views import SGIFicheAPIView, SGIListAPIView
from sgi.views_admin import ConventionTarifaireAdminAPIView, PresentationAdminAPIView

app_name = "sgi"

urlpatterns = [
    path("", SGIListAPIView.as_view(), name="sgi-list"),
    path("<uuid:pk>/", SGIFicheAPIView.as_view(), name="sgi-fiche"),
    path("admin/convention/", ConventionTarifaireAdminAPIView.as_view(), name="admin-convention"),
    path("admin/presentation/", PresentationAdminAPIView.as_view(), name="admin-presentation"),
]