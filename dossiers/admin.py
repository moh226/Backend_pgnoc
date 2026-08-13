"""Admin pour l'app dossiers."""

from django.contrib import admin

from dossiers.models import ChampKYC, EtapeKYC, Dossier, ValeurChamp


class ChampKYCInline(admin.TabularInline):
    model = ChampKYC
    fk_name = "etape"
    extra = 1
    fields = ("nom", "code", "type", "obligatoire", "ordre", "champ_parent", "valeur_declencheur")


@admin.register(EtapeKYC)
class EtapeKYCAdmin(admin.ModelAdmin):
    list_display = ("nom", "sgi", "ordre", "actif")
    list_filter = ("sgi", "actif")
    search_fields = ("nom",)
    readonly_fields = ("id", "date_creation")
    inlines = (ChampKYCInline,)


@admin.register(ChampKYC)
class ChampKYCAdmin(admin.ModelAdmin):
    list_display = ("nom", "etape", "type", "obligatoire", "ordre", "actif")
    list_filter = ("type", "obligatoire", "actif")
    search_fields = ("nom", "code")
    readonly_fields = ("id",)
    autocomplete_fields = ("champ_parent",)


@admin.register(Dossier)
class DossierAdmin(admin.ModelAdmin):
    list_display = ("reference", "utilisateur", "sgi", "statut", "progression_pct", "date_creation")
    list_filter = ("sgi", "statut")
    search_fields = ("reference", "utilisateur__email")
    readonly_fields = (
        "id", "reference", "date_creation", "date_soumission",
        "date_instruction", "date_decision",
    )
    autocomplete_fields = ("utilisateur", "sgi", "etape_courante", "agent")


@admin.register(ValeurChamp)
class ValeurChampAdmin(admin.ModelAdmin):
    list_display = ("dossier", "champ", "valeur_ou_fichier", "est_corrige", "date_maj")
    list_filter = ("est_corrige", "champ__type")
    search_fields = ("dossier__reference", "champ__nom")
    readonly_fields = ("id", "date_creation", "date_maj")
    autocomplete_fields = ("dossier", "champ")

    @admin.display(description="Valeur")
    def valeur_ou_fichier(self, obj):
        return obj.fichier or obj.valeur