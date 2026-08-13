# Create your views here.
from rest_framework import generics, permissions, status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView

from audit.models import JournalAudit
from audit.services import journaliser
from comptes.models import Utilisateur
from comptes.serializers import (
    InscriptionInvestisseurSerializer,
    UtilisateurPublicSerializer,
    UtilisateurTokenObtainPairSerializer,
)


class InscriptionInvestisseurAPIView(generics.CreateAPIView):
    """Endpoint public d'inscription pour les investisseurs.

    POST /api/comptes/register/

    Accès : public (AllowAny), car il s'agit du point d'entrée
    permettant à un nouvel investisseur de créer son compte.
    """

    queryset = Utilisateur.objects.all()
    serializer_class = InscriptionInvestisseurSerializer
    permission_classes = (permissions.AllowAny,)
    # Endpoint public : on limite le débit pour freiner les inscriptions
    # automatisées en masse (voir DEFAULT_THROTTLE_RATES['inscription']).
    throttle_scope = "inscription"

    def create(self, request, *args, **kwargs):
        """Surcharge pour renvoyer une représentation publique en sortie.

        Le serializer d'entrée (InscriptionInvestisseurSerializer)
        gère l'écriture (mot de passe, confirmation) ; le serializer
        de sortie (UtilisateurPublicSerializer) garantit qu'aucune
        donnée sensible n'est jamais renvoyée dans la réponse HTTP,
        même par erreur future si le serializer d'entrée évolue.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        utilisateur = serializer.save()

        journaliser(
            utilisateur,
            JournalAudit.Action.INSCRIPTION,
            "Utilisateur",
            str(utilisateur.pk),
            apres={"email": utilisateur.email, "role": utilisateur.role.code},
            requete=request,
        )

        reponse_serializer = UtilisateurPublicSerializer(utilisateur)
        headers = self.get_success_headers(reponse_serializer.data)
        return Response(
            reponse_serializer.data,
            status=status.HTTP_201_CREATED,
            headers=headers,
        )

class ConnexionAPIView(TokenObtainPairView):
    """
    Endpoint de connexion (obtention des tokens JWT).

    POST /api/comptes/login/

    Accès : public (AllowAny est déjà défini par défaut sur
    TokenObtainPairView, aucune surcharge nécessaire).
    """

    serializer_class = UtilisateurTokenObtainPairSerializer
    # Endpoint public : on limite le débit pour freiner les tentatives de
    # brute-force sur les identifiants (voir DEFAULT_THROTTLE_RATES['connexion']).
    throttle_scope = "connexion"

    def post(self, request, *args, **kwargs):
        try:
            reponse = super().post(request, *args, **kwargs)
        except AuthenticationFailed:
            # Échec de connexion : tracé SANS utilisateur imputé (email
            # inconnu ou mot de passe erroné) — indispensable pour capter
            # les tentatives de brute-force dans le journal. SimpleJWT
            # lève une exception plutôt que de renvoyer une réponse 401.
            email = request.data.get("email", "")
            journaliser(
                None,
                JournalAudit.Action.CONNEXION,
                "Connexion",
                "echec",
                apres={"email": email},
                requete=request,
            )
            raise
        # Connexion réussie : trace d'audit avec l'utilisateur réel
        # (SimpleJWT ne peuple pas `request.user` sur cette vue).
        if reponse.status_code == status.HTTP_200_OK:
            email = request.data.get("email", "")
            utilisateur = Utilisateur.objects.filter(email__iexact=email).first()
            journaliser(
                utilisateur,
                JournalAudit.Action.CONNEXION,
                "Utilisateur",
                str(utilisateur.pk) if utilisateur else email,
                apres={"email": email},
                requete=request,
            )
        return reponse
