from django.contrib import admin
from sgi.models import SGI


@admin.register(SGI)
class SGIAdmin(admin.ModelAdmin):
    list_display = ("nom", "code_sgi", "est_active", "date_creation")
    list_filter = ("est_active",)
    search_fields = ("nom", "code_sgi")
    readonly_fields = ("id", "date_creation")
