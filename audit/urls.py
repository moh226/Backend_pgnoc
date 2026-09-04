"""Routes de lecture du journal d'audit (Admin Général uniquement)."""

from django.urls import path

from audit.views import (
    JournalAuditExportAPIView,
    JournalAuditExportPDFAPIView,
    JournalAuditListAPIView,
)

app_name = "audit"

urlpatterns = [
    path("journal/", JournalAuditListAPIView.as_view(), name="journal-list"),
    path("journal/export/", JournalAuditExportAPIView.as_view(), name="journal-export"),
    path("journal/export-pdf/", JournalAuditExportPDFAPIView.as_view(), name="journal-export-pdf"),
]