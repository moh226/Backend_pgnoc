from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from sgi.models import (
    ActivitePresentation,
    ConventionTarifaire,
    InformationPresentation,
    MembreEquipe,
    ReferencePresentation,
    SGI,
)


class SGIPublicSerializer(serializers.ModelSerializer):
    """Représentation publique d'une SGI, pour le choix par l'investisseur (UC03)."""

    class Meta:
        model = SGI
        fields = ("id", "nom", "code_sgi", "logo")
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Présentation structurée (sections « À propos »)
# ---------------------------------------------------------------------------

class _ActiviteSerialisee(serializers.ModelSerializer):
    class Meta:
        model = ActivitePresentation
        fields = ("titre", "description", "ordre")


class _MembreSerialise(serializers.ModelSerializer):
    class Meta:
        model = MembreEquipe
        fields = ("nom", "fonction", "ordre")


class _ReferenceSerialisee(serializers.ModelSerializer):
    class Meta:
        model = ReferencePresentation
        fields = ("titre", "annee", "description", "ordre")


class PresentationSectionsSerializer(serializers.Serializer):
    """Sortie structurée de la présentation (fiche investisseur et admin)."""

    forme_sociale = serializers.CharField()
    date_creation_societe = serializers.DateField(allow_null=True)
    capital_social = serializers.CharField()
    numero_agrement = serializers.CharField()
    date_agrement = serializers.DateField(allow_null=True)
    autorite_agrement = serializers.CharField()
    est_regule = serializers.BooleanField()
    mission = serializers.CharField()
    vision = serializers.CharField()
    ancrage_regional = serializers.CharField()
    adresse = serializers.CharField()
    telephone = serializers.CharField()
    email_contact = serializers.EmailField(allow_blank=True)
    site_web = serializers.URLField(allow_blank=True)
    activites = _ActiviteSerialisee(many=True)
    membres = _MembreSerialise(many=True)
    references = _ReferenceSerialisee(many=True)


class PresentationAdminEntreeSerializer(serializers.Serializer):
    """Entrée admin : PUT complet de la présentation (sauvegarde = publiée).

    Les listes sont remplacées intégralement quand la clé est présente
    dans le payload ; sinon elles sont laissées telles quelles.
    """

    forme_sociale = serializers.CharField(required=False, allow_blank=True)
    date_creation_societe = serializers.DateField(required=False, allow_null=True)
    capital_social = serializers.CharField(required=False, allow_blank=True)
    numero_agrement = serializers.CharField(required=False, allow_blank=True)
    date_agrement = serializers.DateField(required=False, allow_null=True)
    autorite_agrement = serializers.CharField(required=False, allow_blank=True)
    mission = serializers.CharField(required=False, allow_blank=True)
    vision = serializers.CharField(required=False, allow_blank=True)
    ancrage_regional = serializers.CharField(required=False, allow_blank=True)
    adresse = serializers.CharField(required=False, allow_blank=True)
    telephone = serializers.CharField(required=False, allow_blank=True)
    email_contact = serializers.EmailField(required=False, allow_blank=True)
    site_web = serializers.URLField(required=False, allow_blank=True)
    activites = _ActiviteSerialisee(many=True, required=False)
    membres = _MembreSerialise(many=True, required=False)
    references = _ReferenceSerialisee(many=True, required=False)


class SGIFicheSerializer(serializers.ModelSerializer):
    """Fiche complète de la SGI pour l'adhésion (UC01) : présentation + convention."""

    presentation = serializers.SerializerMethodField()
    convention = serializers.SerializerMethodField()

    class Meta:
        model = SGI
        fields = ("id", "nom", "code_sgi", "logo", "presentation", "convention")
        read_only_fields = fields

    @extend_schema_field(PresentationSectionsSerializer)
    def get_presentation(self, sgi) -> dict[str, object]:
        try:
            presentation = sgi.presentation
        except InformationPresentation.DoesNotExist:
            return PresentationSectionsSerializer({}).data
        return sections_de_presentation(presentation)

    @extend_schema_field(serializers.DictField)
    def get_convention(self, sgi) -> dict[str, object]:
        try:
            convention = sgi.convention
        except ConventionTarifaire.DoesNotExist:
            return {}
        return {
            "titre": convention.titre,
            "signe_requis": bool(convention.fichier_pdf),
            "fichier_url": convention.fichier_pdf.url if convention.fichier_pdf else None,
        }


def sections_de_presentation(presentation) -> dict[str, object]:
    """Sérialise la présentation en sections (mission, identité, listes…).

    Rétrocompatibilité : tant qu'aucune section n'est renseignée,
    l'ancien texte libre `contenu` est exposé dans `mission` pour ne
    pas orpheliner les présentations publiées avant la refonte.
    """
    if not presentation.est_renseignee() and not presentation.mission and presentation.contenu:
        mission = presentation.contenu
    else:
        mission = presentation.mission

    return {
        "forme_sociale": presentation.forme_sociale,
        "date_creation_societe": presentation.date_creation_societe,
        "capital_social": presentation.capital_social,
        "numero_agrement": presentation.numero_agrement,
        "date_agrement": presentation.date_agrement,
        "autorite_agrement": (
            presentation.autorite_agrement
            or ("AMF-UEMOA (ex-CREPMF)" if presentation.numero_agrement else "")
        ),
        "est_regule": bool(presentation.numero_agrement),
        "mission": mission,
        "vision": presentation.vision,
        "ancrage_regional": presentation.ancrage_regional,
        "adresse": presentation.adresse,
        "telephone": presentation.telephone,
        "email_contact": presentation.email_contact,
        "site_web": presentation.site_web,
        "activites": list(
            presentation.activites.values("titre", "description", "ordre")
        ),
        "membres": list(
            presentation.membres.values("nom", "fonction", "ordre")
        ),
        "references": list(
            presentation.references.values("titre", "annee", "description", "ordre")
        ),
    }