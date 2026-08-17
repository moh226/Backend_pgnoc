from rest_framework import serializers

from dossiers.models import ChampKYC, Dossier, EtapeKYC, ValeurChamp
from django.core.exceptions import ValidationError as DjangoValidationError



class ChampKYCSerializer(serializers.ModelSerializer):
    """Représentation en lecture de la définition d'un champ KYC."""

    class Meta:
        model = ChampKYC
        fields = (
            "id", "code", "nom", "type", "obligatoire", "ordre",
            "justification", "options_choix", "champ_parent",
            "valeur_declencheur", "formats_acceptes", "taille_max_mo",
        )
        read_only_fields = fields


class EtapeKYCSerializer(serializers.ModelSerializer):
    """Étape KYC avec ses champs imbriqués (parcours complet à afficher)."""

    champs = ChampKYCSerializer(many=True, read_only=True)

    class Meta:
        model = EtapeKYC
        fields = ("id", "nom", "ordre", "champs")
        read_only_fields = fields


class ValeurChampSerializer(serializers.ModelSerializer):
    """Lecture/écriture d'une valeur de champ KYC pour un dossier.

    `fichier` est en lecture seule : la référence MinIO n'est jamais
    acceptée depuis le client (sinon un investisseur pourrait pointer
    vers le document d'un autre dossier et en obtenir une URL signée).
    Elle n'est renseignée que par l'endpoint de téléversement dédié.
    L'empreinte / la signature / l'horodatage des selfies sont des
    preuves SERVEUR : elles ne sont ni envoyées ni modifiables par le
    client.
    """

    class Meta:
        model = ValeurChamp
        fields = (
            "id", "champ", "valeur", "fichier",
            "empreinte_sha256", "signature_serveur", "date_capture",
            "commentaire_agent", "est_corrige", "date_maj",
        )
        read_only_fields = (
            "id", "fichier",
            "empreinte_sha256", "signature_serveur", "date_capture",
            "commentaire_agent", "est_corrige", "date_maj",
        )

    def validate_champ(self, champ):
        """Le champ doit appartenir à une étape ACTIVE de la SGI du dossier concerné."""
        dossier = self.context["dossier"]
        if champ.etape.sgi_id != dossier.sgi_id:
            raise serializers.ValidationError("Ce champ n'appartient pas à la SGI de ce dossier.")
        if not champ.actif or not champ.etape.actif:
            raise serializers.ValidationError(
                "Ce champ a été désactivé par la SGI : il n'est plus saisissable."
            )
        if champ.type in (ChampKYC.TypeChamp.FICHIER, ChampKYC.TypeChamp.SELFIE):
            raise serializers.ValidationError(
                "Un champ de type fichier se remplit via l'endpoint de téléversement dédié."
            )
        return champ

    def validate_valeur(self, valeur):
        """Normalise les valeurs complexes vers leur format de stockage.

        CHOIX_MULTIPLE accepte aussi bien une liste JSON native qu'une
        chaîne JSON : les deux représentent la même donnée et doivent
        produire le même enregistrement (sinon `json.loads` échouerait
        sur la trace Python d'une liste, en 400 trompeur).
        """
        if isinstance(valeur, list):
            import json as module_json

            return module_json.dumps(valeur)
        return valeur


class DossierListSerializer(serializers.ModelSerializer):
    """Représentation légère pour les listes (file d'attente Agent SGI)."""

    investisseur_email = serializers.EmailField(source="utilisateur.email", read_only=True)

    class Meta:
        model = Dossier
        fields = (
            "id", "reference", "investisseur_email", "sgi", "statut",
            "progression_pct", "date_creation", "date_soumission",
        )
        read_only_fields = fields


