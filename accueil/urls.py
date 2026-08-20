"""Routes publiques de la page d'accueil."""

from django.urls import path

from accueil.views import AccueilPublicAPIView

app_name = "accueil"

urlpatterns = [
    path("", AccueilPublicAPIView.as_view(), name="public"),
]