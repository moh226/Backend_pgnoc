"""Preuve de dépôt minimum : parcours investisseur et vérification SGI.

Post-validation (après la décision VALIDE) :
  - l'investisseur consulte l'exigence (montant, instructions, méthodes)
    et dépose une preuve (image/PDF + référence de transaction) ;
  - le personnel SGI vérifie la preuve et l'approuve ou la rejette ;
  - l'approbation déclenche la transition VALIDE → ACTIF (ouverture du
    compte-titres) via la machine à états (`transiter`), qui vérifie
    elle-même que la preuve est approuvée quand la SGI exige un dépôt.

Le cloisonnement par SGI est impératif (mêmes règles que le circuit
d'instruction) : un agent n'accède jamais aux dépôts d'une autre SGI.
"""

import logging
import uuid

from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, parsers, permissions, serializers as drf_serializers
from rest_framework import status
from rest_framework.response import Response

logger = logging.getLogger("pgnoc.dossiers")

from audit.models import JournalAudit
from audit.services import journaliser
from comptes.permissions import EstInvestisseur, EstPersonnelSGI
from dossiers.mixins import DossierProprietaireMixin
from dossiers.models import DepotMinimum, Dossier
from dossiers.serializers_depot import (
    DepotMinimumSerializer,
    DepotPreuveSerializer,
)
from dossiers.workflow import _servir_depot, transiter
from notifications.tasks import (
    envoyer_email_depot_preuve_deposee,
    envoyer_email_depot_verifie,
    notifier_depot_preuve_deposee_task,
    notifier_depot_verifie_task,
)
from pgnoc.erreurs import erreur


def _depot_autorise(request, view, depot_pk):
    """Récupère un dépôt et vérifie qu'il appartient à la SGI du personnel."""
    depot = get_object_or_404(
        DepotMinimum.objects.select_related("dossier__utilisateur", "dossier__sgi"),
        pk=depot_pk,
        dossier__sgi_id=request.user.sgi_id,
    )
    return depot


class DepotMinimumInvestisseurAPIView(DossierProprietaireMixin, generics.GenericAPIView):
    """Dépôt minimum post-validation : consultation et dépôt de la preuve.

    GET  /api/dossiers/dossiers/<dossier_pk>/depot-minimum/
    POST /api/dossiers/dossiers/<dossier_pk>/depot-minimum/   (multipart)

    Accès restreint au propriétaire du dossier. L'exigence est créée à
    la validation (`workflow`), et matérialisée au premier accès si la
    SGI a activé l'exigence entre-temps (`_servir_depot`).

    État machine du dépôt : EN_ATTENTE → PREUVE_DEPOSEE (investisseur)
    → APPROUVE/REJETE (personnel SGI). Après rejet, l'investisseur
    redépose une nouvelle preuve (RETOUR à PREUVE_DEPOSEE).
    """

    serializer_class = DepotMinimumSerializer
    permission_classes = (permissions.IsAuthenticated, EstInvestisseur)

    def get(self, request, dossier_pk):
        dossier = self.get_dossier()
        if dossier.statut not in (
            Dossier.Statut.VALIDE,
            Dossier.Statut.ACTIF,
        ):
            return erreur(
                "STATUT_INCOMPATIBLE",
                (
                    f"Le dépôt minimum est consultable après validation du "
                    f"dossier : le vôtre est « {dossier.get_statut_display()} »."
                ),
                status.HTTP_409_CONFLICT,
                statut_actuel=dossier.statut,
            )

        depot, _cree = _servir_depot(dossier)
        if depot is None:
            return erreur(
                "DEPOT_NON_EXIGE",
                "La SGI n'exige pas de dépôt minimum pour ce dossier.",
                status.HTTP_404_NOT_FOUND,
            )

        return Response(DepotMinimumSerializer(depot).data, status=status.HTTP_200_OK)

    @extend_schema(
        request=inline_serializer(
            "DepotPreuveEntree",
            {
                "preuve": drf_serializers.FileField(),
                "montant_depose": drf_serializers.DecimalField(
                    max_digits=14, decimal_places=0
                ),
                "methode_paiement": drf_serializers.CharField(),
                "reference_transaction": drf_serializers.CharField(),
            },
        ),
        responses={200: DepotMinimumSerializer},
    )
    def post(self, request, dossier_pk):
        dossier = self.get_dossier()
        if dossier.statut != Dossier.Statut.VALIDE:
            return erreur(
                "STATUT_INCOMPATIBLE",
                (
                    f"La preuve de dépôt ne peut être déposée que pour un "
                    f"dossier VALIDÉ : le vôtre est "
                    f"« {dossier.get_statut_display()} »."
                ),
                status.HTTP_409_CONFLICT,
                statut_actuel=dossier.statut,
            )

        depot, _cree = _servir_depot(dossier)
        if depot is None:
            return erreur(
                "DEPOT_NON_EXIGE",
                "La SGI n'exige pas de dépôt minimum pour ce dossier.",
                status.HTTP_404_NOT_FOUND,
            )

        if depot.statut not in (
            DepotMinimum.Statut.EN_ATTENTE,
            DepotMinimum.Statut.REJETE,
        ):
            return erreur(
                "DEPOT_DEJA_TRAITE",
                (
                    f"Ce dépôt est déjà traité (statut actuel : "
                    f"« {depot.get_statut_display()} ») : aucune nouvelle "
                    "preuve n'est attendue."
                ),
                status.HTTP_409_CONFLICT,
                statut_depot=depot.statut,
            )

        serializer = DepotPreuveSerializer(data=request.data, context={"depot": depot})
        serializer.is_valid(raise_exception=True)

        fichier = serializer.validated_data["preuve"]
        extension = (
            fichier.name.rsplit(".", 1)[-1].lower() if "." in fichier.name else "bin"
        )
        chemin = default_storage.save(
            f"dossiers/depots/{uuid.uuid4().hex}.{extension}", fichier
        )

        depot.preuve = chemin
        depot.montant_depose = serializer.validated_data["montant_depose"]
        depot.methode_paiement = serializer.validated_data["methode_paiement"]
        depot.reference_transaction = serializer.validated_data["reference_transaction"]
        depot.statut = DepotMinimum.Statut.PREUVE_DEPOSEE
        depot.date_depot = timezone.now()
        depot.save()

        journaliser(
            request.user,
            JournalAudit.Action.DEPOT_PREUVE_DEPOSEE,
            "DepotMinimum",
            str(depot.pk),
            apres={
                "dossier": str(dossier.pk),
                "montant_depose": str(depot.montant_depose),
                "methode_paiement": depot.methode_paiement,
            },
            requete=request,
        )

        try:
            notifier_depot_preuve_deposee_task.delay(str(depot.pk))
            envoyer_email_depot_preuve_deposee.delay(str(depot.pk))
        except Exception:
            logger.exception("Notification preuve de dépôt non dispatchée.")

        depot.refresh_from_db()
        return Response(DepotMinimumSerializer(depot).data, status=status.HTTP_200_OK)


