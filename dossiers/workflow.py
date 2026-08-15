"""Machine à états du cycle de vie d'un dossier (section 7 du document de conception).

Conventions :
  - toute transition passe par `transiter()` ; il est interdit de poser
    un `statut` directement ailleurs qu'en création (BROUILLON par défaut) ;
  - `transiter()` valide la transition, ses préconditions métier, pose
    les horodatages et réinitialise les champs obsolètes, puis persiste
    via `save(update_fields=...)` (le `full_clean()` du modèle vérifie
    par ailleurs la cohérence statut/signature).
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from audit.services import journaliser
from audit.models import JournalAudit
from dossiers.models import Dossier
from dossiers.services import calculer_progression_pct
from notifications.services import notifier_transition

TRANSITIONS = {
    Dossier.Statut.BROUILLON: {Dossier.Statut.SOUMIS},
    Dossier.Statut.SOUMIS: {Dossier.Statut.EN_INSTRUCTION},
    Dossier.Statut.EN_INSTRUCTION: {Dossier.Statut.VALIDE, Dossier.Statut.REJETE},
    Dossier.Statut.REJETE: {Dossier.Statut.SOUMIS},
    Dossier.Statut.VALIDE: set(),  # état terminal : aucun retour possible
}


def transiter(dossier,
              nouveau_statut, *,
              agent=None,
              motif_rejet="",
              utilisateur=None,
              requete=None):
    """Applique une transition d'état sur `dossier` (préconditions + effets).

    Paramètres :
      - `agent` : Utilisateur AGENT_SGI qui prend en charge (SOUMIS → EN_INSTRUCTION).
      - `motif_rejet` : motif obligatoire pour EN_INSTRUCTION → REJETE.
      - `utilisateur` : auteur de l'action (request.user côté API) ;
        défaut : le propriétaire du dossier.
      - `requete` : requête HTTP pour les métadonnées légales du journal
        d'audit (IP, User-Agent).

    Lève ValidationError (messages français) sur toute transition
    illégale ou précondition non remplie. Chaque transition réussie est
    tracée dans le journal d'audit (consultable en étape 3B).

    Sûreté concurrentielle : la transition s'exécute dans une
    transaction atomique sous VERROU PESSIMISTE (`select_for_update`)
    sur le dossier. Deux requêtes simultanées (double soumission, double
    prise en charge) sont sérialisées : la seconde relit l'état déjà
    modifié et échoue proprement au lieu de créer une double mutation.
    L'écriture du journal d'audit est dans la même transaction que la
    mutation : aucun « trou » de preuve entre la décision et sa trace.
    """
    with transaction.atomic():
        # Relecture sous verrou : c'est l'état frais qui fait foi, pas
        # l'instance passée par l'appelant (potentiellement obsolète).
        verrouille = Dossier.objects.select_for_update().get(pk=dossier.pk)
        _appliquer_transition(
            verrouille, nouveau_statut,
            agent=agent,
            motif_rejet=motif_rejet,
            utilisateur=utilisateur,
            requete=requete,
        )
    # La transaction est engagée ; on synchronise l'instance de
    # l'appelant puis on alerte les destinataires (effet de bord hors
    # transaction, best-effort).
    dossier.refresh_from_db()
    notifier_transition(dossier, nouveau_statut)


def _appliquer_transition(dossier,
                          nouveau_statut, *,
                          agent=None,
                          motif_rejet="",
                          utilisateur=None,
                          requete=None):
    """Cœur de transition, exécuté sous verrou dans la transaction."""

    if dossier.statut == nouveau_statut:
        raise ValidationError(
            _("Le dossier est déjà à l'état « %(statut)s ».")
            % {"statut": dossier.get_statut_display()}
        )

    autorises = TRANSITIONS.get(dossier.statut, set())
    if nouveau_statut not in autorises:
        raise ValidationError(
            _("Transition interdite : « %(actuel)s » → « %(demande)s ».")
            % {
                "actuel": dossier.get_statut_display(),
                "demande": dict(Dossier.Statut.choices)[nouveau_statut],
            }
        )

    avant = _snapshot(dossier)

    if nouveau_statut == Dossier.Statut.SOUMIS:
        _verifier_avant_soumission(dossier)
        dossier.date_soumission = timezone.now()
        dossier.etape_courante = None
        # La configuration KYC (étapes/champs/obligatoire) a pu changer
        # depuis la dernière saisie : on fige la progression au moment de
        # la soumission pour que l'affichage en file d'attente reste juste.
        dossier.progression_pct = calculer_progression_pct(dossier)
        if dossier.statut == Dossier.Statut.REJETE:
            # Resoumission après rejet : nouvelle version, l'ancien agent
            # est libéré, l'historique de décision et le motif d'ancien
            # rejet sont remis à zéro (le dossier resoumis ne doit plus
            # afficher l'ancien motif).
            dossier.version += 1
            dossier.agent = None
            dossier.date_instruction = None
            dossier.date_decision = None
            dossier.motif_rejet = ""

    elif nouveau_statut == Dossier.Statut.EN_INSTRUCTION:
        _verifier_prise_en_charge(dossier, agent)
        dossier.agent = agent
        dossier.date_instruction = timezone.now()

    elif nouveau_statut == Dossier.Statut.VALIDE:
        _verifier_decision(dossier, agent)
        if not (dossier.type_signature and dossier.donnee_signature and dossier.date_signature):
            raise ValidationError(
                _("Un dossier ne peut être validé sans signature électronique complète.")
            )
        dossier.date_decision = timezone.now()

    elif nouveau_statut == Dossier.Statut.REJETE:
        _verifier_decision(dossier, agent)
        if not motif_rejet.strip():
            raise ValidationError(_("Le motif de rejet est obligatoire."))
        dossier.motif_rejet = motif_rejet.strip()
        dossier.date_decision = timezone.now()

    dossier.statut = nouveau_statut
    dossier.save(update_fields=champs_effectivement_modifies(dossier))

    # La transition étant réussie et persistée, on la fige dans le
    # journal d'audit (état avant / après) — dans la même transaction,
    # sous le même verrou : décision et preuve ne peuvent se désyncrer.
    journaliser(
        utilisateur or dossier.utilisateur,
        JournalAudit.Action.TRANSITION_DOSSIER,
        "Dossier",
        str(dossier.pk),
        avant=avant,
        apres=_snapshot(dossier),
        requete=requete,
    )


def _snapshot(dossier):
    """Vue JSON-sérialisable des informations de cycle de vie d'un dossier."""
    return {
        "reference": dossier.reference,
        "statut": dossier.statut,
        "version": dossier.version,
        "agent_id": str(dossier.agent_id) if dossier.agent_id else None,
        "motif_rejet": dossier.motif_rejet or "",
        "date_soumission": (
            dossier.date_soumission.isoformat() if dossier.date_soumission else None
        ),
        "date_instruction": (
            dossier.date_instruction.isoformat() if dossier.date_instruction else None
        ),
        "date_decision": (
            dossier.date_decision.isoformat() if dossier.date_decision else None
        ),
    }


