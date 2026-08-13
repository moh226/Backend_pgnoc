"""Espace Admin Général : UC20 (SGI), UC21 (utilisateurs), UC22 (tableau de bord).

Règles transverses portées par ces vues :
  - réservées à l'Admin Général (`EstAdminGeneral`) ; aucun autre rôle
    ne les atteint (cloisonnement RBAC strict) ;
  - chaque écriture est journalisée dans le journal d'audit immuable,
    avec l'état avant/après (métadonnées légales IP/User-Agent) ;
  - aucune suppression destructive : suspension par `est_active=False`
    (SGI ou compte) ;
  - l'Admin Général ne peut ni se désactiver, ni changer son propre
    rôle, et le DERNIER admin général actif de la plateforme est protégé
    (impossible de verrouiller le système hors de portée).
"""

from django.db.models import Count
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, permissions, serializers
from rest_framework.response import Response

from administration.serializers import (
    SGIAdminSerializer,
    UtilisateurAdminSerializer,
)
from audit.models import JournalAudit
from audit.services import journaliser
from comptes.models import Role, Utilisateur
from comptes.permissions import EstAdminGeneral
from dossiers.models import Dossier
from sgi.models import SGI


def _snapshot_sgi(sgi):
    return {"nom": sgi.nom, "code_sgi": sgi.code_sgi, "est_active": sgi.est_active}


def _snapshot_utilisateur(utilisateur):
    return {
        "email": utilisateur.email,
        "role": utilisateur.role.code,
        "sgi": str(utilisateur.sgi_id) if utilisateur.sgi_id else None,
        "is_active": utilisateur.is_active,
    }


class SGIListCreateAPIView(generics.ListCreateAPIView):
    """UC20 — Catalogue des SGI partenaires (liste + création).

    GET  /api/admin-general/sgi/
    POST /api/admin-general/sgi/
    """

    serializer_class = SGIAdminSerializer
    permission_classes = (permissions.IsAuthenticated, EstAdminGeneral)

    def get_queryset(self):
        return SGI.objects.annotate(
            nb_utilisateurs=Count("utilisateurs", distinct=True),
            nb_dossiers=Count("dossiers", distinct=True),
        ).order_by("nom")

    def perform_create(self, serializer):
        sgi = serializer.save()
        journaliser(
            self.request.user,
            JournalAudit.Action.CREATION_SGI,
            "SGI", str(sgi.pk),
            apres=_snapshot_sgi(sgi),
            requete=self.request,
        )


class SGIRetrieveUpdateAPIView(generics.RetrieveUpdateAPIView):
    """UC20 — Détail / mise à jour d'une SGI partenaire.

    GET   /api/admin-general/sgi/<id>/
    PATCH /api/admin-general/sgi/<id>/   (ex: {"est_active": false})

    Pas de DELETE : une SGI désactivée ne reçoit plus de nouveaux
    dossiers (contrôle porté par le workflow à la soumission).
    """

    serializer_class = SGIAdminSerializer
    permission_classes = (permissions.IsAuthenticated, EstAdminGeneral)

    def get_queryset(self):
        return SGI.objects.annotate(
            nb_utilisateurs=Count("utilisateurs", distinct=True),
            nb_dossiers=Count("dossiers", distinct=True),
        ).order_by("nom")

    def perform_update(self, serializer):
        avant = _snapshot_sgi(self.get_object())
        sgi = serializer.save()
        apres = _snapshot_sgi(sgi)
        if avant != apres:
            journaliser(
                self.request.user,
                JournalAudit.Action.MODIFICATION_SGI,
                "SGI", str(sgi.pk),
                avant=avant, apres=apres,
                requete=self.request,
            )


class UtilisateurListCreateAPIView(generics.ListCreateAPIView):
    """UC21 — Comptes internes (liste filtrable + création).

    GET  /api/admin-general/utilisateurs/?role=AGENT_SGI&actif=true&
         sgi=<uuid>&email=…
    POST /api/admin-general/utilisateurs/  ({"email", "role", "sgi", …,
         "mot_de_passe"})

    Le mot de passe initial est obligatoire à la création ; il est
    remis à l'intéressé par l'admin (canal sûr), jamais renvoyé par
    l'API. Les rôles INVESTISSEUR sont exclus (inscription publique).
    """

    serializer_class = UtilisateurAdminSerializer
    permission_classes = (permissions.IsAuthenticated, EstAdminGeneral)

    def get_queryset(self):
        qs = Utilisateur.objects.select_related("role", "sgi")
        params = self.request.query_params
        if role := params.get("role"):
            qs = qs.filter(role__code=role)
        if email := params.get("email", "").strip():
            qs = qs.filter(email__icontains=email)
        if sgi_id := params.get("sgi"):
            qs = qs.filter(sgi_id=sgi_id)
        if actif := params.get("actif"):
            qs = qs.filter(is_active=actif.lower() in ("true", "1"))
        return qs

    def perform_create(self, serializer):
        utilisateur = serializer.save()
        journaliser(
            self.request.user,
            JournalAudit.Action.CREATION_UTILISATEUR,
            "Utilisateur", str(utilisateur.pk),
            apres=_snapshot_utilisateur(utilisateur),
            requete=self.request,
        )


