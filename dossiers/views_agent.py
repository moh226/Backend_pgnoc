"""Actions du circuit d'instruction réservées au personnel SGI (UC13/UC14).

Regroupées à part des vues du parcours investisseur pour coller au
RBAC : chaque action exige un agent/admin de LA SGI du dossier, jamais
un investisseur (cloisonnement strict repris de `PeutAccederAuDossier`).
Les transitions d'état délèguent tout à la machine à états
(`dossiers.workflow.transiter`).
"""

import logging

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, permissions, status
from rest_framework import serializers as drf_serializers
from rest_framework.response import Response

logger = logging.getLogger("pgnoc.dossiers")

from audit.models import JournalAudit
from audit.services import journaliser
from comptes.permissions import EstAdminSGI, EstPersonnelSGI
from comptes.models import Role, Utilisateur
from dossiers.models import Dossier, ValeurChamp
from dossiers.permissions import PeutAccederAuDossier
from dossiers.serializers import DossierDetailSerializer
from dossiers.workflow import transiter
from notifications.tasks import notifier_commentaire_agent_task


def _recuperer_dossier_autorise(request, view, dossier_pk):
    """Récupère le dossier et vérifie l'accès object (SGI du personnel)."""
    dossier = get_object_or_404(Dossier, pk=dossier_pk)
    if not PeutAccederAuDossier().has_object_permission(request, view, dossier):
        view.permission_denied(
            request, message="Vous n'avez pas accès à ce dossier."
        )
    return dossier


