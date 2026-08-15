"""Modèle JournalAudit — la « boîte noire » du système (section 6.2.5).

Règles de traçabilité (§8.3 — conformité CREPMF) :
  - INSERT ONLY : aucune écriture, mise à jour ou suppression exposée ;
    ce modèle n'a ni admin d'ajout, ni endpoint d'écriture. La seule
    voie de création est `audit.services.journaliser`.
  - Chaque trace porte l'état `avant`/`apres` en JSONB, l'adresse IP,
    le User-Agent et l'horodatage de l'action.
  - Lecture réservée à l'Admin Général (endpoint dédié, étape 3B).
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class JournalAuditQuerySet(models.QuerySet):
    """QuerySet verrouillé : aucune mise à jour ni suppression en masse."""

    def update(self, *args, **kwargs):
        raise ValueError(_("Une entrée du journal d'audit est immuable (INSERT ONLY)."))

    def delete(self, *args, **kwargs):
        raise ValueError(_("Une entrée du journal d'audit ne peut pas être supprimée."))


class JournalAudit(models.Model):
    """Une entrée du journal d'audit (aucune suppression possible)."""

    class Action(models.TextChoices):
        CONNEXION = "CONNEXION", _("Connexion")
        INSCRIPTION = "INSCRIPTION", _("Inscription")
        CREATION_DOSSIER = "CREATION_DOSSIER", _("Création de dossier")
        TRANSITION_DOSSIER = "TRANSITION_DOSSIER", _("Transition de statut de dossier")
        COMMENTAIRE_AGENT = "COMMENTAIRE_AGENT", _("Commentaire d'un agent")
        ACCEPTATION_CONVENTION = "ACCEPTATION_CONVENTION", _("Acceptation de la convention tarifaire")
        POSE_SIGNATURE = "POSE_SIGNATURE", _("Signature électronique posée")
        CREATION_AGENT = "CREATION_AGENT", _("Création d'un agent SGI")
        MODIFICATION_AGENT = "MODIFICATION_AGENT", _("Modification d'un agent SGI")
        MODIFICATION_CONVENTION = "MODIFICATION_CONVENTION", _("Publication de la convention tarifaire")
        MODIFICATION_PRESENTATION = "MODIFICATION_PRESENTATION", _("Publication de la présentation")
        CREATION_ETAPE_KYC = "CREATION_ETAPE_KYC", _("Création d'une étape KYC")
        MODIFICATION_ETAPE_KYC = "MODIFICATION_ETAPE_KYC", _("Modification d'une étape KYC")
        SUPPRESSION_ETAPE_KYC = "SUPPRESSION_ETAPE_KYC", _("Suppression d'une étape KYC")
        CREATION_CHAMP_KYC = "CREATION_CHAMP_KYC", _("Création d'un champ KYC")
        MODIFICATION_CHAMP_KYC = "MODIFICATION_CHAMP_KYC", _("Modification d'un champ KYC")
        SUPPRESSION_CHAMP_KYC = "SUPPRESSION_CHAMP_KYC", _("Suppression d'un champ KYC")
        CREATION_SGI = "CREATION_SGI", _("Création d'une SGI partenaire")
        MODIFICATION_SGI = "MODIFICATION_SGI", _("Modification d'une SGI partenaire")
        CREATION_UTILISATEUR = "CREATION_UTILISATEUR", _("Création d'un compte interne")
        MODIFICATION_UTILISATEUR = "MODIFICATION_UTILISATEUR", _("Modification d'un compte interne")

    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False,
        verbose_name=_("Identifiant"),
    )
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Utilisateur"),
        related_name="traces_audit",
        on_delete=models.PROTECT,
        null=True, blank=True,
        help_text=_(
            "Null seulement si l'action n'est pas imputable à un compte "
            "utilisateur identifié (ex: tentative de connexion échouée)."
        ),
    )
    action = models.CharField(
        _("Action"), max_length=30, choices=Action.choices, db_index=True,
    )
    entite_concernee = models.CharField(
        _("Entité concernée"), max_length=100,
        help_text=_("Nom de l'entité métier, ex: 'Dossier', 'Utilisateur', 'ValeurChamp'."),
    )
    entite_id = models.CharField(
        _("Identifiant de l'entité"), max_length=100, db_index=True,
    )
    avant = models.JSONField(
        _("État avant"), null=True, blank=True,
        help_text=_("État précédent de l'entité (JSONB dans le MLD)."),
    )
    apres = models.JSONField(
        _("État après"), null=True, blank=True,
        help_text=_("Nouvel état de l'entité (JSONB dans le MLD)."),
    )
    ip_address = models.GenericIPAddressField(
        _("Adresse IP"), null=True, blank=True,
    )
    user_agent = models.CharField(_("User-Agent"), max_length=500, blank=True)
    date_action = models.DateTimeField(
        _("Date de l'action"), auto_now_add=True, db_index=True,
    )

    class Meta:
        verbose_name = _("Entrée d'audit")
        verbose_name_plural = _("Journal d'audit")
        ordering = ["-date_action"]
        indexes = [
            models.Index(fields=["entite_concernee", "entite_id"]),
        ]

    def __str__(self):
        return f"{self.get_action_display()} — {self.entite_concernee} {self.entite_id}"

    # INSERT ONLY : on verrouille toute mise à jour/suppression d'une
    # instance déjà enregistrée, même programmatique (bien qu'aucune
    # opération de ce type ne soit exposée par l'application). Le
    # `id` UUID étant pré-affecté par `default=uuid.uuid4`, un save()
    # sans `force_insert` serait considéré par Django comme un UPDATE :
    # on le refuse donc ici.
    objects = JournalAuditQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if self.pk is not None and not kwargs.get("force_insert", False):
            raise ValueError(_("Une entrée du journal d'audit est immuable."))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(_("Une entrée du journal d'audit ne peut pas être supprimée."))