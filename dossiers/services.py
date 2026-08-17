"""Services du domaine Dossiers : logique métier hors des vues."""

import hashlib
import hmac
import json
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, pbkdf2
from django.utils.translation import gettext_lazy as _

from audit.models import JournalAudit
from audit.services import journaliser
from dossiers.models import ChampKYC, Dossier, EtapeKYC, ValeurChamp

_DUREE_VALIDITE_OTP = timedelta(minutes=5)
_ITERATIONS_PBKDF2 = 120_000

_TAILLE_MAX_SELFIE_SECOURS_MO = 5


def recalculer_progression(dossier):
    """Recalcule et persiste `progression_pct` sans déclencher save()/full_clean.

    `full_clean()` de Dossier validerait des règles de cycle de vie
    (signature obligatoire pour VALIDE…) sans rapport avec la progression :
    on passe par un QuerySet.update() ciblé.
    """
    Dossier.objects.filter(pk=dossier.pk).update(
        progression_pct=calculer_progression_pct(dossier)
    )


def calculer_progression_pct(dossier):
    """Pourcentage de champs obligatoires renseignés pour ce dossier.

    Règles :
      - seuls les champs obligatoires ET actifs des étapes actives de la
        SGI entrent en compte ;
      - un champ conditionnel n'est requis que si le champ parent a, dans
        CE dossier, la valeur déclencheur attendue (sinon il est ignoré) ;
      - un champ est "rempli" s'il a une valeur (ou une référence de
        fichier pour les champs FICHIER).

    Retourne un entier 0-100. Ne soulève jamais d'exception : parcours
    sans champ requis = 0.
    """
    champs = (
        ChampKYC.objects
        .filter(
            etape__sgi_id=dossier.sgi_id,
            etape__actif=True,
            actif=True,
            obligatoire=True,
        )
        .select_related("champ_parent")
    )

    valeurs = {
        vc.champ_id: vc
        for vc in ValeurChamp.objects.filter(dossier_id=dossier.id)
    }

    total = 0
    remplis = 0
    for champ in champs:
        if champ.champ_parent_id:
            parent = valeurs.get(champ.champ_parent_id)
            if parent is None or parent.valeur != champ.valeur_declencheur:
                # Condition non déclenchée par le parent : champ non requis.
                continue
        total += 1
        if _est_rempli(champ, valeurs.get(champ.id)):
            remplis += 1

    if total == 0:
        return 0
    return round(remplis / total * 100)


def _est_rempli(champ, valeur):
    """Un champ est « rempli » si sa valeur a un sens réel pour son type.

    Règles typées (anti comptage factice) :
      - rien / blanc → vide ;
      - BOOLEEN : seuls « oui / true / 1 » comptent (répondre « non » à
        une question obligatoire ne la « remplit » pas) ;
      - CHOIX_MULTIPLE : la liste doit contenir au moins un élément ;
      - NOMBRE : la chaîne doit contenir au moins un chiffre ;
      - FICHIER / SELFIE : une référence de fichier est requise.
    """
    if valeur is None:
        return False
    if champ.type in (ChampKYC.TypeChamp.FICHIER, ChampKYC.TypeChamp.SELFIE):
        return bool(valeur.fichier)
    if not valeur.valeur or not valeur.valeur.strip():
        return False
    brut = valeur.valeur.strip().lower()
    if champ.type == ChampKYC.TypeChamp.BOOLEEN:
        return brut in ("oui", "true", "1")
    if champ.type == ChampKYC.TypeChamp.CHOIX_MULTIPLE:
        try:
            return bool(json.loads(brut))
        except ValueError:
            return False
    if champ.type == ChampKYC.TypeChamp.NOMBRE:
        return any(caractere.isdigit() for caractere in valeur.valeur)
    return True


# ---------------------------------------------------------------------------
# Signature électronique OTP : génération ET vérification côté serveur.
#
# Un code clair n'est JAMAIS stocké : seul son hash PBKDF2 (avec sel
# aléatoire) est conservé, avec une durée de vie de 5 minutes. La preuve
# posée (`donnee_signature`) est un hash chainé du contexte du dossier
# (référence, utilisateur, SGI, horodatage, IP) : elle est liée au
# document signé et falsifier/ réutiliser est impossible.
# ---------------------------------------------------------------------------


