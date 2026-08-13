"""Modèle Utilisateur personnalisé pour PGNOC-TI."""

import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from comptes.managers import UtilisateurManager


class Role(models.Model):
    """Rôle RBAC de la plateforme, en modèle séparé (composition).

    Choisi plutôt qu'un simple CharField à choix figés pour permettre,
    à terme, une gestion des rôles en base (description, éventuels
    droits additionnels) sans migration de schéma. Les 4 codes restent
    néanmoins fermés ('Code.choices') : on ne veut pas qu'un rôle
    arbitraire soit créé en base sans revue de code, le RBAC reste
    piloté par le code applicatif, pas par une table librement éditable.
    """

    class Code(models.TextChoices):
        INVESTISSEUR = "INVESTISSEUR", _("Investisseur")
        AGENT_SGI = "AGENT_SGI", _("Agent SGI")
        ADMIN_SGI = "ADMIN_SGI", _("Admin SGI")
        ADMIN_GENERAL = "ADMIN_GENERAL", _("Admin Général")

    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False,
        verbose_name=_("Identifiant"),
    )
    code = models.CharField(
        _("Code"), max_length=20, choices=Code.choices, unique=True, db_index=True,
    )
    libelle = models.CharField(_("Libellé"), max_length=100)
    description = models.TextField(_("Description"), blank=True)

    class Meta:
        verbose_name = _("Rôle")
        verbose_name_plural = _("Rôles")
        ordering = ["code"]

    def __str__(self):
        return self.libelle


class Utilisateur(AbstractBaseUser, PermissionsMixin):
    """

    Le champ métier spécifique à chaque type d'acteur (Investisseur,
    Agent SGI, Admin SGI, Admin Général) ne vit PAS ici : il est
    porté par un modèle Profil* dédié en relation OneToOne (Étape
    2.0.b), pour garder ce modèle central léger et stable.
    """

    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False,
        verbose_name=_("Identifiant"),
    )
    email = models.EmailField(
        _("Adresse email"), unique=True, db_index=True,
        error_messages={"unique": _("Un utilisateur avec cette adresse email existe déjà.")},
    )
    prenom = models.CharField(_("Prénom"), max_length=150, blank=True)
    nom = models.CharField(_("Nom"), max_length=150, blank=True)
    role = models.ForeignKey(
        Role,
        verbose_name=_("Rôle"),
        related_name="utilisateurs",
        on_delete=models.PROTECT,
        help_text=_("Détermine les permissions RBAC de l'utilisateur."),
    )
    sgi = models.ForeignKey(
        "sgi.SGI",
        verbose_name=_("SGI de rattachement"),
        related_name="utilisateurs",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text=_(
            "Obligatoire pour les rôles AGENT_SGI et ADMIN_SGI. "
            "Toujours NULL pour INVESTISSEUR et ADMIN_GENERAL."
        ),
    )
    is_active = models.BooleanField(_("Actif"), default=True)
    is_staff = models.BooleanField(_("Statut équipe"), default=False)
    date_joined = models.DateTimeField(_("Date d'inscription"), auto_now_add=True)
    date_maj = models.DateTimeField(_("Dernière modification"), auto_now=True)

    objects = UtilisateurManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = _("Utilisateur")
        verbose_name_plural = _("Utilisateurs")
        ordering = ["-date_joined"]
        # Note : la règle de cohérence rôle/SGI est portée par
        # `Utilisateur.clean()` ET par un trigger PostgreSQL
        # (migration 0007) : une CheckConstraint ORM ne peut pas référencer
        # la table Role (E041), le trigger l'établit au niveau base.

    def __str__(self):
        return f"{self.email} ({self.role.libelle})"

    def get_full_name(self):
        full_name = f"{self.prenom} {self.nom}".strip()
        return full_name or self.email

    def get_short_name(self):
        return self.prenom or self.email

    def clean(self):
        """Valide la cohérence rôle / SGI avant sauvegarde."""
        roles_necessitant_sgi = (Role.Code.AGENT_SGI, Role.Code.ADMIN_SGI)
        role_code = self.role.code if self.role_id else None

        if role_code in roles_necessitant_sgi and self.sgi_id is None:
            raise ValidationError({"sgi": _("Une SGI est obligatoire pour ce rôle.")})
        if role_code not in roles_necessitant_sgi and self.sgi_id is not None:
            raise ValidationError({"sgi": _("Ce rôle ne doit pas être rattaché à une SGI.")})

    @property
    def est_investisseur(self):
        return self.role.code == Role.Code.INVESTISSEUR

    @property
    def est_agent_sgi(self):
        return self.role.code == Role.Code.AGENT_SGI

    @property
    def est_admin_sgi(self):
        return self.role.code == Role.Code.ADMIN_SGI

    @property
    def est_admin_general(self):
        return self.role.code == Role.Code.ADMIN_GENERAL


