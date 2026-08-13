"""Migration de données : insère les 4 rôles RBAC de la plateforme.

Sans cette migration, `UtilisateurManager.create_user()` et
`create_superuser()` échouent avec `Role.DoesNotExist` sur une base
fraîchement installée (aucun rôle n'existe encore).
"""

from django.db import migrations

ROLES = [
    ("INVESTISSEUR", "Investisseur"),
    ("AGENT_SGI", "Agent SGI"),
    ("ADMIN_SGI", "Admin SGI"),
    ("ADMIN_GENERAL", "Admin Général"),
]


def creer_roles(apps, schema_editor):
    Role = apps.get_model("comptes", "Role")
    for code, libelle in ROLES:
        Role.objects.get_or_create(code=code, defaults={"libelle": libelle})


def supprimer_roles(apps, schema_editor):
    Role = apps.get_model("comptes", "Role")
    Role.objects.filter(code__in=[code for code, _ in ROLES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("comptes", "0004_profiladmingeneral_profiladminsgi_profilagentsgi_and_more"),
    ]

    operations = [
        migrations.RunPython(creer_roles, supprimer_roles),
    ]
