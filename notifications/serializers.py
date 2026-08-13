"""Sérialisation des notifications : lecture + marquage « lue » uniquement."""

from rest_framework import serializers

from notifications.models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    """Représentation d'une notification (aucune écriture côté client)."""

    type_libelle = serializers.CharField(source="get_type_notif_display", read_only=True)

    class Meta:
        model = Notification
        fields = ("id", "titre", "message", "lue", "type_notif", "type_libelle", "date_creation")
        read_only_fields = fields