from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from sgi.models import InformationPresentation, SGI


class SGIPublicSerializer(serializers.ModelSerializer):
    """Représentation publique d'une SGI, pour le choix par l'investisseur (UC03)."""

    class Meta:
        model = SGI
        fields = ("id", "nom", "code_sgi", "logo")
        read_only_fields = fields


class SGIFicheSerializer(serializers.ModelSerializer):
    """Fiche complète de la SGI pour l'adhésion (UC01) : présentation + convention."""

    presentation = serializers.SerializerMethodField()
    convention = serializers.SerializerMethodField()

    class Meta:
        model = SGI
        fields = ("id", "nom", "code_sgi", "logo", "presentation", "convention")
        read_only_fields = fields

    @extend_schema_field(serializers.CharField)
    def get_presentation(self, sgi) -> str:
        try:
            return sgi.presentation.contenu
        except InformationPresentation.DoesNotExist:
            return ""

    @extend_schema_field(serializers.DictField)
    def get_convention(self, sgi) -> dict[str, object]:
        try:
            convention = sgi.convention
        except sgi.convention.RelatedObjectDoesNotExist:
            return {}
        return {
            "titre": convention.titre,
            "signe_requis": bool(convention.fichier_pdf),
        }