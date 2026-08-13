"""Génération automatique des notifications liées au cycle de vie (§7).

Appelée depuis `dossiers.workflow.transiter()` après chaque transition
réussie. Ni les mutations ni les erreurs de notification ne doivent
perturber l'action métier : toute la génération est best-effort et
tout échec est journalisé sur le logger `pgnoc.notifications`.
"""

import logging

from django.db import transaction

from comptes.models import Role, Utilisateur
from notifications.models import Notification

logger = logging.getLogger("pgnoc.notifications")


def notifier_transition(dossier, nouveau_statut):
    """Enveloppe best-effort INTÉGRALE de la notification.

    La transition métier est déjà persistée quand on arrive ici : une
    alerte impossible à produire est un incident d'exploitation (donc
    journalisé), jamais une erreur 500 rendue à l'appelant. Le `try`
    couvre AUSSI la construction des messages (ex. `dossier.agent` à
    None sur une donnée incohérente), pas seulement l'écriture en base.
    """
    try:
        _notifier_transition(dossier, nouveau_statut)
    except Exception:
        logger.exception(
            "Échec de notification pour le dossier %s (statut %s)",
            getattr(dossier, "reference", dossier.pk),
            nouveau_statut,
        )


def _notifier_transition(dossier, nouveau_statut):
    """Crée les notifications destinataires de la transition `nouveau_statut`.

    - SOUMIS          → tous les agents/admins actifs de la SGI destinataire ;
    - EN_INSTRUCTION  → l'investisseur propriétaire ;
    - VALIDE / REJETE → l'investisseur propriétaire (avec le motif si rejet).
    """
    if nouveau_statut == dossier.Statut.SOUMIS:
        cibles = Utilisateur.objects.filter(
            sgi_id=dossier.sgi_id,
            role__code__in=(Role.Code.AGENT_SGI, Role.Code.ADMIN_SGI),
            is_active=True,
        )
        titre = "Nouveau dossier soumis"
        message = (
            f"Le dossier {dossier.reference} vient d'être soumis "
            f"par {dossier.utilisateur.get_full_name()}."
        )
    elif nouveau_statut == dossier.Statut.EN_INSTRUCTION:
        cibles = [dossier.utilisateur]
        titre = "Dossier en instruction"
        message = (
            f"Votre dossier {dossier.reference} est désormais en instruction "
            f"par {dossier.agent.get_full_name()}."
        )
    elif nouveau_statut == dossier.Statut.VALIDE:
        cibles = [dossier.utilisateur]
        titre = "Dossier validé"
        message = f"Félicitations, votre dossier {dossier.reference} a été validé."
    elif nouveau_statut == dossier.Statut.REJETE:
        cibles = [dossier.utilisateur]
        titre = "Dossier rejeté"
        message = (
            f"Votre dossier {dossier.reference} a été rejeté. "
            f"Motif : {dossier.motif_rejet} — corrigez les champs signalés "
            f"puis resoumettez-le."
        )
    else:
        return

    # Massif et atomique : une notification par destinataire.
    notifications = [
        Notification(
            utilisateur=cible,
            titre=titre,
            message=message,
            type_notif=Notification.TypeNotif.DOSSIER,
        )
        for cible in cibles
    ]
    if notifications:
        with transaction.atomic():
            Notification.objects.bulk_create(notifications)