from django.apps import AppConfig


class ComptesConfig(AppConfig):
    name = 'comptes'

    def ready(self):
        # Enregistre les récepteurs de signaux (création automatique
        # des profils métier) au démarrage de l'application.
        from comptes import signals  # noqa: F401
