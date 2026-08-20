from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.core.exceptions import ValidationError as DjangoValidationError


from comptes.models import ProfilInvestisseur, Utilisateur, Role


class InscriptionInvestisseurSerializer(serializers.ModelSerializer):
    """Serializer d'inscription publique, réservé au rôle Investisseur.

    Le rôle n'est volontairement PAS exposé en entrée : il est forcé
    à INVESTISSEUR côté serveur pour empêcher un utilisateur non
    authentifié de s'auto-attribuer un rôle privilégié (AGENT_SGI,
    ADMIN_SGI, ADMIN_GENERAL) via ce endpoint public.
    """

    password = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password],
        style={"input_type": "password"},
    )
    password_confirmation = serializers.CharField(
        write_only=True,
        required=True,
        style={"input_type": "password"},
    )

    class Meta:
        model = Utilisateur
        fields = (
            "id",
            "email",
            "prenom",
            "nom",
            "password",
            "password_confirmation",
        )
        read_only_fields = ("id",)

    def validate_email(self, value):
        """Normalise l'email et vérifie l'unicité de façon explicite.

        La contrainte unique existe déjà en base, mais une validation
        applicative renvoie une erreur 400 propre plutôt qu'une
        IntegrityError 500 en cas de course conditionnelle rare.
        """
        email = Utilisateur.objects.normalize_email(value)
        if Utilisateur.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError(
                "Un compte existe déjà avec cette adresse email."
            )
        return email

    def validate(self, attrs):
        """Vérifie la correspondance des deux mots de passe saisis."""
        if attrs["password"] != attrs["password_confirmation"]:
            raise serializers.ValidationError(
                {"password_confirmation": "Les mots de passe ne correspondent pas."}
            )
        return attrs

    def create(self, validated_data):
        """Crée l'utilisateur via le manager (hachage du mot de passe inclus).

        Le rôle est forcé à INVESTISSEUR ici, indépendamment de tout
        ce qui pourrait être envoyé dans la requête.
        """
        validated_data.pop("password_confirmation")
        password = validated_data.pop("password")
        try:
            return Utilisateur.objects.create_user(
                password=password,
                role=Role.Code.INVESTISSEUR,
                **validated_data
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)



class UtilisateurPublicSerializer(serializers.ModelSerializer):
    """Représentation publique et minimale d'un utilisateur (lecture seule).

    Utilisé en réponse après inscription — ne contient jamais le
    mot de passe (même haché), ni de champs internes sensibles.
    """

    class Meta:
        model = Utilisateur
        fields = ("id", "email", "prenom", "nom", "role", "date_joined")
        read_only_fields = fields


class UtilisateurTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Serializer JWT personnalisé injectant le rôle et l'email dans le payload.

    Inclure le rôle directement dans le token évite un aller-retour
    API supplémentaire côté frontend pour connaître les permissions
    de l'utilisateur juste après connexion.
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["email"] = user.email
        token["role"] = user.role.code
        return token

    def validate(self, attrs):
        """Ajoute les infos utilisateur à la réponse, en plus des tokens.

        `super().validate()` échoue déjà avec un 401 si les
        identifiants sont invalides ou si `is_active` est False
        (comportement natif de SimpleJWT/Django), donc aucune
        vérification manuelle supplémentaire n'est nécessaire ici.
        """
        data = super().validate(attrs)
        data["utilisateur"] = UtilisateurPublicSerializer(self.user).data
        return data


class AgentSerializer(serializers.ModelSerializer):
    """Gestion d'un compte Agent SGI par l'Admin SGI (UC18).

    - La SGI est TOUJOURS celle de l'administrateur connecté (jamais
      acceptée depuis le client) : strict cloisonnement multi-tenant.
    - `mot_de_passe` est optionnel ; absent, il est généré
      aléatoirement et renvoyé UNE SEULE fois dans la réponse (pas
      réafficher ensuite). Les rôles AGENT_SGI ont vocation à être
      remis via un canal sécurisé par l'admin SGI.
    - `email` est l'identifiant de connexion : modifiable à la
      création, mais pas via un PATCH.
    - Aucune suppression destructive : on bascule `is_active`.
    """

    matricule = serializers.CharField(
        source="profil_agent_sgi.matricule", required=False, allow_blank=True,
    )
    mot_de_passe = serializers.CharField(
        write_only=True, required=False, allow_blank=True,
        style={"input_type": "password"},
    )
    mot_de_passe_initial = serializers.CharField(read_only=True)

    class Meta:
        model = Utilisateur
        fields = (
            "id", "email", "prenom", "nom", "matricule",
            "is_active", "date_joined", "mot_de_passe", "mot_de_passe_initial",
        )
        read_only_fields = ("id", "date_joined")

    def validate_email(self, value):
        """Normalise l'email et vérifie l'unicité insensible à la casse.

        Sans ce contrôle appliqué au niveau du sérialiseur, un email
        déjà existant avec une casse différente déclencherait une
        IntegrityError (contrainte unique sensible à la casse) et donc
        une réponse 500 au lieu d'une erreur 400 exploitable.
        Seule la création est concernée : l'email n'est pas modifiable
        en mise à jour (voir `update`).
        """
        email = Utilisateur.objects.normalize_email(value)
        if self.instance is None and Utilisateur.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError(
                "Un compte existe déjà avec cette adresse email."
            )
        return email

    @staticmethod
    def _generer_mot_de_passe():
        import random
        import string

        alphabet = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
        return "".join(random.SystemRandom().choice(alphabet) for _ in range(12))

    def create(self, validated_data):
        from django.db import transaction

        from comptes.models import ProfilAgentSGI, Role

        admin = self.context["request"].user
        # Le champ `source="profil_agent_sgi.matricule"` imbrique la
        # valeur dans validated_data["profil_agent_sgi"].
        matricule = validated_data.pop("profil_agent_sgi", {}).get("matricule", "")
        mot_de_passe = validated_data.pop("mot_de_passe", "") or self._generer_mot_de_passe()

        # Atomique : un échec de la création du profil ne laisse jamais
        # un compte agent orphelin derrière lui.
        with transaction.atomic():
            utilisateur = Utilisateur.objects.create_user(
                email=validated_data["email"],
                password=mot_de_passe,
                role=Role.objects.get(code=Role.Code.AGENT_SGI),
                sgi_id=admin.sgi_id,
                prenom=validated_data.get("prenom", ""),
                nom=validated_data.get("nom", ""),
                is_active=True,
            )
            profil = ProfilAgentSGI.objects.get(utilisateur=utilisateur)
            if matricule:
                profil.matricule = matricule
                profil.save(update_fields=["matricule"])

        self._mot_de_passe_initial = mot_de_passe
        return utilisateur

    def update(self, instance, validated_data):
        if "email" in validated_data:
            raise serializers.ValidationError(
                {"email": "L'email d'un agent n'est pas modifiable : "
                          "désactivez le compte et recréez-le si besoin."}
            )
        instance.prenom = validated_data.get("prenom", instance.prenom)
        instance.nom = validated_data.get("nom", instance.nom)
        instance.is_active = validated_data.get("is_active", instance.is_active)
        instance.save(update_fields=["prenom", "nom", "is_active"])

        profil = validated_data.pop("profil_agent_sgi", {})
        if "matricule" in profil:
            profil_agent = instance.profil_agent_sgi
            profil_agent.matricule = profil["matricule"]
            profil_agent.save(update_fields=["matricule"])
        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)
        initial = getattr(self, "_mot_de_passe_initial", "") or ""
        if data.get("matricule") is None:
            data["matricule"] = ""
        if initial:
            data["mot_de_passe_initial"] = initial
        return data