class UtilisateurRetrieveUpdateAPIView(generics.RetrieveUpdateAPIView):
    """UC21 — Détail / mise à jour d'un compte interne.

    GET   /api/admin-general/utilisateurs/<id>/
    PATCH /api/admin-general/utilisateurs/<id>/  (ex: {"is_active": false},
         {"role": "AGENT_SGI", "sgi": "<uuid>"}…)

    Garde-fous :
      - impossible de se désactiver ou de changer son propre rôle ;
      - le dernier ADMIN_GENERAL actif ne peut ni être désactivé ni
        rétrogradé (le système garde toujours un gouvernail).
    """

    serializer_class = UtilisateurAdminSerializer
    permission_classes = (permissions.IsAuthenticated, EstAdminGeneral)

    def get_queryset(self):
        return Utilisateur.objects.select_related("role", "sgi")

    def perform_update(self, serializer):
        instance = self.get_object()
        donnees = serializer.validated_data
        moi = instance.pk == self.request.user.pk

        if moi:
            if donnees.get("is_active") is False:
                raise serializers.ValidationError({
                    "is_active": "Vous ne pouvez pas désactiver votre propre compte.",
                })
            nouveau_role = donnees.get("role")
            if nouveau_role is not None and nouveau_role.pk != instance.role_id:
                raise serializers.ValidationError({
                    "role": "Vous ne pouvez pas modifier votre propre rôle.",
                })

        nouveau_role = donnees.get("role", instance.role)
        nouvelle_activation = donnees.get("is_active", instance.is_active)
        if (instance.role.code == Role.Code.ADMIN_GENERAL and (
                nouvelle_activation is False
                or nouveau_role.code != Role.Code.ADMIN_GENERAL)):
            autres_actifs = Utilisateur.objects.filter(
                role__code=Role.Code.ADMIN_GENERAL, is_active=True,
            ).exclude(pk=instance.pk).count()
            if autres_actifs == 0:
                raise serializers.ValidationError({
                    "role": "Impossible : c'est le dernier administrateur général actif.",
                })

        avant = _snapshot_utilisateur(instance)
        utilisateur = serializer.save()
        apres = _snapshot_utilisateur(utilisateur)
        if avant != apres:
            journaliser(
                self.request.user,
                JournalAudit.Action.MODIFICATION_UTILISATEUR,
                "Utilisateur", str(utilisateur.pk),
                avant=avant, apres=apres,
                requete=self.request,
            )


class DashboardAPIView(generics.GenericAPIView):
    """UC22 — Tableau de bord global (consultation seule).

    GET /api/admin-general/dashboard/

    Agrégats de pilotage : volume de dossiers par statut, activité du
    jour, état du réseau de SGI (dont celles sans convention publiée,
    un signal d'alerte réglementaire), répartition des comptes par rôle
    et flux d'activité récent extrait du journal d'audit.
    """

    permission_classes = (permissions.IsAuthenticated, EstAdminGeneral)

    serializer_class = serializers.Serializer

    @extend_schema(responses={200: inline_serializer(
        "TableauDeBord",
        {
            "dossiers": inline_serializer(
                "ResumeDossiers",
                {
                    "total": serializers.IntegerField(),
                    "soumis_aujourd_hui": serializers.IntegerField(),
                    "par_statut": serializers.DictField(
                        child=serializers.IntegerField(),
                    ),
                },
            ),
            "sgi": inline_serializer(
                "ResumeSGI",
                {
                    "total": serializers.IntegerField(),
                    "actives": serializers.IntegerField(),
                    "sans_convention_publiee": serializers.IntegerField(),
                },
            ),
            "utilisateurs": inline_serializer(
                "ResumeUtilisateurs",
                {
                    "total": serializers.IntegerField(),
                    "actifs": serializers.IntegerField(),
                    "par_role": serializers.DictField(
                        child=serializers.IntegerField(),
                    ),
                },
            ),
            "activite_recente": inline_serializer(
                "TraceRecente",
                {
                    "date": serializers.CharField(),
                    "email": serializers.CharField(allow_null=True),
                    "action": serializers.CharField(),
                    "entite_concernee": serializers.CharField(allow_null=True),
                    "entite_id": serializers.CharField(allow_null=True),
                },
                many=True,
            ),
        },
    )})
    def get(self, request):
        aujourd_hui = timezone.localdate()

        sgi_toutes = SGI.objects.prefetch_related("convention")
        sans_convention = sum(
            1 for sgi in sgi_toutes
            if not (hasattr(sgi, "convention") and sgi.convention.est_publiee())
        )

        par_statut = {
            statut: Dossier.objects.filter(statut=statut).count()
            for statut in Dossier.Statut.values
        }
        par_role = {
            role.code: Utilisateur.objects.filter(role__code=role.code).count()
            for role in Role.objects.all()
        }

        activite_recente = [
            {
                "date": trace.date_action.isoformat(),
                "email": trace.utilisateur.email if trace.utilisateur_id else None,
                "action": trace.action,
                "entite_concernee": trace.entite_concernee,
                "entite_id": trace.entite_id,
            }
            for trace in JournalAudit.objects.select_related("utilisateur")
            .order_by("-date_action")[:8]
        ]

        return Response({
            "dossiers": {
                "total": Dossier.objects.count(),
                "soumis_aujourd_hui": Dossier.objects.filter(
                    date_soumission__date=aujourd_hui,
                ).count(),
                "par_statut": par_statut,
            },
            "sgi": {
                "total": sgi_toutes.count(),
                "actives": SGI.objects.filter(est_active=True).count(),
                "sans_convention_publiee": sans_convention,
            },
            "utilisateurs": {
                "total": Utilisateur.objects.count(),
                "actifs": Utilisateur.objects.filter(is_active=True).count(),
                "par_role": par_role,
            },
            "activite_recente": activite_recente,
        })