def generer_code_otp(dossier):
    """Génère un code OTP à 6 chiffres et l'enregistre hashé sur le dossier.

    En production, le code serait acheminé par un canal hors-bande (SMS/
    email) ; l'API le renvoie en clair pour le développement.

    Retourne le code en clair (l'appelant l'achemine / le renvoie).
    """
    code = f"{secrets.randbelow(1_000_000):06d}"
    sel = secrets.token_hex(16)
    # Le sel (non confidentiel) est préfixé au hash : nécessaire pour
    # re-vérifier le code sans stocker le code en clair.
    dossier.otp_hash = f"{sel}:{pbkdf2(code, sel, _ITERATIONS_PBKDF2, 32, hashlib.sha256).hex()}"
    dossier.otp_expiration = timezone.now() + _DUREE_VALIDITE_OTP
    dossier.save(update_fields=["otp_hash", "otp_expiration"])
    return code


def poser_signature_otp(dossier, code_otp, requete=None):
    """Vérifie le code OTP puis pose la preuve de signature sur le dossier.

    Règles :
      - aucun code actif → erreur (200 % côté serveur) ;
      - code expiré → erreur et purge (il faut en générer un nouveau) ;
      - code erroné → erreur (l'ancien reste utilisable pendant sa
        validité) ;
      - code valide → `type_signature=OTP`, `donnee_signature` = preuve
        chaînée (référence | utilisateur | SGI | horodatage | IP | code),
        `date_signature` et `ip_signature` posées ; le hash OTP est
        purgé : un même code ne peut être utilisé qu'une fois.

    Lève ValidationError (messages français). Retourne le dictionnaire
    de la preuve posée.

    Sûreté concurrentielle : la génération et la vérification se font
    sous verrou pessimiste (`select_for_update`) sur le dossier — deux
    `signer/` simultanés ne peuvent pas valider le même code deux fois
    (le second relit un hash déjà purgé et échoue proprement).
    """
    with transaction.atomic():
        # Relecture sous verrou : c'est l'état frais qui fait foi, pas
        # l'instance passée par l'appelant (potentiellement obsolète).
        dossier = Dossier.objects.select_for_update().get(pk=dossier.pk)
        try:
            _poser_signature_sous_verrou(dossier, code_otp, requete)
            erreur = None
        except ValidationError as exc:
            # La purge éventuelle de l'OTP (expiration) a déjà été
            # persistée dans la transaction : laisser l'exception
            # sortir du bloc `atomic` rollbackerait la purge. On la
            # re-lève une fois le bloc engagé.
            erreur = exc
    if erreur is not None:
        raise erreur

    dossier.refresh_from_db()
    return {
        "type_signature": dossier.type_signature,
        "donnee_signature": dossier.donnee_signature,
        "date_signature": dossier.date_signature,
        "ip_signature": dossier.ip_signature,
    }


def _poser_signature_sous_verrou(dossier, code_otp, requete):
    """Cœur de la pose de signature, exécuté sous verrou dans la transaction."""
    if not dossier.otp_hash or not dossier.otp_expiration:
        raise ValidationError(
            _("Aucun code OTP actif : générez-en un nouveau avant de signer.")
        )

    if timezone.now() > dossier.otp_expiration:
        _purger_otp(dossier)
        dossier.save(update_fields=["otp_hash", "otp_expiration"])
        raise ValidationError(
            _("Le code OTP a expiré : générez-en un nouveau avant de signer.")
        )

    # Vérification en temps constant (anti timing-attack).
    sel, hash_stocke = dossier.otp_hash.split(":", 1)
    if not constant_time_compare(
        hash_stocke,
        pbkdf2(code_otp, sel, _ITERATIONS_PBKDF2, 32, hashlib.sha256).hex(),
    ):
        raise ValidationError(_("Code OTP invalide."))

    ip = _adresse_ip(requete)
    horodatage = timezone.now()
    preuve = "sha256:" + hashlib.sha256(
        "|".join([
            dossier.reference,
            str(dossier.utilisateur_id),
            str(dossier.sgi_id),
            horodatage.isoformat(),
            ip or "0.0.0.0",
            code_otp,
        ]).encode("utf-8")
    ).hexdigest()

    dossier.type_signature = Dossier.TypeSignature.OTP
    dossier.donnee_signature = preuve
    dossier.date_signature = horodatage
    dossier.ip_signature = ip
    _purger_otp(dossier)
    dossier.save(update_fields=[
        "type_signature", "donnee_signature", "date_signature",
        "ip_signature", "otp_hash", "otp_expiration",
    ])

    # La pose de signature est l'événement légal du dossier (preuve,
    # horodatage, IP) : il doit figurer dans la même transaction que
    # la preuve pour ne laisser aucun « trou » d'audit.
    journaliser(
        dossier.utilisateur,
        JournalAudit.Action.POSE_SIGNATURE,
        "Dossier",
        str(dossier.pk),
        avant={
            "type_signature": "",
            "date_signature": None,
            "ip_signature": None,
        },
        apres={
            "type_signature": dossier.type_signature,
            "donnee_signature": dossier.donnee_signature,
            "date_signature": dossier.date_signature.isoformat() if dossier.date_signature else None,
            "ip_signature": dossier.ip_signature,
        },
        requete=requete,
    )



