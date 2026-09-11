"""Enveloppe d'erreur unifiée pour toute l'API (expérience utilisateur).

Problème résolu : sans point central, les erreurs partaient dans des
formats hétérogènes — `{"detail": "…"}` pour les réponses construites à
la main, `{"champ": ["…"]}` pour la validation DRF, une simple liste
`["…"]` pour certaines ValidationError, et des messages anglais bruts
pour les exceptions standard DRF (« Not found. », « Request was
throttled… »). Le frontend ne pouvait ni afficher proprement, ni
brancher de logique (retry, redirection, focus champ) sur ces formes.

Toute erreur renvoyée au frontend suit désormais le MÊME format :

    {
      "code": "DOSSIER_INCOMPLET",        # stable, pour la logique UI
      "message": "…",                      # français, actionnable
      "detail": "…",                       # alias rétrocompatibilité
      "champs": {"email": ["…"]},          # erreurs par champ de formulaire
      "champs_manquants": [...]            # contexte métier éventuel
    }

`code` : identifiant stable, en MAJUSCULES, que le frontend peut tester
(`if code === "DOSSIER_INCOMPLET" …`). `message` : phrase française
complète, prête à afficher telle quelle (une bannière, un toast…).
`champs` : dictionnaire champ → liste de messages, pour surligner les
inputs fautifs d'un formulaire. Toute autre clé (ex. `champs_manquants`,
`champs_a_corriger`) est du contexte métier qui aide l'UI à GUIDER
l'utilisateur plutôt qu'à lui demander de deviner.
"""

import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.core.exceptions import ValidationError as ErreurValidationDjango
from rest_framework import status as http_status
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import PermissionDenied as PermissionRefuseeDRF
from rest_framework.exceptions import ValidationError as ErreurValidationDRF
from rest_framework.response import Response
from rest_framework.views import exception_handler as handler_drf

logger = logging.getLogger("pgnoc")

# Messages standard DRF/SimpleJWT en anglais → équivalents français.
# Seuls les messages les plus fréquents : les autres exceptions portent
# déjà des messages français (levés par nos vues/serializeurs avec un
# texte personnalisé, conservé tel quel).
_TRADUCTIONS = {
    "No active account found with the given credentials":
        "Aucun compte actif ne correspond à ces identifiants.",
    "Token is invalid or expired":
        "Session expirée ou invalide : veuillez vous reconnecter.",
    "Token is blacklisted":
        "Session révoquée : veuillez vous reconnecter.",
    "Given token not valid for any token type":
        "Session expirée ou invalide : veuillez vous reconnecter.",
    "Token contained no recognizable user identification":
        "Session invalide : veuillez vous reconnecter.",
    "User not found":
        "Compte introuvable : veuillez vous reconnecter.",
    "User is inactive":
        "Ce compte est désactivé.",
    "Authentication credentials were not provided.":
        "Vous devez être connecté pour accéder à cette ressource.",
    "Authentication credential were not provided.":
        "Vous devez être connecté pour accéder à cette ressource.",
    "Incorrect authentication credentials.":
        "Identifiants invalides.",
    "Invalid token.":
        "Session invalide : veuillez vous reconnecter.",
    "Invalid input.":
        "Données soumises invalides.",
    "Malformed request.":
        "Requête mal formée.",
    "Invalid page.":
        "Cette page n'existe pas.",
    "Requested page is out of range.":
        "Cette page n'existe pas.",
    "You do not have permission to perform this action.":
        "Vous n'avez pas la permission d'effectuer cette action.",
    "Not found.":
        "Ressource introuvable.",
    "This method is not allowed.":
        "Cette méthode HTTP n'est pas autorisée sur cette ressource.",
}


class ApiError(Exception):
    """Erreur métier levée depuis n'importe quelle couche (service,
    workflow, vue…), transformée en enveloppe unifiée par le handler.

    Utilisation :
        raise ApiError(
            "DOSSIER_INCOMPLET",
            "Le dossier ne peut pas être soumis : 2 champs restent vides.",
            statut=400,
            champs_manquants=[{"etape": "Identité", "champ": "Nom"}],
        )
    """

    def __init__(self, code, message, statut=http_status.HTTP_400_BAD_REQUEST,
                 champs=None, **contexte):
        super().__init__(message)
        self.code = code
        self.message = message
        self.statut = statut
        self.champs = champs
        self.contexte = contexte


