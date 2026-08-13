"""Managers personnalisés pour le modèle Utilisateur."""

from django.contrib.auth.base_user import BaseUserManager
from django.db import models
from django.utils.translation import gettext_lazy as _


class UtilisateurQuerySet(models.QuerySet):
    """QuerySet exposant les règles de cloisonnement multi-tenant."""

    def avec_role(self):
        """Précharge le rôle systématiquement (évite le N+1 sur `est_*`)."""
        return self.select_related("role", "sgi")

    def visible_pour(self, utilisateur):
        """Retourne le sous-ensemble d'utilisateurs visibles par `utilisateur`."""
        base = self.avec_role()

        if utilisateur.est_admin_general:
            return base

        if utilisateur.est_admin_sgi or utilisateur.est_agent_sgi:
            if utilisateur.sgi_id is None:
                return base.none()
            return base.filter(sgi_id=utilisateur.sgi_id)

        return base.filter(pk=utilisateur.pk)


class UtilisateurManager(BaseUserManager):
    """Manager personnalisé pour le modèle Utilisateur basé sur l'email."""

    use_in_migrations = True

    def get_queryset(self):
        return UtilisateurQuerySet(self.model, using=self._db).avec_role()

    def visible_pour(self, utilisateur):
        return self.get_queryset().visible_pour(utilisateur)

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError(_("L'adresse email est obligatoire."))

        email = self.normalize_email(email)
        utilisateur = self.model(email=email, **extra_fields)
        utilisateur.set_password(password)
        utilisateur.full_clean(exclude=["password"])
        # La sauvegarde déclenche le signal `post_save` de comptes.signals
        # qui crée automatiquement le Profil* correspondant au rôle.
        utilisateur.save(using=self._db)
        return utilisateur


    def create_user(self, email, password=None, role=None, **extra_fields):
        """Crée un utilisateur standard.

        `role` accepte soit une instance `Role`, soit un code
        (ex: "INVESTISSEUR") pour la commodité des appels en shell
        et des fixtures de test.
        """
        from comptes.models import Role  # import local : évite un cycle

        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)

        if role is None:
            role = Role.objects.get(code=Role.Code.INVESTISSEUR)
        elif isinstance(role, str):
            role = Role.objects.get(code=role)

        return self._create_user(email, password, role=role, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        from comptes.models import Role

        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault(
            "role", Role.objects.get(code=Role.Code.ADMIN_GENERAL)
        )

        if extra_fields.get("is_staff") is not True:
            raise ValueError(_("Le superutilisateur doit avoir is_staff=True."))
        if extra_fields.get("is_superuser") is not True:
            raise ValueError(_("Le superutilisateur doit avoir is_superuser=True."))

        return self._create_user(email, password, **extra_fields)