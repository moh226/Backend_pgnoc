"""Sérialiseurs de paramétrage du parcours KYC par l'Admin SGI (UC15).

Le cloisonnement est strict : aucune étape ni champ d'une autre SGI
n'est lisible/écrivable (querysets filtrés + `validate_etape`).
La validation métier du modèle (`clean()` → `full_clean()` dans
`save()`) est convertie en erreur DRF propre, y compris les collisions
de contraintes uniques (ordre d'étape, code de champ).
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from rest_framework import serializers

from dossiers.models import ChampKYC, EtapeKYC


def _convertir_erreur_metier(exception):
    """Transforme ValidationError Django / IntegrityError en erreur DRF 400."""
    if isinstance(exception, DjangoValidationError):
        return serializers.ValidationError(
            exception.message_dict
            if hasattr(exception, "error_dict")
            else exception.messages
        )
    if isinstance(exception, IntegrityError):
        return serializers.ValidationError(
            "Un élément identique existe déjà (contrainte d'unicité)."
        )
    return serializers.ValidationError(str(exception))


class EtapeKYCAdminSerializer(serializers.ModelSerializer):
    """Création / mise à jour d'une étape du parcours (SGI de l'admin forcée)."""

    class Meta:
        model = EtapeKYC
        fields = ("id", "nom", "ordre", "actif", "date_creation")
        read_only_fields = ("id", "date_creation")

    def create(self, validated_data):
        validated_data["sgi_id"] = self.context["request"].user.sgi_id
        try:
            return super().create(validated_data)
        except (DjangoValidationError, IntegrityError) as exc:
            raise _convertir_erreur_metier(exc)


class ChampKYCAdminSerializer(serializers.ModelSerializer):
    """Création / mise à jour d'un champ KYC de la SGI de l'admin connecté."""

    class Meta:
        model = ChampKYC
        fields = (
            "id", "etape", "code", "nom", "type", "obligatoire", "ordre",
            "justification", "options_choix", "champ_parent",
            "valeur_declencheur", "formats_acceptes", "taille_max_mo", "actif",
        )
        read_only_fields = ("id",)

    def validate_etape(self, etape):
        sgi_id = self.context["request"].user.sgi_id
        if etape.sgi_id != sgi_id:
            raise serializers.ValidationError(
                "Cette étape n'appartient pas à votre SGI."
            )
        return etape

    def validate_champ_parent(self, champ_parent):
        if champ_parent is None:
            return None
        etape = self.initial_data.get("etape")
        if etape and str(champ_parent.etape_id) != str(etape):
            raise serializers.ValidationError(
                "Le champ parent doit appartenir à la même étape."
            )
        return champ_parent

    def validate(self, attrs):
        if attrs.get("type") == ChampKYC.TypeChamp.FICHIER and not attrs.get("taille_max_mo"):
            raise serializers.ValidationError(
                {"taille_max_mo": "Un champ FICHIER doit définir une taille "
                                  "maximale (en Mo) : l'upload ne peut jamais "
                                  "être illimité."}
            )
        return attrs

    def create(self, validated_data):
        try:
            return super().create(validated_data)
        except (DjangoValidationError, IntegrityError) as exc:
            raise _convertir_erreur_metier(exc)

    def update(self, instance, validated_data):
        try:
            return super().update(instance, validated_data)
        except (DjangoValidationError, IntegrityError) as exc:
            raise _convertir_erreur_metier(exc)