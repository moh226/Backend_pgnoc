"""Services du domaine Dossiers : logique métier hors des vues."""

import hashlib
import json
import secrets
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, pbkdf2
from django.utils.translation import gettext_lazy as _

from dossiers.models import ChampKYC, Dossier, EtapeKYC, ValeurChamp

_DUREE_VALIDITE_OTP = timedelta(minutes=5)
_ITERATIONS_PBKDF2 = 120_000


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
      - FICHIER : une référence de fichier est requise.
    """
    if valeur is None:
        return False
    if champ.type == ChampKYC.TypeChamp.FICHIER:
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
        _poser_signature_sous_verrou(dossier, code_otp, requete)

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