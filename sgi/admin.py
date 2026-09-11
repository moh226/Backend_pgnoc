from django.contrib import admin
from sgi.models import ConfigDepotMinimum, SGI


@admin.register(SGI)
class SGIAdmin(admin.ModelAdmin):
    list_display = ("nom", "code_sgi", "est_active", "date_creation")
    list_filter = ("est_active",)
    search_fields = ("nom", "code_sgi")
    readonly_fields = ("id", "date_creation")


@admin.register(ConfigDepotMinimum)
class ConfigDepotMinimumAdmin(admin.ModelAdmin):
    list_display = ("sgi", "exige_depot", "montant_depot_min", "date_modification")
    list_filter = ("exige_depot",)
    search_fields = ("sgi__nom",)
    readonly_fields = ("id", "date_modification")
