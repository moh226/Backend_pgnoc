"""Classes de permissions RBAC réutilisables."""

from rest_framework import permissions

from comptes.models import Role


class EstInvestisseur(permissions.BasePermission):
    message = "Cette action est réservée aux investisseurs."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.est_investisseur
        )


class EstAgentSGI(permissions.BasePermission):
    message = "Cette action est réservée aux agents SGI."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.est_agent_sgi
        )


class EstAdminSGI(permissions.BasePermission):
    message = "Cette action est réservée aux administrateurs SGI."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.est_admin_sgi
        )


class EstAdminGeneral(permissions.BasePermission):
    message = "Cette action est réservée aux administrateurs généraux."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.est_admin_general
        )


class EstPersonnelSGI(permissions.BasePermission):
    message = "Cette action est réservée au personnel SGI."

    ROLES_AUTORISES = (Role.Code.AGENT_SGI, Role.Code.ADMIN_SGI)

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role.code in self.ROLES_AUTORISES
        )


class EstProprietaireOuPersonnelSGI(permissions.BasePermission):
    """Permission d'objet : propriétaire du dossier OU personnel SGI."""

    def has_object_permission(self, request, view, obj):
        if request.user.est_investisseur:
            return obj.utilisateur_id == request.user.id
        return request.user.role.code in (
            Role.Code.AGENT_SGI, Role.Code.ADMIN_SGI, Role.Code.ADMIN_GENERAL,
        )


class MemeSGIQueRequete(permissions.BasePermission):
    message = "Vous n'avez pas accès aux données de cette SGI."

    def has_object_permission(self, request, view, obj):
        if request.user.est_admin_general:
            return True
        if not hasattr(obj, "sgi_id"):
            return False
        return obj.sgi_id == request.user.sgi_id