"""Services du domaine Dossiers : logique métier hors des vues."""

import hashlib
import hmac
import json
import logging
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

logger = logging.getLogger("pgnoc.dossiers")

_DUREE_VALIDITE_OTP = timedelta(minutes=5)
_ITERATIONS_PBKDF2 = 120_000
_TENTATIVES_OTP_MAX = 5

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
    total = 0
    remplis = 0
    for _champ, valeur in _champs_requis(dossier):
        total += 1
        if _est_rempli(_champ, valeur):
            remplis += 1

    if total == 0:
        return 0
    return round(remplis / total * 100)


def champs_manquants(dossier):
    """Champs obligatoires non renseignés de CE dossier, avec leur étape.

    Base UX des messages d'erreur de soumission : au lieu d'un « dossier
    incomplet » générique qui laisse l'investisseur chercher, on liste
    précisément ce qui bloque — le frontend peut le transformer en
    parcours guidé (« bouton Continuer → prochain champ à remplir »).

    Ordre : celui du parcours KYC (étape puis champ), pour que la liste
    reflète l'ordre naturel de remplissage.
    """
    manquants = []
    for champ, valeur in _champs_requis(dossier):
        if not _est_rempli(champ, valeur):
            manquants.append(
                {
                    "etape": champ.etape.nom,
                    "champ": champ.nom,
                    "code": champ.code,
                }
            )
    return manquants