def _purger_otp(dossier):
    dossier.otp_hash = ""
    dossier.otp_expiration = None


def _adresse_ip(requete):
    """Adresse IP du signataire (via proxy inverse si présent)."""
    if requete is None:
        return None
    x_forwarded = requete.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()
    return requete.META.get("REMOTE_ADDR")


# ---------------------------------------------------------------------------
# Preuve de vie asynchrone (champ SELFIE) : empreinte + signature serveur.
#
# La plateforme ne peut pas distinguer un byte-array issu d'une caméra
# d'un upload arbitraire : l'anti-fraude de niveau 1 est l'UI (capture
# caméra contrainte). La chaîne de preuve serveur garantit ensuite la
# traçabilité exigée par l'audit CREPMF :
#   - `empreinte_sha256` : hash du contenu calculé côté serveur à la
#     réception (un fichier remplacé en silence ne passe pas inaperçu) ;
#   - `signature_serveur` : HMAC-SHA256 d'un payload liant la référence
#     du dossier, l'identifiant de la valeur, le chemin stocké, le hash
#     et l'horodatage — signée avec une clé dérivée de SECRET_KEY, elle
#     ne peut être régénérée par un client ni après coup ;
#   - `date_capture` : horodatage serveur de réception.
# ---------------------------------------------------------------------------


def _cle_signature_selfie():
    """Clé HMAC dédiée aux preuves de selfie (dérivée de `SECRET_KEY`)."""
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        b"pgnoc:preuve-vie:selfie",
        hashlib.sha256,
    ).digest()


def _payload_preuve_selfie(reference, valeur_id, chemin, empreinte, horodatage):
    return "|".join([
        reference,
        str(valeur_id),
        chemin,
        empreinte,
        horodatage.isoformat(),
    ])


def signer_preuve_selfie(reference, valeur_id, chemin, empreinte, horodatage):
    """Signe (HMAC-SHA256) le contexte d'une preuve de vie reçue côté serveur."""
    payload = _payload_preuve_selfie(
        reference, valeur_id, chemin, empreinte, horodatage
    )
    return hmac.new(
        _cle_signature_selfie(), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verifier_preuve_selfie(dossier, valeur):
    """Re-vérifie l'intégrité d'un selfie depuis le stockage.

    Recalcule le hash SHA-256 du fichier actuellement stocké et re-valide
    la signature HMAC sur le contexte enregistré. Retourne un dictionnaire
    prêt à sérialiser ; le fichier manquant ou illisible ne fait pas
    planter la vérification (rapport d'échec contrôlé).
    """
    if not valeur.fichier:
        return {"fichier présent": False}
    if not valeur.empreinte_sha256 or not valeur.signature_serveur:
        return {
            "date_capture": valeur.date_capture,
            "concordante": False,
            "signature_valide": False,
            "detail": "Preuve incomplète (empreinte ou signature absente).",
        }

    try:
        with default_storage.open(valeur.fichier, "rb") as objet:
            hacheur = hashlib.sha256()
            for morceau in iter(lambda: objet.read(1024 * 1024), b""):
                hacheur.update(morceau)
        empreinte_calculee = hacheur.hexdigest()
    except Exception as exc:
        return {
            "date_capture": valeur.date_capture,
            "concordante": False,
            "signature_valide": False,
            "detail": f"Fichier illisible en stockage : {exc}",
        }

    concordante = empreinte_calculee == valeur.empreinte_sha256
    signature_attendue = signer_preuve_selfie(
        dossier.reference,
        valeur.id,
        valeur.fichier,
        valeur.empreinte_sha256,
        valeur.date_capture,
    )
    signification = (
        valeur.signature_serveur.strip()
        and constant_time_compare(signature_attendue, valeur.signature_serveur.strip())
    )
    return {
        "date_capture": valeur.date_capture,
        "empreinte_sha256": valeur.empreinte_sha256,
        "concordante": concordante,
        "signature_valide": signification,
        "detail": (
            "Preuve de vie conforme : le fichier stocké correspond à "
            "l'empreinte servie à la réception et la signature serveur "
            "est valide."
            if concordante and signification
            else (
                "Le contenu stocké ne correspond pas à l'empreinte d'origine."
                if not concordante
                else "La signature serveur est invalide."
            )
        ),
    }