"""Routes des notifications (destinataire connecté uniquement)."""

from django.urls import path

from notifications.views import (
    NotificationListAPIView, NotificationMarquerLueAPIView, NotificationNonLuesAPIView,
)

app_name = "notifications"

urlpatterns = [
    path("", NotificationListAPIView.as_view(), name="liste"),
    path("non-lues/", NotificationNonLuesAPIView.as_view(), name="non-lues"),
    path(
        "<uuid:pk>/marquer-lue/",
        NotificationMarquerLueAPIView.as_view(),
        name="marquer-lue",
    ),
]