class DepotListeAgentAPIView(generics.GenericAPIView):
    """File d'attente des dépôts minimum de la SGI (personnel SGI).

    GET /api/dossiers/depots/?statut=PREUVE_DEPOSEE
    Filtrage multi-statuts : ?statut=PREUVE_DEPOSEE&statut=REJETE
    (et/ou liste séparée par des virgules).
    """

    serializer_class = DepotMinimumSerializer
    permission_classes = (permissions.IsAuthenticated, EstPersonnelSGI)

    def get_queryset(self):
        qs = (
            DepotMinimum.objects.filter(dossier__sgi_id=self.request.user.sgi_id)
            .select_related("dossier__utilisateur", "dossier__sgi")
            .order_by("-date_creation")
        )
        statuts = self.request.query_params.getlist("statut") or [
            s.strip()
            for s in (self.request.query_params.get("statut") or "").split(",")
            if s.strip()
        ]
        if statuts:
            qs = qs.filter(statut__in=statuts)
        return qs

    def get(self, request):
        depots = self.get_queryset()
        return Response(
            DepotMinimumSerializer(depots, many=True).data, status=status.HTTP_200_OK
        )


class DepotDetailAgentAPIView(generics.GenericAPIView):
    """Détail d'un dépôt de la SGI (personnel SGI).

    GET /api/dossiers/depots/<uuid:pk>/
    """

    serializer_class = DepotMinimumSerializer
    permission_classes = (permissions.IsAuthenticated, EstPersonnelSGI)

    def get(self, request, pk):
        depot = _depot_autorise(request, self, pk)
        return Response(DepotMinimumSerializer(depot).data, status=status.HTTP_200_OK)


