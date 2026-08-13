"""Application « Espace Admin Général » (étape 3F).

Regroupe le back-office réservé à l'Admin Général (superviseur global,
section 4.5 du document de conception) :
  - UC20 : gérer les SGI partenaires (catalogue, activation/suspension) ;
  - UC21 : gérer les utilisateurs internes (comptes, rôles, activation) ;
  - UC22 : consulter le tableau de bord global (agrégats + activité).

Aucun modèle propre : l'app orchestre les modèles existants
(comptes.Utilisateur, sgi.SGI, dossiers.Dossier, audit.JournalAudit).
Toutes les actions sont journalisées dans le journal d'audit immuable.
"""

from django.apps import AppConfig


class AdministrationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "administration"
    verbose_name = "Espace Admin Général"