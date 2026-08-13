"""Admin Django du journal d'audit : consultation en lecture seule.

Aucune action d'ajout/modification/suppression : l'admin est fourni
pour la supervision (CHEPMF exige la non-suppression, INSERT ONLY).
"""

from django.contrib import admin

from audit.models import JournalAudit


@admin.register(JournalAudit)
class JournalAuditAdmin(admin.ModelAdmin):
    list_display = ("date_action", "utilisateur", "action", "entite_concernee", "entite_id", "ip_address")
    list_filter = ("action", "date_action")
    search_fields = ("entite_id", "utilisateur__email")
    date_hierarchy = "date_action"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False