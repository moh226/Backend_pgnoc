"""Tâches Celery asynchrones pour les notifications et emails.

Ces tâches remplacent les appels synchrones de `notifier_transition`
et `notifier_commentaire_agent`. En mode test (`CELERY_TASK_ALWAYS_EAGER`),
elles s'exécutent dans le processus Django sans broker.
"""

import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags

from comptes.models import Role, Utilisateur
from notifications.models import Notification

logger = logging.getLogger("pgnoc.notifications")


@shared_task(ignore_result=True)
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


@shared_task(ignore_result=True)
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


@shared_task(ignore_result=True, autoretry_for=(Exception,), retry_backoff=60, max_retries=3)
def envoyer_email_inscription(user_pk):
    """Envoie un email de confirmation d'inscription (UC02, §4.2).

    L'email est envoyé en arrière-plan via Celery/Redis pour ne pas
    bloquer la requête d'inscription.  En mode eager (tests), la tâche
    s'exécute dans le processus Django sans broker.

    Le HTML passe obligatoirement par le template ``emails/inscription.html``
    (auto-échappé par Django) : le prénom étant une donnée saisie par
    l'utilisateur, il ne doit jamais être interpolé directement dans le
    HTML (risque d'injection de scripts/liens dans la boîte mail).
    """
    try:
        user = Utilisateur.objects.get(pk=user_pk)
    except Utilisateur.DoesNotExist:
        logger.warning("Utilisateur %s introuvable — email inscription ignoré.", user_pk)
        return

    _envoyer_email(
        "emails/inscription.html",
        "Bienvenue sur PGNOC-TI — Confirmation de votre compte",
        [user.email],
        {
            "prenom": user.prenom,
            "email": user.email,
            "url_connexion": f"{settings.FRONTEND_URL}/login",
            "annee": timezone.now().year,
        },
    )


# ─────────────────────────────────────────────────────────────
# Emails transactionnels — transitions de dossier
# ─────────────────────────────────────────────────────────────


def _envoyer_email(template, sujet, destinataires, contexte):
    """Helper : rend un template HTML et envoie l'email.

    `annee` (copyright du socle `base.html`) est injecté automatiquement
    — toute omission produisait un pied de page « ©  PGNOC-TI ».

    Ne capture PAS les exceptions : les tâches appelantes sont
    auto-retry (3 tentatives, backoff 60 s via `autoretry_for`) — le
    `max_retries` jusqu'ici décoratif devient effectif, et l'échec
    final est rapporté par le worker Celery.
    """
    contexte_complet = {"annee": timezone.now().year, **contexte}
    html = render_to_string(template, contexte_complet)
    text = strip_tags(html)
    send_mail(
        subject=sujet,
        message=text,
        from_email=None,
        recipient_list=destinataires,
        html_message=html,
        fail_silently=False,
    )
    logger.info("Email '%s' envoyé à %d destinataire(s).", sujet, len(destinataires))


@shared_task(ignore_result=True, autoretry_for=(Exception,), retry_backoff=60, max_retries=3)
def envoyer_email_dossier_soumis(dossier_pk):
    """Email aux agents/admin SGI quand un dossier est soumis."""
    from dossiers.models import Dossier

    try:
        dossier = Dossier.objects.select_related("utilisateur", "sgi").get(pk=dossier_pk)
    except Dossier.DoesNotExist:
        return

    cibles = list(
        Utilisateur.objects.filter(
            sgi_id=dossier.sgi_id,
            role__code__in=(Role.Code.AGENT_SGI, Role.Code.ADMIN_SGI),
            is_active=True,
        ).values_list("email", flat=True)
    )
    if not cibles:
        return

    _envoyer_email(
        "emails/dossier_soumis.html",
        f"Nouveau dossier soumis — {dossier.reference}",
        cibles,
        {
            "reference": dossier.reference,
            "investisseur_email": dossier.utilisateur.email,
            "sgi_nom": dossier.sgi.nom,
            "date_soumission": dossier.date_soumission.strftime("%d/%m/%Y %H:%M") if dossier.date_soumission else "",
        },
    )


