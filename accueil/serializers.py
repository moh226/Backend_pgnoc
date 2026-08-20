"""Sérialiseurs de la page d'accueil (public + espace Admin Général)."""

from rest_framework import serializers

from accueil.models import BlocAccueil

_CLEFS_PAR_TYPE = {
    BlocAccueil.TypeBloc.HERO: ("cta_principal", "lien_principal",
                                "cta_secondaire", "lien_secondaire"),
    BlocAccueil.TypeBloc.APPEL_ACTION: ("cta", "lien", "slogan"),
}

_LISTES_PAR_TYPE = {
    BlocAccueil.TypeBloc.REASSURANCE: ("mentions", "str"),
    BlocAccueil.TypeBloc.CHIFFRES: ("chiffres", "chiffre"),
    BlocAccueil.TypeBloc.ETAPES: ("etapes", "etape"),
    BlocAccueil.TypeBloc.SECURITE: ("cartes", "carte"),
    BlocAccueil.TypeBloc.TEMOIGNAGES: ("temoignages", "temoignage"),
    BlocAccueil.TypeBloc.FAQ: ("questions", "question"),
}

_CLEFS_OBJETS = {
    "chiffre": ("valeur", "libelle"),
    "etape": ("titre", "description"),
    "carte": ("titre", "description"),
    "temoignage": ("nom", "role", "texte"),
    "question": ("question", "reponse"),
}


def _valider_contenu(type_bloc, contenu):
    """Valide la structure du contenu JSON propre à chaque type de bloc.

    Validation souple (elle ne bloque pas les saisies partielles) :
    les clés inattendues sont tolérées, mais un type de valeur erroné
    (ex: liste attendue, chaîne fournie) est rejeté proprement.
    """
    from rest_framework.exceptions import ValidationError

    if not isinstance(contenu, dict):
        raise ValidationError(
            {"contenu": "Le contenu doit être un dictionnaire."}
        )
    for cle in _CLEFS_PAR_TYPE.get(type_bloc, ()):
        if cle in contenu and not isinstance(contenu[cle], str):
            raise ValidationError(
                {"contenu": f"La clé `{cle}` doit être une chaîne."}
            )
    entree = _LISTES_PAR_TYPE.get(type_bloc)
    if entree is not None:
        cle, nom_entite = entree
        valeur = contenu.get(cle, [])
        if not isinstance(valeur, list):
            raise ValidationError(
                {"contenu": f"La clé `{cle}` doit être une liste."}
            )
        if nom_entite == "str":
            for element in valeur:
                if not isinstance(element, str):
                    raise ValidationError(
                        {"contenu": f"Chaque élément de `{cle}` doit être une chaîne."}
                    )
        else:
            for element in valeur:
                if not isinstance(element, dict):
                    raise ValidationError(
                    {"contenu": f"Chaque élément de `{cle}` doit être un objet."}
                )
            for champ in _CLEFS_OBJETS[nom_entite]:
                if champ in element and not isinstance(element[champ], str):
                    raise ValidationError(
                        {"contenu": f"Le champ `{champ}` doit être une chaîne."}
                    )


class BlocAccueilPublicSerializer(serializers.ModelSerializer):
    """Représentation publique : blocs actifs et publiés uniquement."""

    image_url = serializers.SerializerMethodField()

    class Meta:
        model = BlocAccueil
        fields = ("type", "titre", "contenu", "image_url")
        read_only_fields = fields

    def get_image_url(self, bloc) -> str | None:
        if not bloc.image:
            return None
        demande = self.context.get("request")
        if demande is None:
            return bloc.image.url
        return demande.build_absolute_uri(bloc.image.url)


class BlocAccueilAdminSerializer(serializers.ModelSerializer):
    """Lecture/édition d'un bloc par l'Admin Général (PATCH partiel)."""

    image_url = serializers.SerializerMethodField()
    publie = serializers.BooleanField(source="est_publie", read_only=True)

    class Meta:
        model = BlocAccueil
        fields = (
            "type", "titre", "contenu", "image", "image_url",
            "actif", "ordre", "publie", "date_publication", "date_maj",
        )
        read_only_fields = ("type", "date_publication", "date_maj")

    def get_image_url(self, bloc) -> str | None:
        if not bloc.image:
            return None
        demande = self.context.get("request")
        if demande is None:
            return bloc.image.url
        return demande.build_absolute_uri(bloc.image.url)

    def validate_contenu(self, valeur):
        type_bloc = self.instance.type if self.instance else self.initial_data.get("type")
        _valider_contenu(type_bloc, valeur)
        return valeur


class _ElementOrdreSerializer(serializers.Serializer):
    """Un bloc dans la liste d'ordonnancement (type + état + position)."""

    type = serializers.ChoiceField(choices=BlocAccueil.TypeBloc.choices)
    actif = serializers.BooleanField(default=True)
    ordre = serializers.IntegerField(min_value=0)


class BlocAccueilOrdreSerializer(serializers.Serializer):
    """Réordonnancement / activation des blocs + publication globale."""

    blocs = _ElementOrdreSerializer(many=True, required=False)
    publier = serializers.BooleanField(required=False, default=False)