class DepotVerifierAgentAPIView(generics.GenericAPIView):
    """Vérification de la preuve par le personnel SGI (approbation/rejet).

    POST /api/dossiers/depots/<uuid:pk>/verifier/
    Body : { "approuver": true|false, "commentaire_agent": "…" (rejet : obligatoire) }

    L'approbation passe le dépôt à APPROUVE et déclenche l'ouverture du
    compte : le dossier VALIDE devient ACTIF via `transiter` (qui
    re-vérifie l'exigence). Le rejet rend la preuve à corriger à
    l'investisseur (il peut redéposer une nouvelle preuve).
    """

    serializer_class = DepotMinimumSerializer
    permission_classes = (permissions.IsAuthenticated, EstPersonnelSGI)

    @extend_schema(
        request=inline_serializer(
            "VerificationDepot",
            {
                "approuver": drf_serializers.BooleanField(),
                "commentaire_agent": drf_serializers.CharField(required=False),
            },
        ),
        responses={200: DepotMinimumSerializer},
    )
    def post(self, request, pk):
        depot = _depot_autorise(request, self, pk)

        if depot.statut != DepotMinimum.Statut.PREUVE_DEPOSEE:
            return erreur(
                "STATUT_INCOMPATIBLE",
                (
                    f"Seule une preuve déposée peut être vérifiée : celle-ci "
                    f"est « {depot.get_statut_display()} »."
                ),
                status.HTTP_409_CONFLICT,
                statut_depot=depot.statut,
            )

        approuver = request.data.get("approuver")
        if isinstance(approuver, str):
            approuver = approuver.strip().lower() in ("true", "1", "oui", "yes")
        if not isinstance(approuver, bool):
            return erreur(
                "REQUETE_INVALIDE",
                "Le champ `approuver` (true/false) est obligatoire.",
                status.HTTP_400_BAD_REQUEST,
                champs={"approuver": ["Ce champ est obligatoire (true/false)."]},
            )

        commentaire = (request.data.get("commentaire_agent") or "").strip()
        if not approuver and not commentaire:
            return erreur(
                "MOTIF_REJET_MANQUANT",
                (
                    "Un motif de rejet est obligatoire (`commentaire_agent`) : "
                    "l'investisseur doit savoir pourquoi sa preuve est refusée."
                ),
                status.HTTP_400_BAD_REQUEST,
                champs={"commentaire_agent": [
                    "Obligatoire en cas de rejet : expliquez le motif."
                ]},
            )

        try:
            with transaction.atomic():
                # Verrouillage du dépôt PUIS du dossier : sérialise deux
                # vérifications concurrentes (un seul APPROUVE possible).
                depot = (
                    type(depot)
                    .objects.select_related("dossier__utilisateur", "dossier__sgi")
                    .select_for_update()
                    .get(pk=depot.pk)
                )
                if depot.statut != DepotMinimum.Statut.PREUVE_DEPOSEE:
                    return erreur(
                        "DEPOT_DEJA_TRAITE",
                        (
                            f"Ce dépôt a été traité entre-temps "
                            f"(« {depot.get_statut_display()} ») : "
                            "rechargez la page."
                        ),
                        status.HTTP_409_CONFLICT,
                        statut_depot=depot.statut,
                    )

                depot.commentaire_agent = commentaire
                depot.date_verification = timezone.now()

                if approuver:
                    depot.statut = DepotMinimum.Statut.APPROUVE
                    depot.save(update_fields=[
                        "statut", "commentaire_agent", "date_verification",
                    ])
                    journaliser(
                        request.user,
                        JournalAudit.Action.DEPOT_APPROUVE,
                        "DepotMinimum",
                        str(depot.pk),
                        apres={
                            "dossier": str(depot.dossier.pk),
                            "montant_depose": str(depot.montant_depose),
                        },
                        requete=request,
                    )
                    # L'approbation OUVRE le compte : transition VALIDE→ACTIF.
                    transiter(
                        depot.dossier, Dossier.Statut.ACTIF,
                        agent=request.user, utilisateur=request.user, requete=request,
                    )
                    journaliser(
                        request.user,
                        JournalAudit.Action.ACTIVATION_COMPTE,
                        "Dossier",
                        str(depot.dossier.pk),
                        apres={
                            "reference": depot.dossier.reference,
                            "statut": depot.dossier.statut,
                        },
                        requete=request,
                    )
                    try:
                        notifier_depot_verifie_task.delay(str(depot.pk), True)
                        envoyer_email_depot_verifie.delay(str(depot.pk), True)
                    except Exception:
                        logger.exception("Notification dépôt approuvé non dispatchée.")
                else:
                    depot.statut = DepotMinimum.Statut.REJETE
                    depot.save(update_fields=[
                        "statut", "commentaire_agent", "date_verification",
                    ])
                    journaliser(
                        request.user,
                        JournalAudit.Action.DEPOT_REJETE,
                        "DepotMinimum",
                        str(depot.pk),
                        apres={
                            "dossier": str(depot.dossier.pk),
                            "commentaire_agent": depot.commentaire_agent,
                        },
                        requete=request,
                    )
                    try:
                        notifier_depot_verifie_task.delay(str(depot.pk), False)
                        envoyer_email_depot_verifie.delay(str(depot.pk), False)
                    except Exception:
                        logger.exception("Notification dépôt rejeté non dispatchée.")
        except ValidationError as exc:
            return erreur(
                "TRANSITION_REFUSEE",
                " ".join(exc.messages),
                status.HTTP_409_CONFLICT,
            )

        depot.refresh_from_db()
        return Response(DepotMinimumSerializer(depot).data, status=status.HTTP_200_OK)