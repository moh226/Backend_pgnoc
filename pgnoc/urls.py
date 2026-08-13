"""
URL configuration for backend project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
"""
import logging

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.db import connection
from django.db.utils import OperationalError
from django.http import JsonResponse
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView

logger = logging.getLogger("pgnoc.health")


def healthz(request):
    """Point de santé pour l'orchestrateur (Docker, Kubernetes, sondes CI).

    Public et sans base si possible : c'est lui qui décide du redémarrage
    d'un conteneur. 200 quand la base répond, 503 sinon.
    """
    try:
        connection.ensure_connection()
    except OperationalError:
        logger.exception("Sonde de santé : base de données injoignable.")
        return JsonResponse(
            {"status": "erreur", "base_de_donnees": "injoignable"},
            status=503,
        )
    return JsonResponse({"status": "ok", "base_de_donnees": "ok"})


urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path('admin/', admin.site.urls),

    path("api/comptes/", include("comptes.urls")),
    path("api/sgi/", include("sgi.urls")),

    path("api/dossiers/", include("dossiers.urls")),

    path("api/audit/", include("audit.urls")),

    path("api/notifications/", include("notifications.urls")),

    path("api/admin-general/", include("administration.urls")),

    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),

    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),

    path(
        "api/redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