class ProfilMoiSerializer(serializers.ModelSerializer):
    """Lecture/mise à jour du profil personnel (GET/PATCH /moi/profil/).

    Endpoint « moi », commun aux quatre rôles :
      - l'email et le rôle sont en lecture seule (identifiants système) ;
      - le champ métier dépend du rôle (type_personne pour
        l'investisseur, matricule pour l'agent, fonction pour l'admin
        SGI) ; il n'est renvoyé qu'au rôle concerné, et les champs
        d'un autre rôle sont ignorés silencieusement en écriture.
    """

    role = serializers.CharField(source="role.code", read_only=True)
    sgi = serializers.CharField(source="sgi.nom", read_only=True, default=None)
    type_personne = serializers.ChoiceField(
        source="profil_investisseur.type_personne",
        choices=ProfilInvestisseur.TypePersonne.choices,
        required=False,
    )
    matricule = serializers.CharField(
        source="profil_agent_sgi.matricule", required=False, allow_blank=True,
    )
    fonction = serializers.CharField(
        source="profil_admin_sgi.fonction", required=False, allow_blank=True,
    )

    class Meta:
        model = Utilisateur
        fields = (
            "id", "email", "prenom", "nom", "role", "sgi", "date_joined",
            "type_personne", "matricule", "fonction",
        )
        read_only_fields = ("id", "email", "date_joined")

    def update(self, instance, validated_data):
        instance.prenom = validated_data.get("prenom", instance.prenom)
        instance.nom = validated_data.get("nom", instance.nom)
        instance.save(update_fields=["prenom", "nom", "date_maj"])

        profil_investisseur = validated_data.pop("profil_investisseur", {})
        if instance.est_investisseur and "type_personne" in profil_investisseur:
            profil = instance.profil_investisseur
            profil.type_personne = profil_investisseur["type_personne"]
            profil.save(update_fields=["type_personne"])

        profil_agent = validated_data.pop("profil_agent_sgi", {})
        if instance.est_agent_sgi and "matricule" in profil_agent:
            profil = instance.profil_agent_sgi
            profil.matricule = profil_agent["matricule"]
            profil.save(update_fields=["matricule"])

        profil_admin = validated_data.pop("profil_admin_sgi", {})
        if instance.est_admin_sgi and "fonction" in profil_admin:
            profil = instance.profil_admin_sgi
            profil.fonction = profil_admin["fonction"]
            profil.save(update_fields=["fonction"])
        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)
        champs_du_role = {
            Role.Code.INVESTISSEUR: {"type_personne"},
            Role.Code.AGENT_SGI: {"matricule"},
            Role.Code.ADMIN_SGI: {"fonction"},
            Role.Code.ADMIN_GENERAL: set(),
        }[instance.role.code]
        for champ in ("type_personne", "matricule", "fonction"):
            if champ not in champs_du_role:
                data.pop(champ, None)
        return data


class ChangerMotDePasseSerializer(serializers.Serializer):
    """Changement de mot de passe avec vérification de l'ancien."""

    ancien_mot_de_passe = serializers.CharField(
        write_only=True, style={"input_type": "password"},
    )
    nouveau_mot_de_passe = serializers.CharField(
        write_only=True, validators=[validate_password],
        style={"input_type": "password"},
    )
    confirmation = serializers.CharField(
        write_only=True, style={"input_type": "password"},
    )

    def validate(self, attrs):
        if attrs["nouveau_mot_de_passe"] != attrs["confirmation"]:
            raise serializers.ValidationError(
                {"confirmation": "Les mots de passe ne correspondent pas."}
            )
        if not self.context["request"].user.check_password(attrs["ancien_mot_de_passe"]):
            raise serializers.ValidationError(
                {"ancien_mot_de_passe": "L'ancien mot de passe est incorrect."}
            )
        return attrs