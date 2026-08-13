"""Verrou INSERT ONLY du journal d'audit, appliqué en base (trigger).

Les garde-fous applicatifs de `JournalAudit` (QuerySet refusant
update/delete, `save()` bloqué hors force_insert) ne couvrent pas le
SQL brut ni `bulk_update`. Pour une exigence réglementaire « boîte
noire », la base elle-même doit refuser toute mutation : ce trigger
PostgreSQL RAISE dès qu'une ligne existante du journal est modifiée ou
supprimée — par l'ORM comme par toute requête directe.

Le trigger est recréé par `CREATE OR REPLACE` : appliquer plusieurs fois
cette migration reste idempotent.
"""

from django.db import migrations


class Migration(migrations.Migration):
    """Verrouille la table audit_journalaudit en écriture seule."""

    dependencies = [
        ("audit", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE OR REPLACE FUNCTION audit_verrouiller_immutable()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'journal d''audit en ecriture seule (INSERT ONLY)';
            END;
            $$;

            CREATE TRIGGER verrou_insert_only
            BEFORE UPDATE OR DELETE ON audit_journalaudit
            FOR EACH ROW
            EXECUTE FUNCTION audit_verrouiller_immutable();
            """,
            reverse_sql="""
            DROP TRIGGER IF EXISTS verrou_insert_only ON audit_journalaudit;
            DROP FUNCTION IF EXISTS audit_verrouiller_immutable();
            """,
        ),
    ]