"""Service d'enregistrement du journal d'audit.

Point d'accès unique à la création de traces (`INSERT ONLY`) : aucun
autre module n'instancie `JournalAudit` directement. Ce service est
appelé par le workflow des dossiers, les vues d'authentification, et
toute action sensible à venir.
"""

import logging

from django.conf import settings

from audit.models import JournalAudit

logger = logging.getLogger("pgnoc.audit")


def journaliser(
    utilisateur,
    action,
    entite_concernee,
    entite_id,
    avant=None,
    apres=None,
    requete=None,
):
    """Crée une entrée d'audit avec les métadonnées légales (IP, User-Agent).

    Paramètres :
      - `utilisateur` : instance Utilisateur (ou None si inconnu — les
        tentatives échouées sont tracées sans imputabilité).
      - `action` : une valeur de `JournalAudit.Action`.
      - `entite_concernee` / `entite_id` : désignation de la ressource.
      - `avant` / `apres` : états JSON-sérialisables.
      - `requete` : objet requête (Django/DRF) pour capturer IP + UA ;
        None quand on journalise hors requête (shell, tâches).

    Stratégie « best-effort tracé » : une erreur de journalisation ne
    doit pas faire échouer l'action métier qu'elle documente, mais elle
    doit être VISIBLE (log d'erreur, exceptions collectionnées) — jamais
    silencieuse. Le vrai verrou « INSERT ONLY » est appliqué en base par
    le trigger PostgreSQL `audit_verrouiller_immutable` (voir
    `audit/migrations/0002_...`), qui couvre même le SQL brut.
    """
    ip_address = None
    user_agent = ""
    if requete is not None:
        ip_address = _adresse_ip(requete)
        user_agent = requete.META.get("HTTP_USER_AGENT", "")[:500]

    try:
        JournalAudit.objects.create(
            utilisateur=utilisateur,
            action=action,
            entite_concernee=entite_concernee,
            entite_id=str(entite_id),
            avant=avant,
            apres=apres,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    except Exception:
        # Best-effort mais jamais muet : l'absence de trace en régime
        # réglementaire est un incident à remonter en monitoring.
        logger.exception(
            "ÉCHEC de journalisation (action=%s, entite=%s:%s) : la "
            "trace n'a pas été écrite.",
            action,
            entite_concernee,
            entite_id,
        )


def _adresse_ip(requete):
    """Adresse IP réelle : respecte le proxy inverse (X-Forwarded-For).

    Anti-falsification : au lieu de prendre le PREMIER élément (contrôlé
    par le client quand le proxy ajoute au header au lieu de le réécrire),
    on prend l'élément en remontant depuis la droite d'autant de sauts
    que de proxies de confiance (`PROXIES_DE_CONFIANCE`, défaut 1).
    Le dernier élément est toujours posé par le proxy immédiat, que le
    client ne contrôle pas. La valeur n'a de valeur probante que si ce
    paramètre correspond à la topologie réelle du déploiement.
    """
    x_forwarded = requete.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded:
        sauts = max(int(getattr(settings, "PROXIES_DE_CONFIANCE", 1)), 0)
        elements = [e.strip() for e in x_forwarded.split(",") if e.strip()]
        if elements:
            # Avec N proxies de confiance, l'IP client est l'élément à
            # N positions depuis la fin ; sans header, c'est REMOTE_ADDR.
            return elements[-(sauts + 1)] if len(elements) > sauts else elements[0]
    return requete.META.get("REMOTE_ADDR")


def adresse_ip_client(requete):
    """Adresse IP du client pour les preuves légales (signature, audit).

    Point d'accès unique partagé par tout le projet (l'ancienne
    duplication dossiers.services._adresse_ip est supprimée) : le
    comportement anti-spoofing est appliqué partout de la même façon.
    """
    return _adresse_ip(requete)