class DossierPrendreEnChargeAPIView(generics.GenericAPIView):
    """UC14 : un Agent SGI prend en charge un dossier SOUMIS (de SA SGI).

    POST /api/dossiers/dossiers/<pk>/prendre-en-charge/
    """

    serializer_class = DossierDetailSerializer
    permission_classes = (permissions.IsAuthenticated, EstPersonnelSGI)

    def post(self, request, dossier_pk):
        dossier = _recuperer_dossier_autorise(request, self, dossier_pk)

        if dossier.statut != Dossier.Statut.SOUMIS:
            return Response(
                {"detail": "Seul un dossier SOUMIS peut être pris en charge."},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            transiter(
                dossier, Dossier.Statut.EN_INSTRUCTION,
                agent=request.user, utilisateur=request.user, requete=request,
            )
        except ValidationError as exc:
            raise drf_serializers.ValidationError(exc.messages)

        dossier.refresh_from_db()
        return Response(self.get_serializer(dossier).data, status=status.HTTP_200_OK)


class ValeurChampCommenterAPIView(generics.GenericAPIView):
    """UC09/UC14 : l'agent commente une valeur à corriger (relecture).

    POST /api/dossiers/dossiers/<pk>/commenter/
    Body : { "valeur": <uuid>, "commentaire": "…" }

    Le passage du champ en relecture est signalé à l'investisseur via
    `est_corrige=False` (de nouveau à corriger) et repasse à True dès
    qu'il ressaisit le champ (voir `ValeurChampListCreateAPIView.create`).
    """

    permission_classes = (permissions.IsAuthenticated, EstPersonnelSGI)

    serializer_class = drf_serializers.Serializer

    @extend_schema(
        request=inline_serializer(
            "CommentaireValeur",
            {
                "valeur": drf_serializers.UUIDField(),
                "commentaire": drf_serializers.CharField(),
            },
        ),
        responses={200: inline_serializer(
            "ValeurCommentee",
            {
                "id": drf_serializers.UUIDField(),
                "commentaire_agent": drf_serializers.CharField(),
            },
        )},
    )
    def post(self, request, dossier_pk):
        dossier = _recuperer_dossier_autorise(request, self, dossier_pk)

        valeur_pk = request.data.get("valeur")
        commentaire = (request.data.get("commentaire") or "").strip()

        if not valeur_pk or not commentaire:
            return Response(
                {
                    "detail": "Les champs `valeur` et `commentaire` sont obligatoires.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if dossier.statut != Dossier.Statut.EN_INSTRUCTION:
            return Response(
                {"detail": "Le dossier doit être EN_INSTRUCTION pour commenter."},
                status=status.HTTP_409_CONFLICT,
            )

        valeur = get_object_or_404(ValeurChamp, pk=valeur_pk, dossier_id=dossier_pk)

        valeur.commentaire_agent = commentaire
        valeur.est_corrige = False
        valeur.save(update_fields=["commentaire_agent", "est_corrige"])

        journaliser(
            request.user,
            JournalAudit.Action.COMMENTAIRE_AGENT,
            "ValeurChamp",
            str(valeur.pk),
            apres={"commentaire_agent": valeur.commentaire_agent, "dossier": str(dossier.pk)},
            requete=request,
        )

        # UC09 : l'investisseur doit être alerté de la demande de
        # correction — il ne peut pas deviner qu'il doit revenir.
        try:
            notifier_commentaire_agent_task.delay(str(dossier.pk), str(valeur.pk))
        except Exception:
            logger.warning("Notification commentaire non dispatchée (Redis ?)", exc_info=True)

        return Response(
            {"id": valeur.id, "commentaire_agent": valeur.commentaire_agent},
            status=status.HTTP_200_OK,
        )


class DossierValiderAPIView(generics.GenericAPIView):
    """UC14 : décision de validation d'un dossier EN_INSTRUCTION (signature requise).

    POST /api/dossiers/dossiers/<pk>/valider/
    """

    serializer_class = DossierDetailSerializer
    permission_classes = (permissions.IsAuthenticated, EstPersonnelSGI)

    def post(self, request, dossier_pk):
        dossier = _recuperer_dossier_autorise(request, self, dossier_pk)

        if dossier.statut != Dossier.Statut.EN_INSTRUCTION:
            return Response(
                {"detail": "Seul un dossier EN_INSTRUCTION peut être validé."},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            transiter(
                dossier, Dossier.Statut.VALIDE,
                agent=request.user, utilisateur=request.user, requete=request,
            )
        except ValidationError as exc:
            raise drf_serializers.ValidationError(exc.messages)

        dossier.refresh_from_db()
        return Response(self.get_serializer(dossier).data, status=status.HTTP_200_OK)


class DossierRejeterAPIView(generics.GenericAPIView):
    """UC14 : décision de rejet d'un dossier EN_INSTRUCTION (motif obligatoire).

    POST /api/dossiers/dossiers/<pk>/rejeter/
    Body : { "motif_rejet": "…" }
    """

    serializer_class = DossierDetailSerializer
    permission_classes = (permissions.IsAuthenticated, EstPersonnelSGI)

    def post(self, request, dossier_pk):
        dossier = _recuperer_dossier_autorise(request, self, dossier_pk)

        if dossier.statut != Dossier.Statut.EN_INSTRUCTION:
            return Response(
                {"detail": "Seul un dossier EN_INSTRUCTION peut être rejeté."},
                status=status.HTTP_409_CONFLICT,
            )

        motif = (request.data.get("motif_rejet") or "").strip()
        try:
            transiter(
                dossier, Dossier.Statut.REJETE,
                agent=request.user, motif_rejet=motif,
                utilisateur=request.user, requete=request,
            )
        except ValidationError as exc:
            raise drf_serializers.ValidationError(exc.messages)

        dossier.refresh_from_db()
        return Response(self.get_serializer(dossier).data, status=status.HTTP_200_OK)


class DossierTransfererAPIView(generics.GenericAPIView):
    """UC : un Admin SGI transfère un dossier à un autre agent de la même SGI.

    POST /api/dossiers/dossiers/<pk>/transferer/
    Body : { "agent_id": "uuid" }

    Règles :
      - Seul un Admin SGI peut transférer.
      - Le dossier doit être EN_INSTRUCTION ou SOUMIS.
      - L'agent cible doit être actif et appartenir à la même SGI.
      - Le statut reste EN_INSTRUCTION (si SOUMIS, bascule via workflow).
    """

    serializer_class = DossierDetailSerializer
    permission_classes = (permissions.IsAuthenticated, EstAdminSGI)

    @extend_schema(
        request=inline_serializer(
            "TransfertDossier",
            {"agent_id": drf_serializers.UUIDField()},
        ),
        responses={200: DossierDetailSerializer},
    )
    def post(self, request, dossier_pk):
        dossier = _recuperer_dossier_autorise(request, self, dossier_pk)

        if dossier.statut not in (Dossier.Statut.SOUMIS, Dossier.Statut.EN_INSTRUCTION):
            return Response(
                {"detail": "Seul un dossier SOUMIS ou EN_INSTRUCTION peut être transféré."},
                status=status.HTTP_409_CONFLICT,
            )

        agent_id = request.data.get("agent_id")
        if not agent_id:
            return Response(
                {"detail": "Le champ `agent_id` est obligatoire."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            agent_cible = Utilisateur.objects.get(pk=agent_id)
        except (Utilisateur.DoesNotExist, ValueError):
            return Response(
                {"detail": "Agent introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not agent_cible.is_active:
            return Response(
                {"detail": "Cet agent est désactivé."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if agent_cible.sgi_id != dossier.sgi_id:
            return Response(
                {"detail": "Cet agent n'appartient pas à la même SGI que le dossier."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if agent_cible.role.code not in (Role.Code.AGENT_SGI, Role.Code.ADMIN_SGI):
            return Response(
                {"detail": "L'agent cible doit être un Agent SGI ou Admin SGI."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ancien_agent_id = dossier.agent_id
        ancien_statut = dossier.statut

        dossier.agent = agent_cible
        update_fields = ["agent"]

        if dossier.statut == Dossier.Statut.SOUMIS:
            from dossiers.workflow import transiter
            try:
                transiter(
                    dossier,
                    Dossier.Statut.EN_INSTRUCTION,
                    agent=agent_cible,
                    utilisateur=request.user,
                    requete=request,
                )
                update_fields = []
            except ValidationError as exc:
                return Response(
                    {"detail": str(exc.message)},
                    status=status.HTTP_409_CONFLICT,
                )

        if update_fields:
            dossier.save(update_fields=update_fields)

        journaliser(
            request.user,
            JournalAudit.Action.TRANSITION_DOSSIER,
            "Dossier",
            str(dossier.pk),
            avant={
                "agent_id": str(ancien_agent_id) if ancien_agent_id else None,
                "statut": ancien_statut,
            },
            apres={
                "agent_id": str(agent_cible.pk),
                "statut": dossier.statut,
            },
            requete=request,
        )

        dossier.refresh_from_db()
        return Response(self.get_serializer(dossier).data, status=status.HTTP_200_OK)