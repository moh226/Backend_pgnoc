"""Assure le chargement de l'app Celery au démarrage de Django."""

from pgnoc.celery import app as celery_app

__all__ = ["celery_app"]
