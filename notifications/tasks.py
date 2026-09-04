"""Tâches Celery asynchrones pour les notifications et emails.

Ces tâches remplacent les appels synchrones de `notifier_transition`
et `notifier_commentaire_agent`. En mode test (`CELERY_TASK_ALWAYS_EAGER`),
elles s'exécutent dans le processus Django sans broker.
"""

import logging

from celery import shared_task
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from comptes.models import Role, Utilisateur
from notifications.models import Notification

logger = logging.getLogger("pgnoc.notifications")


@shared_task(ignore_result=True, max_retries=3)
def notifier_transition_task(dossier_pk, nouveau_statut):
    """Crée les notifications liées à une transition de statut (best-effort).

    `dossier_pk` est passé (et non l'instance) pour éviter les problèmes
    de sérialisation Django ORM dans le broker Redis.
    """
    from dossiers.models import Dossier

    try:
        dossier = Dossier.objects.select_related("utilisateur", "agent").get(pk=dossier_pk)
    except Dossier.DoesNotExist:
        logger.warning("Dossier %s introuvable — notification ignorée.", dossier_pk)
        return

    try:
        _creer_notifications_transition(dossier, nouveau_statut)
    except Exception:
        logger.exception(
            "Échec de notification pour le dossier %s (statut %s)",
            dossier.reference,
            nouveau_statut,
        )


def _creer_notifications_transition(dossier, nouveau_statut):
    """Logique métier de création des notifications (même corps que l'ancien `_notifier_transition`)."""
    from dossiers.models import Dossier as _Dossier

    if nouveau_statut == _Dossier.Statut.SOUMIS:
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
    elif nouveau_statut == _Dossier.Statut.EN_INSTRUCTION:
        cibles = [dossier.utilisateur]
        titre = "Dossier en instruction"
        agent_nom = dossier.agent.get_full_name() if dossier.agent else "un agent"
        message = (
            f"Votre dossier {dossier.reference} est désormais en instruction "
            f"par {agent_nom}."
        )
    elif nouveau_statut == _Dossier.Statut.VALIDE:
        cibles = [dossier.utilisateur]
        titre = "Dossier validé"
        message = f"Félicitations, votre dossier {dossier.reference} a été validé."
    elif nouveau_statut == _Dossier.Statut.REJETE:
        cibles = [dossier.utilisateur]
        titre = "Dossier rejeté"
        message = (
            f"Votre dossier {dossier.reference} a été rejeté. "
            f"Motif : {dossier.motif_rejet} — corrigez les champs signalés "
            f"puis resoumettez-le."
        )
    else:
        return

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


@shared_task(ignore_result=True, max_retries=3)
def notifier_commentaire_agent_task(dossier_pk, valeur_pk):
    """Notifie l'investisseur d'une demande de correction (UC09) — asynchrone."""
    from dossiers.models import Dossier, ValeurChamp

    try:
        dossier = Dossier.objects.select_related("utilisateur").get(pk=dossier_pk)
        valeur = ValeurChamp.objects.select_related("champ").get(pk=valeur_pk)
    except (Dossier.DoesNotExist, ValeurChamp.DoesNotExist) as exc:
        logger.warning("Dossier/valeur introuvable — notification ignorée : %s", exc)
        return

    try:
        Notification.objects.create(
            utilisateur=dossier.utilisateur,
            titre="Correction demandée sur votre dossier",
            message=(
                f"Votre dossier {dossier.reference} : « "
                f"{valeur.champ.nom} » demande une correction. "
                f"Motif : {valeur.commentaire_agent}"
            ),
            type_notif=Notification.TypeNotif.DOSSIER,
        )
    except Exception:
        logger.exception(
            "Échec de notification de correction pour le dossier %s",
            dossier.reference,
        )


# ─────────────────────────────────────────────────────────────
# Emails transactionnels (UC02 — inscription)
# ─────────────────────────────────────────────────────────────


@shared_task(ignore_result=True, max_retries=3)
def envoyer_email_inscription(user_pk):
    """Envoie un email de confirmation d'inscription (UC02, §4.2).

    L'email est envoyé en arrière-plan via Celery/Redis pour ne pas
    bloquer la requête d'inscription.  En mode eager (tests), la tâche
    s'exécute dans le processus Django sans broker.
    """
    try:
        user = Utilisateur.objects.get(pk=user_pk)
    except Utilisateur.DoesNotExist:
        logger.warning("Utilisateur %s introuvable — email inscription ignoré.", user_pk)
        return

    subject = "Bienvenue sur PGNOC-TI — Confirmation de votre compte"
    message = (
        f"Bonjour {user.prenom or user.email},\n\n"
        f"Votre compte a été créé avec succès sur la plateforme PGNOC-TI.\n"
        f"Vous pouvez désormais vous connecter et constituer votre dossier KYC.\n\n"
        f"Cordialement,\nL'équipe PGNOC-TI"
    )
    html_message = (
        f"<p>Bonjour <strong>{user.prenom or user.email}</strong>,</p>"
        f"<p>Votre compte a été créé avec succès sur la plateforme <strong>PGNOC-TI</strong>.</p>"
        f"<p>Vous pouvez désormais vous connecter et constituer votre dossier KYC.</p>"
        f"<p>Cordialement,<br>L'équipe PGNOC-TI</p>"
    )

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=None,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )
        logger.info("Email d'inscription envoyé à %s.", user.email)
    except Exception:
        logger.exception("Échec envoi email inscription à %s", user.email)
