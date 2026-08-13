"""Signaux de l'app comptes.

Centralise la création automatique du profil métier (Profil*) associé
au rôle d'un utilisateur. Placer cette logique dans un signal `post_save`
— plutôt que dans le seul manager `create_user` — garantit qu'un profil
est créé quel que soit le chemin de création de l'utilisateur :

    - inscription via l'API (manager `create_user`) ;
    - création via l'admin Django (formulaire → `Model.save()`, qui ne
      passe PAS par le manager) ;
    - `create_superuser`, shell, fixtures, etc.

Sans ce signal, un utilisateur créé depuis l'admin Django n'aurait jamais
son Profil* correspondant, laissant des données métier incohérentes.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from comptes.models import (
    ProfilAdminGeneral, ProfilAdminSGI, ProfilAgentSGI, ProfilInvestisseur,
    Role, Utilisateur,
)

# Correspondance rôle → modèle de profil dédié.
MAPPING_PROFIL = {
    Role.Code.INVESTISSEUR: ProfilInvestisseur,
    Role.Code.AGENT_SGI: ProfilAgentSGI,
    Role.Code.ADMIN_SGI: ProfilAdminSGI,
    Role.Code.ADMIN_GENERAL: ProfilAdminGeneral,
}


@receiver(post_save, sender=Utilisateur, dispatch_uid="comptes_creer_profil")
def creer_profil_utilisateur(sender, instance, created, **kwargs):
    """Crée le Profil* correspondant au rôle, à la création de l'utilisateur.

    Idempotent (`get_or_create`) : ne fait rien si le profil existe déjà,
    ce qui évite toute erreur d'intégrité si l'objet est resauvegardé ou
    si un profil a déjà été instancié par ailleurs (ex: inline admin).
    """
    if not created:
        return

    modele_profil = MAPPING_PROFIL.get(instance.role.code)
    if modele_profil is None:
        return

    modele_profil.objects.get_or_create(utilisateur=instance)