class ProfilInvestisseur(models.Model):
    """Données propres à un utilisateur au rôle INVESTISSEUR.

    Existe uniquement si `utilisateur.role.code == Role.Code.INVESTISSEUR`
    (contrainte imposée par validation applicative, pas par la base —
    voir `clean()` ci-dessous).
    """

    class TypePersonne(models.TextChoices):
        PHYSIQUE = "PHYSIQUE", _("Personne physique")
        MORALE = "MORALE", _("Personne morale")
        MINEUR = "MINEUR", _("Mineur")

    utilisateur = models.OneToOneField(
        Utilisateur,
        verbose_name=_("Utilisateur"),
        related_name="profil_investisseur",
        on_delete=models.CASCADE,
        primary_key=True,
    )
    type_personne = models.CharField(
        _("Type de personne"), max_length=20, choices=TypePersonne.choices,
        default=TypePersonne.PHYSIQUE,
    )
    google_id = models.CharField(
        _("Identifiant Google"), max_length=255, blank=True, null=True, unique=True,
        help_text=_("Renseigné uniquement si l'inscription s'est faite via OAuth Google."),
    )

    class Meta:
        verbose_name = _("Profil Investisseur")
        verbose_name_plural = _("Profils Investisseur")

    def clean(self):
        if not self.utilisateur.est_investisseur:
            raise ValidationError(
                _("Ce profil ne peut être associé qu'à un utilisateur au rôle INVESTISSEUR.")
            )

    def __str__(self):
        return f"Profil Investisseur — {self.utilisateur.email}"


class ProfilAgentSGI(models.Model):
    """Données propres à un utilisateur au rôle AGENT_SGI."""

    utilisateur = models.OneToOneField(
        Utilisateur,
        verbose_name=_("Utilisateur"),
        related_name="profil_agent_sgi",
        on_delete=models.CASCADE,
        primary_key=True,
    )
    matricule = models.CharField(
        _("Matricule interne"), max_length=50, blank=True,
        help_text=_("Identifiant interne attribué par la SGI (facultatif)."),
    )

    class Meta:
        verbose_name = _("Profil Agent SGI")
        verbose_name_plural = _("Profils Agent SGI")

    def clean(self):
        if not self.utilisateur.est_agent_sgi:
            raise ValidationError(
                _("Ce profil ne peut être associé qu'à un utilisateur au rôle AGENT_SGI.")
            )

    def __str__(self):
        return f"Profil Agent SGI — {self.utilisateur.email}"


class ProfilAdminSGI(models.Model):
    """Données propres à un utilisateur au rôle ADMIN_SGI."""

    utilisateur = models.OneToOneField(
        Utilisateur,
        verbose_name=_("Utilisateur"),
        related_name="profil_admin_sgi",
        on_delete=models.CASCADE,
        primary_key=True,
    )
    fonction = models.CharField(
        _("Fonction"), max_length=100, blank=True,
        help_text=_("Ex: Responsable Conformité, Directeur des Opérations."),
    )

    class Meta:
        verbose_name = _("Profil Admin SGI")
        verbose_name_plural = _("Profils Admin SGI")

    def clean(self):
        if not self.utilisateur.est_admin_sgi:
            raise ValidationError(
                _("Ce profil ne peut être associé qu'à un utilisateur au rôle ADMIN_SGI.")
            )

    def __str__(self):
        return f"Profil Admin SGI — {self.utilisateur.email}"


class ProfilAdminGeneral(models.Model):
    """Données propres à un utilisateur au rôle ADMIN_GENERAL."""

    utilisateur = models.OneToOneField(
        Utilisateur,
        verbose_name=_("Utilisateur"),
        related_name="profil_admin_general",
        on_delete=models.CASCADE,
        primary_key=True,
    )
    notes_internes = models.TextField(
        _("Notes internes"), blank=True,
        help_text=_("Notes de supervision, non visibles par les autres rôles."),
    )

    class Meta:
        verbose_name = _("Profil Admin Général")
        verbose_name_plural = _("Profils Admin Général")

    def clean(self):
        if not self.utilisateur.est_admin_general:
            raise ValidationError(
                _("Ce profil ne peut être associé qu'à un utilisateur au rôle ADMIN_GENERAL.")
            )

    def __str__(self):
        return f"Profil Admin Général — {self.utilisateur.email}"