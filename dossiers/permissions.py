"""Permissions spécifiques au domaine Dossiers.

Ne réutilise PAS `EstProprietaireOuPersonnelSGI` de comptes/permissions.py
pour l'accès objet à un Dossier : cette dernière autorise tout
personnel SGI sans vérifier son appartenance à LA SGI précise du
dossier — ce qui romprait le cloisonnement strict exigé par le
cahier des charges. `PeutAccederAuDossier` corrige ce point.
"""

from rest_framework import permissions


class PeutAccederAuDossier(permissions.BasePermission):
    """Permission d'objet pour Dossier, respectant le cloisonnement SGI strict."""

    message = "Vous n'avez pas accès à ce dossier."

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.est_admin_general:
            return True
        if user.est_admin_sgi or user.est_agent_sgi:
            return user.sgi_id is not None and obj.sgi_id == user.sgi_id
        if user.est_investisseur:
            return obj.utilisateur_id == user.id
        return False