class DossierDetailSerializer(serializers.ModelSerializer):
    """Représentation complète d'un dossier, avec ses valeurs de champs."""

    valeurs_champs = ValeurChampSerializer(many=True, read_only=True)
    investisseur_email = serializers.EmailField(source="utilisateur.email", read_only=True)
    agent_email = serializers.EmailField(source="agent.email", read_only=True, default=None)

    class Meta:
        model = Dossier
        fields = (
            "id", "reference", "investisseur_email", "sgi", "etape_courante",
            "agent", "agent_email", "statut", "version", "progression_pct",
            "motif_rejet", "convention_acceptee", "type_signature", "date_signature",
            "date_creation",
            "date_soumission", "date_instruction", "date_decision", "valeurs_champs",
        )
        read_only_fields = fields


class DossierCreationSerializer(serializers.ModelSerializer):
    """Création d'un dossier par un investisseur (UC03).

    `utilisateur` est toujours forcé à `request.user` — jamais accepté
    depuis la requête (même logique de sécurité qu'à l'inscription,
    Étape 1.3 : on ne fait jamais confiance à un identifiant envoyé
    par le client pour désigner un autre utilisateur).
    """

    class Meta:
        model = Dossier
        fields = ("id", "sgi", "etape_courante")
        read_only_fields = ("id",)

    def validate_sgi(self, sgi):
        if not sgi.est_active:
            raise serializers.ValidationError("Cette SGI n'accepte plus de nouveaux dossiers.")
        return sgi

    def validate(self, attrs):
        etape = attrs.get("etape_courante")
        if etape and etape.sgi_id != attrs["sgi"].id:
            raise serializers.ValidationError(
                {"etape_courante": "Cette étape n'appartient pas à la SGI sélectionnée."}
            )
        return attrs

    def create(self, validated_data):
        validated_data["utilisateur"] = self.context["request"].user
        try:
            return Dossier.objects.create(**validated_data)
        except DjangoValidationError as exc:
            # Convertit une ValidationError Django (levée par full_clean
            # dans Dossier.save()) en 400 DRF propre, au lieu d'un 500 non
            # intercepté. On utilise `exc.messages` (toujours présent),
            # car `exc.message` n'existe pas quand l'erreur porte un dict
            # ou une liste (cas de full_clean / Dossier.clean()).
            raise serializers.ValidationError(exc.messages)



