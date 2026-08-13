"""Connexion via Google OAuth 2.0 (Authorization Code Flow, côté backend).

Flux en 2 étapes, entièrement piloté par le serveur :

    1. 'ConnexionGoogleAPIView` → redirige le navigateur vers l'écran
       de connexion Google (Authorization endpoint), avec un `state`
       aléatoire stocké en session (anti-CSRF / anti-forgery) ;
    2. 'ConnexionGoogleCallbackView` → Google rappelle le backend avec
       un `code'. Le backend échange ce code contre un `access_token`
       (Token endpoint), récupère l'identité (email, Google sub, nom)
       puis remet à l'utilisateur NOS tokens JWT (SimpleJWT) via une
       redirection vers le frontend.

Le `client_secret` Google reste côté serveur (jamais exposé au client).
Les tokens SimpleJWT sont renvoyés dans le fragment d'URL ('#access=…'),
qui n'est ni envoyé au serveur ni loggé côté navigateur.

Sécurité : le paramètre d'état ('state') empêche l'attaque "login CSRF"
(un attaquant qui forcerait un utilisateur à s'authentifier) : le
callback compare la valeur reçue à celle stockée en session.
"""

import secrets
from urllib.parse import urlencode, urlsplit, urlunsplit

import requests
from django.conf import settings
from django.shortcuts import redirect
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from comptes.models import Role, Utilisateur
from comptes.serializers import UtilisateurTokenObtainPairSerializer

# Points de terminaison fixes de Google OAuth 2.0 / OpenID Connect.
_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"

_TIMEOUT = 10  # secondes : ne jamais bloquer le serveur sur Google.


def _rediriger_erreur(message):
    """Redirige vers le frontend avec un code d'erreur en query string.

    Le frontend lit ` ? Error=<code>` et affiche un message adapté ; aucun
    détail sensible n'est exposé ici.
    """
    url = urlsplit(settings.GOOGLE_OAUTH_FRONT_REDIRECT)
    base = urlunsplit((url.scheme, url.netloc, url.path, "", ""))
    return redirect(f"{base}?error={message}")


class ConnexionGoogleAPIView(APIView):
    """Étape 1 : redirige le navigateur vers l'écran de connexion Google.

    GET /api/comptes/oauth/google/login/
    """

    permission_classes = (permissions.AllowAny,)
    serializer_class = serializers.Serializer

    @extend_schema(responses={302: OpenApiTypes.URI})
    def get(self, request):
        if not settings.GOOGLE_OAUTH_CLIENT_ID:
            return Response(
                {"detail": "L'authentification Google n'est pas configurée."},
                status=500,
            )

        state = secrets.token_urlsafe(32)
        request.session["google_oauth_state"] = state

        parametres = {
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "redirect_uri": settings.GOOGLE_OAUTH_CALLBACK_URL,
            "response_type": "code",
            "scope": "openid email profile",
            "prompt": "select_account",
            "state": state,
        }
        return redirect(f"{_AUTH_ENDPOINT}?{urlencode(parametres)}")