def _corps(code, message, champs=None, contexte=None):
    """Construit le corps JSON standard d'une erreur."""
    corps = {
        "code": code,
        "message": message,
        # Alias rétrocompatible : le frontend actuel lit `detail`.
        "detail": message,
    }
    if champs:
        corps["champs"] = champs
    if contexte:
        corps.update(contexte)
    return corps


def erreur(code, message, statut=http_status.HTTP_400_BAD_REQUEST,
           champs=None, **contexte):
    """Réponse d'erreur au format unifié — remplace les
    `Response({"detail": …}, status=…)` dispersés dans les vues.

    Utilisation :
        return erreur(
            "CHAMP_VERROUILLE",
            "Le champ « Nom » est verrouillé…",
            status.HTTP_403_FORBIDDEN,
            champs_a_corriger=["Nom", "Adresse"],
        )
    """
    return Response(_corps(code, message, champs, contexte), status=statut)


def _traduire(texte):
    """Traduit en français les messages standard connus, sinon les laisse."""
    return _TRADUCTIONS.get(str(texte).strip(), str(texte))


def _formater_validation(detail):
    """Normalise le `detail` d'une ValidationError DRF (dict, liste ou
    chaîne) vers l'enveloppe unifiée.

    DRF accepte trois formes de payload, et nos vues en produisaient les
    trois selon le chemin d'erreur — le frontend recevait donc des
    structures différentes pour la même famille d'incident.
    """
    if isinstance(detail, dict):
        champs = {}
        messages_globaux = []
        for cle, valeur in detail.items():
            messages = (
                [str(m) for m in valeur] if isinstance(valeur, (list, tuple))
                else [str(valeur)]
            )
            # Les erreurs "non_field_errors" / "__all__" ne sont pas
            # rattachées à un champ : elles rejoignent le message global.
            if cle in ("non_field_errors", "__all__", "detail"):
                messages_globaux.extend(messages)
            else:
                champs[cle] = [_traduire(m) for m in messages]
        message = " ".join(_traduire(m) for m in messages_globaux)
        if not message:
            if champs:
                nb = len(champs)
                message = (
                    f"{nb} champ{'s' if nb > 1 else ''} à corriger — "
                    "les champs concernés sont signalés dans « champs »."
                )
            else:
                message = "Données soumises invalides."
        return _corps("VALIDATION_INVALIDE", message, champs)

    if isinstance(detail, (list, tuple)):
        message = " ".join(_traduire(m) for m in detail)
        return _corps("VALIDATION_INVALIDE", message or "Données soumises invalides.")

    return _corps("VALIDATION_INVALIDE", _traduire(detail))


# Classe d'exception DRF → (code stable, message français par défaut).
_ERREURS_STANDARDS = {
    "NotAuthenticated": (
        "NON_AUTHENTIFIE",
        "Vous devez être connecté pour accéder à cette ressource.",
    ),
    "AuthenticationFailed": (
        "AUTHENTIFICATION_INVALIDE",
        "Identifiants invalides.",
    ),
    "PermissionDenied": (
        "ACCES_REFUSE",
        "Vous n'avez pas la permission d'effectuer cette action.",
    ),
    "NotFound": (
        "INTROUVABLE",
        "Ressource introuvable.",
    ),
    "MethodNotAllowed": (
        "METHODE_NON_AUTORISEE",
        "Cette méthode HTTP n'est pas autorisée sur cette ressource.",
    ),
    "NotAcceptable": (
        "FORMAT_NON_ACCEPTABLE",
        "Aucun format de réponse compatible avec votre requête n'est disponible.",
    ),
    "UnsupportedMediaType": (
        "FORMAT_NON_SUPPORTE",
        "Le format de média envoyé n'est pas supporté.",
    ),
    "Throttled": (
        "LIMITE_ATTEINTE",
        "Trop de requêtes : veuillez patienter un instant avant de réessayer.",
    ),
}


def _message_francais(exc, message_defaut):
    """Choisit le message français pour une exception DRF standard.

    Règle : un message PERSONNALISÉ (déjà français dans ce code, ex.
    `permission_denied(message="Ce dossier ne vous appartient pas.")`)
    est conservé ; un message ÉGAL au défaut anglais de la classe (ou
    connu de la table de traduction) est traduit/remplacé.
    """
    detail = getattr(exc, "detail", None)
    if isinstance(detail, (list, tuple)):
        return " ".join(_traduire(m) for m in detail)
    if detail is None:
        return message_defaut

    texte = str(detail)
    defaut = str(getattr(type(exc), "default_detail", "")).strip()
    if texte.strip() != defaut and texte not in _TRADUCTIONS:
        # Message personnalisé : conservé tel quel.
        return texte
    return _traduire(texte) if texte in _TRADUCTIONS else message_defaut