class TeleversementFichierSerializer(serializers.Serializer):
    """Valide un fichier téléversé pour un ChampKYC de type FICHIER ou SELFIE.

    Serializer simple (pas ModelSerializer) car il ne correspond pas
    directement à un modèle : il combine `champ` (référence) et
    `fichier` (objet fichier brut, jamais persisté tel quel).

    Durcissement : le format est vérifié par le EXTENSION ET par les
    magic bytes du contenu (le Content-Type envoyé par le client est
    falsifiable) ; la taille est plafonnée par `taille_max_mo` du champ,
    avec une borne absolue en secours (un champ sans limite ne signifie
    JAMAIS « illimité ») — 10 Mo pour un FICHIER, 5 Mo pour un SELFIE
    (image de capture caméra, par nature légère).
    """

    # Bornes de secours si le champ n'a pas de limite configurée.
    TAILLE_MAX_SECOURS_MO = 10
    TAILLE_MAX_SELFIE_MO = 5

    # Signatures binaires (magic bytes) reconnues, par type MIME.
    _MAGIC_BYTES = {
        b"\x25\x50\x44\x46": "pdf",
        b"\x89\x50\x4e\x47\x0d\x0a\x1a\x0a": "png",
        b"\xff\xd8\xff": "jpg",
        b"\x47\x49\x46\x38": "gif",
        b"\x52\x49\x46\x46": "webp",
        b"\x50\x4b\x03\x04": "zip",  # docx, xlsx, odt… (conteneurs ZIP)
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1": "ole2",  # .doc/.xls réels (OLE2)
    }
    _CORRESPONDANCES = {
        "pdf": {"pdf"},
        "png": {"png"},
        "jpg": {"jpg", "jpeg"},
        "gif": {"gif"},
        "webp": {"webp"},
        "zip": {"docx", "xlsx", "odt", "ods", "zip"},
        "ole2": {"doc", "xls"},
    }

    champ = serializers.PrimaryKeyRelatedField(
        queryset=ChampKYC.objects.filter(
            type__in=[ChampKYC.TypeChamp.FICHIER, ChampKYC.TypeChamp.SELFIE]
        )
    )
    fichier = serializers.FileField()

    def validate(self, attrs):
        champ = attrs["champ"]
        fichier = attrs["fichier"]
        dossier = self.context["dossier"]

        if champ.etape.sgi_id != dossier.sgi_id:
            raise serializers.ValidationError(
                {"champ": "Ce champ n'appartient pas à la SGI de ce dossier."}
            )

        extension = fichier.name.rsplit(".", 1)[-1].lower() if "." in fichier.name else ""
        formats_autorises = [f.strip().lower() for f in champ.formats_acceptes.split(",")]
        if extension not in formats_autorises:
            raise serializers.ValidationError(
                {"fichier": f"Format '.{extension}' non accepté. Formats autorisés : {champ.formats_acceptes}."}
            )

        self._valider_content_reel(fichier, extension, formats_autorises)

        taille_mo = fichier.size / (1024 * 1024)
        if champ.type == ChampKYC.TypeChamp.SELFIE:
            plafond_mo = champ.taille_max_mo or self.TAILLE_MAX_SELFIE_MO
        else:
            plafond_mo = champ.taille_max_mo or self.TAILLE_MAX_SECOURS_MO
        if taille_mo > plafond_mo:
            raise serializers.ValidationError(
                {"fichier": f"Fichier trop volumineux ({taille_mo:.1f} Mo, max {plafond_mo} Mo)."}
            )

        return attrs

    def _valider_content_reel(self, fichier, extension, formats_autorises):
        """Vérifie que le contenu binaire correspond réellement au format.

        Lit les premiers octets (magic bytes) : le `Content-Type` et le
        nom envoyés par le client sont des données falsifiables.
        - Si le ou les formats autorisés appartiennent aux signatures
          connues (pdf, png, jpg…), la signature est EXIGÉE : un fichier
          sans signature reconnaissable est rejeté (exécutable déguisé).
        - Les formats sans signature fiable (txt, csv…) restent pilotés
          par l'extension.
        """
        signature = fichier.read(16)
        fichier.seek(0)
        if not signature:
            raise serializers.ValidationError(
                {"fichier": "Fichier vide : contenu illisible."}
            )

        genres_autorises = {
            genre
            for genre, extensions in self._CORRESPONDANCES.items()
            if extensions & set(formats_autorises)
        }
        if not genres_autorises:
            return  # format non snifiable (txt, csv…) : l'extension fait foi

        detecte = None
        for entete, genre in self._MAGIC_BYTES.items():
            if signature.startswith(entete):
                detecte = genre
                break
        if detecte not in genres_autorises:
            raise serializers.ValidationError(
                {"fichier": "Le contenu du fichier ne correspond pas au format attendu."}
            )


class SoumettreDossierSerializer(serializers.Serializer):
    """Soumission d'un dossier (UC10).

    Le corps de la requête est volontairement vide : la signature
    électronique n'est plus acceptée ici. Elle est posée séparément via
    `/dossiers/<id>/generer-otp/` puis `/dossiers/<id>/signer/`, avec un
    code OTP généré et vérifié côté serveur (preuve chaînée, voir
    `dossiers/services.poser_signature_otp`).
    """


class SignerDossierSerializer(serializers.Serializer):
    """Signature électronique OTP d'un dossier (preuve serveur)."""

    otp_code = serializers.CharField(
        max_length=6, min_length=6,
        error_messages={
            "min_length": "Le code OTP compte 6 chiffres.",
            "max_length": "Le code OTP compte 6 chiffres.",
        },
    )