class ConnexionGoogleCallbackView(APIView):
    """Étape 2 : rappelé par Google avec le code d'autorisation.

    GET /api/comptes/oauth/google/callback/?code=…&state=…

    Échange le code, récupère l'identité, connecte ou crée le compte,
    puis redirige le navigateur vers le frontend avec les tokens JWT.
    """

    permission_classes = (permissions.AllowAny,)
    serializer_class = serializers.Serializer

    @extend_schema(
        parameters=[
            OpenApiParameter("code", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter("state", OpenApiTypes.STR, OpenApiParameter.QUERY),
        ],
        responses={302: OpenApiTypes.URI},
    )
    def get(self, request):
        erreur = request.query_params.get("error")
        code = request.query_params.get("code")
        state = request.query_params.get("state")

        if erreur:  # refus de l'utilisateur sur l'écran Google
            return _rediriger_erreur("acces_refuse")
        if not code:
            return _rediriger_erreur("code_manquant")

        # Anti falsification du flux : le state doit correspondre à celui
        # généré par /login/. Sinon c'est probablement une requête forgée.
        if state != request.session.pop("google_oauth_state", None):
            return _rediriger_erreur("etat_invalide")

        identite = self._recuperer_identite_google(code)
        if identite is None:
            return _rediriger_erreur("identite_indisponible")

        # Google ne garantit l'adresse que si l'email a été VERIFIÉ par
        # son propriétaire (`email_verified`). Refuser les emails non
        # vérifiés empêche la prise de contrôle d'un compte existant
        # via une adresse usurpée (issue de revue de code).
        if not identite.get("email_verified", False):
            return _rediriger_erreur("email_non_verifie")

        email = identite.get("email") or ""
        google_id = identite.get("sub") or ""
        if not email:
            return _rediriger_erreur("aucun_email")

        utilisateur = _obtenir_ou_creer_investisseur_google(email, google_id, identite)
        if utilisateur is None:
            return _rediriger_erreur("compte_conflit")

        jeton_access = UtilisateurTokenObtainPairSerializer.get_token(utilisateur)
        jeton_refresh = RefreshToken.for_user(utilisateur)
        url = urlsplit(settings.GOOGLE_OAUTH_FRONT_REDIRECT)
        base = urlunsplit((url.scheme, url.netloc, url.path, "", ""))
        fragment = f"access={jeton_access}&refresh={jeton_refresh}"
        return redirect(f"{base}#{fragment}")

    def _recuperer_identite_google(self, code):
        """Échange le code contre un access_token puis récupère le profil."""
        try:
            reponse = requests.post(
                _TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                    "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                    "redirect_uri": settings.GOOGLE_OAUTH_CALLBACK_URL,
                    "grant_type": "authorization_code",
                },
                timeout=_TIMEOUT,
            )
            reponse.raise_for_status()
            access_token = reponse.json()["access_token"]
        except (requests.RequestException, KeyError, ValueError):
            return None

        try:
            profil = requests.get(
                _USERINFO_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=_TIMEOUT,
            )
            profil.raise_for_status()
            return profil.json()
        except (requests.RequestException, ValueError):
            return None


def _obtenir_ou_creer_investisseur_google(email, google_id, identite):
    """Connecte le compte Google à un utilisateur existant ou le crée.

    Règles :
      - email déjà présent en base → on relie le compte en enregistrant
        le `google_id` (le dernier Google utilisé gagne) ;
      - email inconnu → création d'un compte INVESTISSEUR avec un mot
        de passe aléatoire inutilisable (le compte n'est accessible que
        via Google, pas par mot de passe) ;
      - email déjà lié à un AUTRE google_id → conflit, refus (sinon un
        deuxième compte Google pourrait voler l'accès à ce compte).
    """
    try:
        utilisateur = Utilisateur.objects.get(email__iexact=email)
    except Utilisateur.DoesNotExist:
        utilisateur = Utilisateur.objects.create_user(
            email=email,
            password=secrets.token_urlsafe(48),  # mot de passe inutilisable
            role=Role.Code.INVESTISSEUR,
            prenom=identite.get("given_name", ""),
            nom=identite.get("family_name", ""),
        )

    # Guard réglementaire : le flux Google n'existe que pour les
    # INVESTISSEURS. Un email déjà utilisé par un compte AGENT_SGI /
    # ADMIN_SGI / ADMIN_GENERAL (privilégié, sans profil investisseur)
    # est un conflit à refuser — jamais un compte à relier (issue de
    # revue de code : un 500 puis un JWT privilégié étaient possibles).
    if utilisateur.role.code != Role.Code.INVESTISSEUR:
        return None

    profil = utilisateur.profil_investisseur
    if profil.google_id and profil.google_id != google_id:
        return None
    if profil.google_id != google_id:
        profil.google_id = google_id
        profil.save(update_fields=["google_id"])

    return utilisateur