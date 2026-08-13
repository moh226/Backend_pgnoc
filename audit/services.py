"""Service d'enregistrement du journal d'audit.

Point d'accès unique à la création de traces (`INSERT ONLY`) : aucun
autre module n'instancie `JournalAudit` directement. Ce service est
appelé par le workflow des dossiers, les vues d'authentification, et
toute action sensible à venir.
"""

import logging

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

    Note : la valeur n'a de valeur probante que si le header est posé
    par un proxy de confiance ; `SECURE_PROXY_SSL_HEADER` / le reverse
    proxy doivent normaliser X-Forwarded-For en amont.
    """
    x_forwarded = requete.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded:
        # Le premier élément est l'adresse d'origine côté client.
        return x_forwarded.split(",")[0].strip()
    return requete.META.get("REMOTE_ADDR")