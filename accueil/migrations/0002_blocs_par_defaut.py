"""Blocs par défaut de la page d'accueil (données de premier déploiement).

Les huit blocs de la page d'accueil sont créés à l'état « brouillon »
(date_publication null) : l'Admin Général les enrichit et les publie
depuis l'interface /admin-general/accueil, l'endpoint public ne les
révèle pas avant publication.
"""

from django.db import migrations

BLOCS_DEFAUT = [
    {
        "type": "HERO",
        "titre": "Ouvrez votre compte-titres en toute sécurité, sans vous déplacer",
        "ordre": 0,
        "contenu": {
            "cta_principal": "S'inscrire",
            "lien_principal": "/inscription",
            "cta_secondaire": "Se connecter",
            "lien_secondaire": "/login",
        },
    },
    {
        "type": "REASSURANCE",
        "titre": "",
        "ordre": 1,
        "contenu": {
            "mentions": [
                "Régulé par le CREPMF",
                "KYC documentaire complet",
                "Preuve de vie horodatée et signée",
                "Données chiffrées côté serveur",
            ],
        },
    },
    {
        "type": "CHIFFRES",
        "titre": "La plateforme en chiffres",
        "ordre": 2,
        "contenu": {
            "chiffres": [
                {"valeur": "4", "libelle": "espaces sécurisés"},
                {"valeur": "100 %", "libelle": "dossiers tracés"},
                {"valeur": "1", "libelle": "parcours en ligne"},
            ],
        },
    },
    {
        "type": "ETAPES",
        "titre": "Comment ça marche",
        "ordre": 3,
        "contenu": {
            "etapes": [
                {
                    "titre": "1. Créez votre compte",
                    "description": "Inscription en ligne en quelques minutes.",
                },
                {
                    "titre": "2. Choisissez votre SGI",
                    "description": "Comparez les sociétés partenaires et leur convention.",
                },
                {
                    "titre": "3. Complétez votre dossier KYC",
                    "description": "Pièces d'identité et selfie de vérification, signés côté serveur.",
                },
                {
                    "titre": "4. Suivez l'instruction",
                    "description": "Votre dossier est instruit et tracé par l'agent SGI.",
                },
            ],
        },
    },
    {
        "type": "SECURITE",
        "titre": "Sécurité et conformité",
        "ordre": 4,
        "contenu": {
            "cartes": [
                {
                    "titre": "Preuve de vie signée",
                    "description": "Chaque selfie est horodaté, haché (SHA-256) et signé par le serveur.",
                },
                {
                    "titre": "Journal d'audit immuable",
                    "description": "Chaque action est tracée avec IP, horodatage et état avant/après.",
                },
                {
                    "titre": "Cloisonnement par SGI",
                    "description": "Chaque société ne voit que les dossiers de ses investisseurs.",
                },
                {
                    "titre": "Identification renforcée",
                    "description": "Connexion JWT à durée courte, mots de passe hachés Argon2.",
                },
            ],
        },
    },
    {
        "type": "TEMOIGNAGES",
        "titre": "Ils nous font confiance",
        "ordre": 5,
        "contenu": {
            "temoignages": [
                {
                    "nom": "Awa K.",
                    "role": "Investisseuse",
                    "texte": "J'ai ouvert mon compte-titres à distance, en toute sérénité.",
                },
            ],
        },
    },
    {
        "type": "FAQ",
        "titre": "Questions fréquentes",
        "ordre": 6,
        "contenu": {
            "questions": [
                {
                    "question": "Quels documents dois-je fournir ?",
                    "reponse": "Une pièce d'identité valide et un selfie vous montrant votre carte en main.",
                },
                {
                    "question": "Comment mes données sont-elles protégées ?",
                    "reponse": "Chiffrement côté serveur, journal d'audit immuable et accès cloisonné par SGI.",
                },
                {
                    "question": "Quel est le délai de traitement de mon dossier ?",
                    "reponse": "L'instruction démarre dès la soumission ; chaque étape est tracée.",
                },
            ],
        },
    },
    {
        "type": "APPEL_ACTION",
        "titre": "",
        "ordre": 7,
        "contenu": {
            "cta": "S'inscrire",
            "lien": "/inscription",
            "slogan": "Prêt à ouvrir votre compte-titres ?",
        },
    },
]


def creer_blocs(apps, schema_editor):
    BlocAccueil = apps.get_model("accueil", "BlocAccueil")
    for bloc in BLOCS_DEFAUT:
        BlocAccueil.objects.get_or_create(type=bloc["type"], defaults=bloc)


def supprimer_blocs(apps, schema_editor):
    BlocAccueil = apps.get_model("accueil", "BlocAccueil")
    BlocAccueil.objects.filter(
        type__in=[bloc["type"] for bloc in BLOCS_DEFAUT],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accueil", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(creer_blocs, supprimer_blocs),
    ]