def handler_erreurs(exc, contexte):
    """EXCEPTION_HANDLER DRF : toute erreur sort au format unifié, en français.

    Ordre de traitement :
      1. `ApiError` — nos erreurs métier (inconnues de DRF, il faut les
         capturer avant son propre handler) ;
      2. `ValidationError` Django remontée hors des vues qui la
         convertissent déjà : DRF ne la connaît pas (sinon → 500) ;
      3. handler DRF standard — s'il reconnaît l'exception, on reformate
         sa réponse dans l'enveloppe unifiée (traduction des messages
         standard, normalisation de la validation) ;
      4. exception non-API (bug, panne BDD…) : en production on renvoie
         une 500 générique SANS détail technique (pas de fuite
         d'information), l'exception complète étant journalisée côté
         serveur ; en DEBUG/tests on laisse remonter le comportement
         brut de Django pour ne pas masquer le diagnostic.
    """
    if isinstance(exc, ApiError):
        return Response(
            _corps(exc.code, exc.message, exc.champs, exc.contexte),
            status=exc.statut,
        )

    if isinstance(exc, ErreurValidationDjango) and not isinstance(
        exc, ErreurValidationDRF
    ):
        if hasattr(exc, "error_dict"):
            detail = {
                cle: [str(m) for m in messages]
                for cle, messages in exc.message_dict.items()
            }
        else:
            detail = [str(m) for m in exc.messages]
        return Response(
            _formater_validation(detail),
            status=http_status.HTTP_400_BAD_REQUEST,
        )

    # DRF reçoit parfois les exceptions Django brutes (Http404 d'un
    # get_object_or_404, PermissionDenied) : on les normalise en
    # équivalents DRF AVANT le handler standard, sinon le nom de classe
    # ne correspondrait à aucune entrée de _ERREURS_STANDARDS.
    if isinstance(exc, Http404):
        exc = NotFound()
    elif isinstance(exc, PermissionDenied) and not isinstance(
        exc, PermissionRefuseeDRF
    ):
        exc = PermissionRefuseeDRF()

    reponse = handler_drf(exc, contexte)
    if reponse is None:
        if settings.DEBUG or getattr(settings, "EXECUTION_TESTS", False):
            return None
        logger.exception("Erreur interne non gérée : %s", exc)
        return Response(
            _corps(
                "ERREUR_INTERNE",
                "Une erreur inattendue est survenue. "
                "Nos équipes ont été informées : veuillez réessayer plus tard.",
            ),
            status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Déjà au format unifié (double passage) : ne rien refaire.
    if isinstance(reponse.data, dict) and "code" in reponse.data \
            and "message" in reponse.data:
        return reponse

    if isinstance(exc, ErreurValidationDRF):
        reponse.data = _formater_validation(exc.detail)
        return reponse

    nom_classe = type(exc).__name__
    code, message_defaut = _ERREURS_STANDARDS.get(
        nom_classe, ("ERREUR_API", "La requête n'a pas pu être traitée.")
    )

    # Messages interpolés par DRF (throttle, média) : reconstruits en
    # français depuis les attributs de l'exception — la comparaison au
    # défaut anglais ne fonctionne pas pour eux (le défaut contient un
    # gabarit "{wait}", pas la valeur interpolée). Les autres messages
    # interpolés (méthode non autorisée…) sont déjà traduits en français
    # par Django/DRF (USE_I18N) et conservés tels quels.
    contexte_extra = {}
    force_defaut = False
    if code == "LIMITE_ATTEINTE":
        attente = getattr(exc, "wait", None)
        if attente:
            contexte_extra["attente_secondes"] = attente
            message_defaut = (
                f"Trop de requêtes : réessayez dans {attente} seconde"
                f"{'s' if attente > 1 else ''}."
            )
            force_defaut = True
    elif code == "FORMAT_NON_SUPPORTE" and getattr(exc, "media_type", None):
        message_defaut = f"Le format « {exc.media_type} » n'est pas supporté."
        force_defaut = True

    message = message_defaut if force_defaut else _message_francais(
        exc, message_defaut
    )
    reponse.data = _corps(code, message, contexte=contexte_extra or None)
    return reponse
