"""Enrichissement du bloc HERO (slogan d'accroche) et nettoyage des titres.

Le HERO disposait d'un titre et de boutons mais d'aucun sous-titre :
la page publique manquait d'un paragraphe de proposition de valeur.
Cette migration ajoute un slogan par défaut éditable par l'Admin Général
et retire les préfixes « 1. », « 2. »… des titres d'étapes (le badge
numéroté de la vitrine les affiche déjà).
"""

from django.db import migrations

SLOGAN_HERO = (
    "Déposez votre demande d'ouverture de compte-titres auprès d'une SGI "
    "agréée du marché régional — pièces justificatives, preuve de vie et "
    "signature, entièrement en ligne."
)

ETAPES_NETTOYEES = [
    {
        "titre": "Créez votre compte",
        "description": "Inscription en ligne en quelques minutes.",
    },
    {
        "titre": "Choisissez votre SGI",
        "description": "Comparez les sociétés partenaires et leur convention.",
    },
    {
        "titre": "Complétez votre dossier KYC",
        "description": "Pièces d'identité et selfie de vérification, signés côté serveur.",
    },
    {
        "titre": "Suivez l'instruction",
        "description": "Votre dossier est instruit et tracé par l'agent SGI.",
    },
]


def maj_blocs(apps, schema_editor):
    BlocAccueil = apps.get_model("accueil", "BlocAccueil")

    hero = BlocAccueil.objects.filter(type="HERO").first()
    if hero is not None:
        contenu = dict(hero.contenu or {})
        contenu.setdefault("slogan", SLOGAN_HERO)
        hero.contenu = contenu
        hero.save(update_fields=["contenu"])

    etapes = BlocAccueil.objects.filter(type="ETAPES").first()
    if etapes is not None:
        contenu = dict(etapes.contenu or {})
        liste = contenu.get("etapes")
        if isinstance(liste, list) and liste:
            contenu["etapes"] = [
                {
                    **(etape if isinstance(etape, dict) else {}),
                    "titre": etape.get("titre", "").lstrip("0123456789. ").strip()
                    if isinstance(etape, dict) else "",
                }
                for etape in liste
            ]
            etapes.contenu = contenu
            etapes.save(update_fields=["contenu"])


def annuler_blocs(apps, schema_editor):
    BlocAccueil = apps.get_model("accueil", "BlocAccueil")

    hero = BlocAccueil.objects.filter(type="HERO").first()
    if hero is not None:
        contenu = dict(hero.contenu or {})
        contenu.pop("slogan", None)
        hero.contenu = contenu
        hero.save(update_fields=["contenu"])

    etapes = BlocAccueil.objects.filter(type="ETAPES").first()
    if etapes is not None:
        contenu = dict(etapes.contenu or {})
        contenu["etapes"] = ETAPES_NETTOYEES
        etapes.contenu = contenu
        etapes.save(update_fields=["contenu"])


class Migration(migrations.Migration):

    dependencies = [
        ("accueil", "0002_blocs_par_defaut"),
    ]

    operations = [
        migrations.RunPython(maj_blocs, annuler_blocs),
    ]
