"""Modèle Notification (section 6.2.5).

Alerts générées automatiquement à chaque transition de dossier
(section 7) : soumission → personnel SGI ; prise en charge, décision →
investisseur. INSERT ONLY côté API : aucune notification n'est créée,
modifiée ou supprimée depuis le client (seul `marquer lue` est permis).
L'envoi asynchrone par Celery/Redis est prévu en phase finale ; ici la
création est synchrone dans `notifications.services.notifier_transition`.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Notification(models.Model):
    """Une alerte destinée à un utilisateur précis."""

    class TypeNotif(models.TextChoices):
        DOSSIER = "DOSSIER", _("Dossier")
        SYSTEME = "SYSTEME", _("Système")

    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False,
        verbose_name=_("Identifiant"),
    )
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Destinataire"),
        related_name="notifications",
        on_delete=models.PROTECT,
    )
    titre = models.CharField(_("Titre"), max_length=255)
    message = models.TextField(_("Message"))
    lue = models.BooleanField(_("Lue"), default=False, db_index=True)
    type_notif = models.CharField(
        _("Type"), max_length=20, choices=TypeNotif.choices,
        default=TypeNotif.DOSSIER,
    )
    date_creation = models.DateTimeField(
        _("Date de création"), auto_now_add=True, db_index=True,
    )

    class Meta:
        verbose_name = _("Notification")
        verbose_name_plural = _("Notifications")
        ordering = ["-date_creation"]

    def __str__(self):
        return f"{self.titre} → {self.utilisateur.email}"