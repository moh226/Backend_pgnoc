"""Verrou base : cohérence rôle / SGI portée par un trigger PostgreSQL.

`Utilisateur.clean()` applique déjà la règle au niveau applicatif :
  - un compte AGENT_SGI / ADMIN_SGI doit être rattaché à une SGI ;
  - un compte INVESTISSEUR / ADMIN_GENERAL ne doit pas en avoir.

Une CheckConstraint ORM ne peut pas référencer la table Role (E041,
les jointures sont interdites dans les CHECK) : on la porte donc par
un trigger BEFORE INSERT OR UPDATE, même défense en profondeur.
"""

from django.db import migrations

FONCTION = """
CREATE OR REPLACE FUNCTION verifier_coherence_role_sgi()
RETURNS trigger AS $$
DECLARE
    code_role VARCHAR;
BEGIN
    SELECT code INTO code_role FROM comptes_role WHERE id = NEW.role_id;

    IF code_role IN ('AGENT_SGI', 'ADMIN_SGI') AND NEW.sgi_id IS NULL THEN
        RAISE EXCEPTION
            'Un compte personnel SGI doit être rattaché à une SGI.';
    END IF;
    IF COALESCE(code_role, '') NOT IN ('AGENT_SGI', 'ADMIN_SGI')
       AND NEW.sgi_id IS NOT NULL THEN
        RAISE EXCEPTION
            'Ce rôle ne doit pas être rattaché à une SGI.';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

TRIGGER = """
CREATE TRIGGER verrou_coherence_role_sgi
BEFORE INSERT OR UPDATE OF role_id, sgi_id ON comptes_utilisateur
FOR EACH ROW EXECUTE FUNCTION verifier_coherence_role_sgi();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("comptes", "0005_seed_roles"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[FONCTION, TRIGGER],
            reverse_sql=[
                "DROP TRIGGER IF EXISTS verrou_coherence_role_sgi "
                "ON comptes_utilisateur;",
                "DROP FUNCTION IF EXISTS verifier_coherence_role_sgi();",
            ],
        ),
    ]