"""Sérialisation du journal d'audit : lecture seule (INSERT ONLY)."""

from rest_framework import serializers

from audit.models import JournalAudit


class JournalAuditSerializer(serializers.ModelSerializer):
    """Trace d'audit côté lecture — aucun champ n'est modifiable."""

    utilisateur_email = serializers.EmailField(
        source="utilisateur.email", read_only=True, default=None,
    )
    action_libelle = serializers.CharField(
        source="get_action_display", read_only=True,
    )

    class Meta:
        model = JournalAudit
        fields = (
            "id", "date_action", "utilisateur", "utilisateur_email",
            "action", "action_libelle", "entite_concernee", "entite_id",
            "avant", "apres", "ip_address", "user_agent",
        )
        read_only_fields = fields