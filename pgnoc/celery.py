"""Configuration Celery pour PGNOC-TI.

Charge automatiquement les tâches de chaque app Django via
`autodiscover_tasks()` et lit la configuration Redis depuis les
variables d'environnement (python-decouple).
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pgnoc.settings")

app = Celery("pgnoc")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
