"""Sérialiseurs de l'espace Admin Général (UC20-21)."""

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework import serializers

from comptes.models import Role, Utilisateur
from sgi.models import SGI


class SGIAdminSerializer(serializers.ModelSerializer):
    """UC20 — SGI partenaires vues par l'Admin Général.

    La suppression destructive d'une SGI n'existe pas : une SGI se
    suspend (`est_active=False`), ce qui bloque l'ouverture de nouveaux
    dossiers (contrôle porté par le workflow à la soumission).
    """

    nb_utilisateurs = serializers.IntegerField(read_only=True)
    nb_dossiers = serializers.IntegerField(read_only=True)

    class Meta:
        model = SGI
        fields = [
            "id", "nom", "code_sgi", "logo", "est_active",
            "date_creation", "nb_utilisateurs", "nb_dossiers",
        ]
        read_only_fields = ["id", "date_creation"]


class UtilisateurAdminSerializer(serializers.ModelSerializer):
    """UC21 — Comptes internes gérés par l'Admin Général.

    - La création exige un mot de passe initial fort (jamais généré
      côté serveur : il est remis à l'intéressé par un canal sûr).
    - Les comptes INVESTISSEUR ne se créent pas ici : ils passent par
      l'inscription publique (l'espace gère agents, admins SGI et
      administratifs).
    - Cohérence rôle/SGI portée par `Utilisateur.clean()` (appliquée
      en création comme en mise à jour).
    - Jamais de DELETE : on bascule `is_active`.
    """

    role = serializers.SlugRelatedField(
        slug_field="code", queryset=Role.objects.all(),
    )
    mot_de_passe = serializers.CharField(
        write_only=True, required=False, allow_blank=True,
        style={"input_type": "password"},
    )

    class Meta:
        model = Utilisateur
        fields = [
            "id", "email", "prenom", "nom", "role", "sgi",
            "is_active", "mot_de_passe", "date_joined",
        ]
        read_only_fields = ["id", "date_joined"]

    def validate(self, attrs):
        role = attrs.get("role")
        if role and role.code == Role.Code.INVESTISSEUR:
            raise serializers.ValidationError({
                "role": "Les comptes investisseurs passent par l'inscription publique, "
                        "pas par l'espace Admin Général.",
            })
        email = attrs.get("email")
        qs = Utilisateur.objects.filter(email__iexact=email)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if email and qs.exists():
            raise serializers.ValidationError({
                "email": "Un utilisateur avec cette adresse email existe déjà.",
            })
        return attrs

    def _appliquer(self, instance, donnees):
        """Applique la fusion patch+instance en respectant `Utilisateur.clean()`.

        On n'appelle PAS `full_clean()` : il validerait `password`
        (champ interne d'AbstractBaseUser, vide à la création).
        L'email, le mot de passe et l'unicité sont déjà validés par le
        serializer ; `clean()` porte la règle métier rôle/SGI.
        """
        for champ in ("email", "prenom", "nom", "role", "sgi", "is_active"):
            if champ in donnees:
                setattr(instance, champ, donnees[champ])
        try:
            with transaction.atomic():
                instance.clean()
                instance.save()
                return instance
        except ValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc

    def create(self, validated_data):
        mot_de_passe = validated_data.pop("mot_de_passe", "")
        if not mot_de_passe:
            raise serializers.ValidationError({
                "mot_de_passe": "Le mot de passe initial est obligatoire à la création.",
            })
        try:
            validate_password(mot_de_passe)
        except ValidationError as exc:
            raise serializers.ValidationError({"mot_de_passe": exc.messages}) from exc
        utilisateur = self._appliquer(Utilisateur(), validated_data)
        utilisateur.set_password(mot_de_passe)
        utilisateur.save(update_fields=["password"])
        return utilisateur

    def update(self, instance, validated_data):
        validated_data.pop("mot_de_passe", None)
        return self._appliquer(instance, validated_data)