def champs_effectivement_modifies(dossier):
    """Compare l'état en mémoire aux valeurs en base pour `update_fields`.

    'save (update_fields=...)` n'écrit QUE les champs listés : on liste
    donc uniquement ceux que `transiter()` a vraiment modifiés, en les
    comparant aux valeurs persistées en base.
    """
    candidats = [
        "statut", "version", "etape_courante", "agent", "progression_pct",
        "date_soumission", "date_instruction", "date_decision", "motif_rejet",
    ]
    en_base = Dossier.objects.only(*candidats).get(pk=dossier.pk)
    modifie = [
        champ for champ in candidats
        if getattr(dossier, champ) != getattr(en_base, champ)
    ]
    if not modifie:
        # Sauvegarder sans champ modifié n'est pas accepté par Django
        # (ValueError) : on renvoie au minimum le statut.
        modifie = ["statut"]
    return modifie


def _verifier_avant_soumission(dossier):
    """UC10 : un dossier incomplet ne peut pas être soumis.

    Préconditions :
      - progression 100 % (tous les champs obligatoires renseignés) ;
      - SGI destinataire active ;
      - signature électronique posée (UC17) : le dossier entre dans le
        circuit d'instruction avec sa preuve de signature — il est figé
        ensuite (`signer/` refuse tout dossier non BROUILLON/REJETE),
        donc la signature doit avoir été posée AVANT la soumission ;
      - dès lors que la SGI a publié sa convention tarifaire,
        l'investisseur doit l'avoir acceptée avant soumission. Tant
        qu'aucune convention n'est publiée, aucun accord n'est exigé.
    """
    if calculer_progression_pct(dossier) < 100:
        raise ValidationError(
            _("Le dossier ne peut être soumis : des champs obligatoires restent à renseigner.")
        )

    if not (dossier.type_signature and dossier.donnee_signature and dossier.date_signature):
        raise ValidationError(
            _("Le dossier ne peut être soumis sans signature électronique : "
              "générez un code OTP et signez avant de soumettre.")
        )

    if not dossier.sgi.est_active:
        raise ValidationError(
            _("Cette SGI est temporairement suspendue : aucun dossier "
              "ne peut être soumis en ce moment.")
        )

    published = dossier.sgi.convention if hasattr(dossier.sgi, "convention") else None
    if published and published.fichier_pdf:
        if not dossier.convention_acceptee:
            raise ValidationError(
                _("Vous devez accepter la convention tarifaire de la SGI "
                  "avant de soumettre votre dossier.")
            )


def _verifier_prise_en_charge(dossier, agent):
    """UC14 : l'agent doit appartenir à la SGI destinataire du dossier."""
    if agent is None:
        raise ValidationError(_("Un agent SGI doit être désigné pour prendre en charge le dossier."))
    if agent.sgi_id != dossier.sgi_id:
        raise ValidationError(_("Cet agent n'appartient pas à la SGI destinataire du dossier."))


def _verifier_decision(dossier, agent):
    """UC14 : la décision est liée à l'instruction.

    Seul l'agent qui a pris en charge le dossier (et donc l'a instruit)
    peut le valider ou le rejeter. Un admin SGI de la même SGI garde la
    main pour superviser, mais aucun agent tiers ne peut décider à la
    place de son collègue.
    """
    if agent is None:
        raise ValidationError(_("Un agent SGI doit être désigné pour rendre la décision."))
    if agent.sgi_id != dossier.sgi_id:
        raise ValidationError(_("Cet agent n'appartient pas à la SGI destinataire du dossier."))
    if not agent.est_admin_sgi and dossier.agent_id != agent.id:
        raise ValidationError(
            _("Seul l'agent qui a pris en charge le dossier peut rendre la décision.")
        )