@shared_task(ignore_result=True, autoretry_for=(Exception,), retry_backoff=60, max_retries=3)
def envoyer_email_dossier_valide(dossier_pk):
    """Email à l'investisseur quand son dossier est validé."""
    from dossiers.models import Dossier

    try:
        dossier = Dossier.objects.select_related("utilisateur", "sgi").get(pk=dossier_pk)
    except Dossier.DoesNotExist:
        return

    _envoyer_email(
        "emails/dossier_valide.html",
        f"Dossier validé — {dossier.reference}",
        [dossier.utilisateur.email],
        {
            "prenom": dossier.utilisateur.prenom,
            "email": dossier.utilisateur.email,
            "reference": dossier.reference,
            "sgi_nom": dossier.sgi.nom,
        },
    )


@shared_task(ignore_result=True, autoretry_for=(Exception,), retry_backoff=60, max_retries=3)
def envoyer_email_dossier_rejete(dossier_pk):
    """Email à l'investisseur quand son dossier est rejeté."""
    from dossiers.models import Dossier

    try:
        dossier = Dossier.objects.select_related("utilisateur", "sgi").get(pk=dossier_pk)
    except Dossier.DoesNotExist:
        return

    _envoyer_email(
        "emails/dossier_rejete.html",
        f"Dossier rejeté — {dossier.reference}",
        [dossier.utilisateur.email],
        {
            "prenom": dossier.utilisateur.prenom,
            "email": dossier.utilisateur.email,
            "reference": dossier.reference,
            "sgi_nom": dossier.sgi.nom,
            "motif_rejet": dossier.motif_rejet or "",
            "url_dossier": f"{settings.FRONTEND_URL}/espace-investisseur/dossiers/{dossier.pk}",
        },
    )


@shared_task(ignore_result=True, autoretry_for=(Exception,), retry_backoff=60, max_retries=3)
def envoyer_email_demande_correction(dossier_pk, valeur_pk):
    """Email à l'investisseur quand un agent demande une correction."""
    from dossiers.models import Dossier, ValeurChamp

    try:
        dossier = Dossier.objects.select_related("utilisateur", "sgi").get(pk=dossier_pk)
        valeur = ValeurChamp.objects.select_related("champ").get(pk=valeur_pk)
    except (Dossier.DoesNotExist, ValeurChamp.DoesNotExist):
        return

    _envoyer_email(
        "emails/demande_correction.html",
        f"Correction demandée — {dossier.reference}",
        [dossier.utilisateur.email],
        {
            "prenom": dossier.utilisateur.prenom,
            "email": dossier.utilisateur.email,
            "reference": dossier.reference,
            "sgi_nom": dossier.sgi.nom,
            "champ_nom": valeur.champ.nom,
            "motif": valeur.commentaire_agent or "",
            "url_dossier": f"{settings.FRONTEND_URL}/espace-investisseur/dossiers/{dossier.pk}",
        },
    )


@shared_task(ignore_result=True, autoretry_for=(Exception,), retry_backoff=60, max_retries=3)
def envoyer_email_code_otp(dossier_pk, code):
    """Achemine le code OTP de signature par email (canal hors-bande).

    UC17 : en production, le code n'est JAMAIS renvoyé dans la réponse
    API — c'est cet email qui l'achemine à l'investisseur. Le code est
    envoyé une seule fois ; les re-dispatches Celery sont tolérés (le
    hash OTP a pu être purgé entre-temps, l'email est alors ignoré).
    """
    from dossiers.models import Dossier

    try:
        dossier = Dossier.objects.select_related("utilisateur").get(pk=dossier_pk)
    except Dossier.DoesNotExist:
        return

    # Ne pas expédier un code déjà purgé/expiré (re-dispatch tardif).
    if not dossier.otp_expiration or timezone.now() > dossier.otp_expiration:
        logger.warning("OTP du dossier %s expiré avant envoi — email ignoré.", dossier_pk)
        return

    _envoyer_email(
        "emails/code_otp.html",
        f"Votre code de signature — {dossier.reference}",
        [dossier.utilisateur.email],
        {
            "prenom": dossier.utilisateur.prenom,
            "email": dossier.utilisateur.email,
            "reference": dossier.reference,
            "code": code,
            "minutes_validite": 5,
            "annee": timezone.now().year,
        },
    )