def _champs_requis(dossier):
    """Itère (champ, valeur_saisie) sur les champs exigés de CE dossier.

    Base commune de `calculer_progression_pct` (compter) et
    `champs_manquants` (guider) : une seule définition de « ce qui est
    requis », sinon les deux dériveraient tôt ou tard.

    Un champ est exigé s'il est obligatoire, actif, dans une étape active
    de la SGI, et si sa condition parent (le cas échéant) est déclenchée
    par la valeur saisie dans CE dossier.
    """
    champs = (
        ChampKYC.objects
        .filter(
            etape__sgi_id=dossier.sgi_id,
            etape__actif=True,
            actif=True,
            obligatoire=True,
        )
        .select_related("etape", "champ_parent")
    )

    valeurs = {
        vc.champ_id: vc
        for vc in ValeurChamp.objects.filter(dossier_id=dossier.id)
    }

    for champ in champs:
        if champ.champ_parent_id:
            parent = valeurs.get(champ.champ_parent_id)
            if parent is None or parent.valeur != champ.valeur_declencheur:
                # Condition non déclenchée par le parent : champ non requis.
                continue
        yield champ, valeurs.get(champ.id)


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

    Le code est acheminé par email (tâche `envoyer_email_code_otp`,
    canal hors-bande) ; l'API ne le renvoie en clair qu'en
    développement (``DEBUG=True``).

    Retourne le code en clair (uniquement pour l'acheminement DEBUG).
    """
    code = f"{secrets.randbelow(1_000_000):06d}"
    sel = secrets.token_hex(16)
    # Le sel (non confidentiel) est préfixé au hash : nécessaire pour
    # re-vérifier le code sans stocker le code en clair.
    dossier.otp_hash = f"{sel}:{pbkdf2(code, sel, _ITERATIONS_PBKDF2, 32, hashlib.sha256).hex()}"
    dossier.otp_expiration = timezone.now() + _DUREE_VALIDITE_OTP
    dossier.otp_tentatives = 0
    dossier.save(update_fields=["otp_hash", "otp_expiration", "otp_tentatives"])

    # Acheminement hors-bande (best-effort : un échec d'email ne bloque
    # pas la génération, l'investisseur peut en demander un nouveau).
    from notifications.tasks import envoyer_email_code_otp

    try:
        transaction.on_commit(
            lambda: envoyer_email_code_otp.delay(str(dossier.pk), code)
        )
    except Exception:
        logger.exception("Dispatch email OTP impossible (broker indisponible ?)")

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
        dossier.save(update_fields=["otp_hash", "otp_expiration", "otp_tentatives"])
        raise ValidationError(
            _("Le code OTP a expiré : générez-en un nouveau avant de signer.")
        )

    # Vérification en temps constant (anti timing-attack).
    sel, hash_stocke = dossier.otp_hash.split(":", 1)
    if not constant_time_compare(
        hash_stocke,
        pbkdf2(code_otp, sel, _ITERATIONS_PBKDF2, 32, hashlib.sha256).hex(),
    ):
        # Anti brute-force : au bout de N codes erronés sur un même OTP,
        # il est purgé — il faut en générer un nouveau (throttle 10/min
        # en amont, ~50 essais max par code, insuffisant seul).
        dossier.otp_tentatives = (dossier.otp_tentatives or 0) + 1
        if dossier.otp_tentatives >= _TENTATIVES_OTP_MAX:
            _purger_otp(dossier)
            dossier.save(update_fields=["otp_hash", "otp_expiration", "otp_tentatives"])
            raise ValidationError(
                _("Trop de codes erronés : générez un nouveau code OTP.")
            )
        dossier.save(update_fields=["otp_tentatives"])
        raise ValidationError(_("Code OTP invalide."))

    ip = _adresse_ip(requete)
    horodatage = timezone.now()
    empreinte = _empreinte_contenu(dossier)
    preuve_hash = hashlib.sha256(
        "|".join([
            dossier.reference,
            str(dossier.utilisateur_id),
            str(dossier.sgi_id),
            horodatage.isoformat(),
            ip or "0.0.0.0",
            code_otp,
            # Empreinte du CONTENU signé : la preuve couvre l'intégralité
            # des valeurs du dossier au moment de la signature. Toute
            # modification ultérieure d'une valeur rend la preuve
            # incohérente avec le contenu (vérifiée à la validation).
            empreinte,
        ]).encode("utf-8")
    ).hexdigest()
    # L'empreinte du contenu est embarquée en clair dans la preuve : la
    # validation peut la comparer au contenu réellement instruit sans
    # connaître le code OTP (purgé).
    preuve = f"sha256:{preuve_hash}|contenu:{empreinte}"

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
    dossier.otp_tentatives = 0


def empreinte_contenu(dossier):
    """Empreinte SHA-256 publique de l'ensemble des valeurs du dossier.

    Voir `_empreinte_contenu` : exposée sous ce nom pour le workflow
    (validation de la preuve de signature au moment de la décision).
    """
    return _empreinte_contenu(dossier)


def _empreinte_contenu(dossier):
    """Empreinte SHA-256 de l'ensemble des valeurs du dossier.

    Chaque ligne ``ValeurChamp`` contribue au hash trié par identifiant
    de champ (ordre stable) : toute valeur saisie, remplacée ou supprimée
    change l'empreinte, et donc la cohérence de la preuve de signature.
    Les fichiers sont couverts par leur référence de stockage (le
    contenu binaire est déjà couvert par `empreinte_sha256` du selfie).
    """
    lignes = (
        ValeurChamp.objects.filter(dossier_id=dossier.pk)
        .order_by("champ_id")
        .values_list("champ_id", "valeur", "fichier")
    )
    payload = "\n".join(f"{c}|{v or ''}|{f or ''}" for c, v, f in lignes)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _adresse_ip(requete):
    """Délègue au helper partagé (comportement anti-spoofing unique)."""
    if requete is None:
        return None
    from audit.services import adresse_ip_client

    return adresse_ip_client(requete)


def poser_signature_resoumission(dossier, requete=None):
    """Re-scelle automatiquement la preuve d'un dossier CORRIGÉ.

    Appelé par le workflow lors de la résoumission (REJETE → SOUMIS) :
    l'investisseur a déjà prouvé son identité par OTP à la première
    signature, et c'est le geste même de résoumission qui scelle le
    contenu corrigé. Aucun nouveau code n'est demandé.

    La preuve emprunte exactement le format OTP
    (`sha256:<hash>|contenu:<empreinte>`) : la vérification VALIDE compare
    l'empreinte du contenu instruit à celle embarquée dans la preuve — le
    contenu corrigé est ainsi couvert, la réutilisation d'une preuve issue
    d'une autre version restant impossible.
    """
    horodatage = timezone.now()
    ip = _adresse_ip(requete)
    empreinte = _empreinte_contenu(dossier)
    preuve_hash = hashlib.sha256(
        "|".join([
            dossier.reference,
            str(dossier.utilisateur_id),
            str(dossier.sgi_id),
            horodatage.isoformat(),
            ip or "0.0.0.0",
            "resoumission:" + secrets.token_hex(8),
            empreinte,
        ]).encode("utf-8")
    ).hexdigest()
    preuve = f"sha256:{preuve_hash}|contenu:{empreinte}"

    dossier.type_signature = Dossier.TypeSignature.OTP
    dossier.donnee_signature = preuve
    dossier.date_signature = horodatage
    dossier.ip_signature = ip
    dossier.save(update_fields=[
        "type_signature", "donnee_signature", "date_signature", "ip_signature",
    ])

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
            "date_signature": horodatage.isoformat(),
            "ip_signature": ip,
        },
        requete=requete,
    )


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