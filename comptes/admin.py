"""Configuration de l'interface Django Admin pour l'app comptes."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.translation import gettext_lazy as _

from comptes.models import (
    ProfilAdminGeneral, ProfilAdminSGI, ProfilAgentSGI, ProfilInvestisseur,
    Role, Utilisateur,
)


class ProfilInvestisseurInline(admin.StackedInline):
    model = ProfilInvestisseur
    can_delete = False


class ProfilAgentSGIInline(admin.StackedInline):
    model = ProfilAgentSGI
    can_delete = False


class ProfilAdminSGIInline(admin.StackedInline):
    model = ProfilAdminSGI
    can_delete = False


class ProfilAdminGeneralInline(admin.StackedInline):
    model = ProfilAdminGeneral
    can_delete = False



@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("libelle", "code")
    search_fields = ("code", "libelle")
    readonly_fields = ("id",)


@admin.register(Utilisateur)
class UtilisateurAdmin(UserAdmin):
    ordering = ("-date_joined",)
    list_display = ("email", "prenom", "nom", "role", "sgi", "is_active", "date_joined")
    list_filter = ("role", "sgi", "is_active", "is_staff")
    search_fields = ("email", "prenom", "nom")
    readonly_fields = ("id", "date_joined", "date_maj", "last_login")
    autocomplete_fields = ("role", "sgi")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Informations personnelles"), {"fields": ("prenom", "nom")}),
        (
            _("Rôle et permissions"),
            {
                "fields": ("role", "sgi", "is_active", "is_staff", "is_superuser", "groups", "user_permissions")
            },
        ),

        (
            _("Dates importantes"),
            {
                "fields": ("last_login", "date_joined", "date_maj")
            }
        ),
        (
            _("Identifiant"),
            {
                "fields": ("id",)
            }
        ),
    )

    add_fieldsets = (
        (None,
         {"classes": ("wide",),
                "fields": ("email", "role", "sgi", "password1", "password2")
          }
         ),
    )


    def get_inlines(self, request, obj=None):
        """Affiche uniquement l'inline Profil correspondant au rôle de l'utilisateur.

        Évite de montrer les 4 inlines vides simultanément dans
        l'admin, ce qui serait trompeur (suggérerait qu'un
        utilisateur peut avoir 4 profils à la fois).
        """
        if obj is None:
            return []
        mapping = {
            Role.Code.INVESTISSEUR: [ProfilInvestisseurInline],
            Role.Code.AGENT_SGI: [ProfilAgentSGIInline],
            Role.Code.ADMIN_SGI: [ProfilAdminSGIInline],
            Role.Code.ADMIN_GENERAL: [ProfilAdminGeneralInline],
        }
        return mapping.get(obj.role.code, [])