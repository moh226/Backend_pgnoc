"""Utilitaires partagés de l'app dossiers."""

import uuid as _uuid


def est_uuid_valide(valeur):
    """True si `valeur` est un UUID bien formé (évite un 500 sur filtre)."""
    try:
        _uuid.UUID(str(valeur))
        return True
    except (ValueError, AttributeError):
        return False