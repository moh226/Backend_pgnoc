"""Lecture du journal d'audit : réservée à l'Admin Général (§8.3).

Le journal est INSERT ONLY côté écriture ; ces vues n'exposent que la
lecture (liste paginée, filtres, export CSV pour les contrôles CREPMF).
"""

import csv
import json
from datetime import date

from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, permissions
from rest_framework import serializers as drf_serializers

from audit.models import JournalAudit
from audit.serializers import JournalAuditSerializer
from comptes.permissions import EstAdminGeneral


def _filtrer_journal(params):
    """Applique les filtres communs liste/export (tous optionnels)."""
    qs = JournalAudit.objects.select_related("utilisateur")

    if action := params.get("action"):
        qs = qs.filter(action=action)
    if email := params.get("email", "").strip():
        qs = qs.filter(utilisateur__email__icontains=email)
    if entite := params.get("entite_concernee"):
        qs = qs.filter(entite_concernee=entite)
    if entite_id := params.get("entite_id", "").strip():
        qs = qs.filter(entite_id__icontains=entite_id)

    def _lire_date(nom):
        brut = params.get(nom)
        if not brut:
            return None
        try:
            return date.fromisoformat(brut)
        except ValueError as exc:
            raise drf_serializers.ValidationError(
                {nom: f"Format de date invalide « {brut} » (attendu : AAAA-MM-JJ)."}
            ) from exc

    if date_debut := _lire_date("date_debut"):
        qs = qs.filter(date_action__date__gte=date_debut)
    if date_fin := _lire_date("date_fin"):
        qs = qs.filter(date_action__date__lte=date_fin)

    return qs


_PARAMETRES_JOURNAL = [
    OpenApiParameter("action", OpenApiTypes.STR, OpenApiParameter.QUERY),
    OpenApiParameter("email", OpenApiTypes.STR, OpenApiParameter.QUERY),
    OpenApiParameter("entite_concernee", OpenApiTypes.STR, OpenApiParameter.QUERY),
    OpenApiParameter("entite_id", OpenApiTypes.STR, OpenApiParameter.QUERY),
    OpenApiParameter("date_debut", OpenApiTypes.DATE, OpenApiParameter.QUERY),
    OpenApiParameter("date_fin", OpenApiTypes.DATE, OpenApiParameter.QUERY),
]


@extend_schema_view(
    get=extend_schema(parameters=_PARAMETRES_JOURNAL),
)
class JournalAuditListAPIView(generics.ListAPIView):
    """Liste paginée du journal d'audit (UC23), réservée à l'Admin Général.

    GET /api/audit/journal/?action=TRANSITION_DOSSIER&email=a@b.c&
        entite_concernee=Dossier&entite_id=…&date_debut=…&date_fin=…
    """

    serializer_class = JournalAuditSerializer
    permission_classes = (permissions.IsAuthenticated, EstAdminGeneral)

    def get_queryset(self):
        return _filtrer_journal(self.request.query_params)


def _cellule_csv(valeur):
    """Neutralise une injection de formule CSV (Excel/Sheets).

    Une cellule commençant par = + - @ ou \t est interprétée comme une
    formule par les tableurs (l'export contient des données saisies par
    les utilisateurs — ex: `avant`/`apres` en JSON — qu'on ne peut pas
    considérer comme fiables). On préfixe par `'` pour forcer le texte.
    """
    if valeur and valeur[:1] in ("=", "+", "-", "@", "\t"):
        return "'" + valeur
    return valeur


class JournalAuditExportAPIView(generics.GenericAPIView):
    """Export CSV du journal (filtres identiques à la liste), INSERT ONLY à l'écrit.

    GET /api/audit/journal/export/?action=…&date_debut=…&date_fin=…
    """

    permission_classes = (permissions.IsAuthenticated, EstAdminGeneral)

    serializer_class = drf_serializers.Serializer

    @extend_schema(parameters=_PARAMETRES_JOURNAL, responses={(200, "text/csv"): OpenApiTypes.STR})
    def get(self, request):
        entrees = _filtrer_journal(request.query_params).iterator()

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="journal-audit-{date.today().isoformat()}.csv"'
        )
        writer = csv.writer(response)
        writer.writerow([
            "date_action", "email_utilisateur", "action", "entite_concernee",
            "entite_id", "avant", "apres", "ip_address", "user_agent",
        ])
        for entree in entrees:
            writer.writerow([
                entree.date_action.isoformat(),
                _cellule_csv(entree.utilisateur.email if entree.utilisateur else ""),
                _cellule_csv(entree.action),
                _cellule_csv(entree.entite_concernee),
                _cellule_csv(entree.entite_id),
                _cellule_csv(json.dumps(entree.avant, ensure_ascii=False)) if entree.avant else "",
                _cellule_csv(json.dumps(entree.apres, ensure_ascii=False)) if entree.apres else "",
                _cellule_csv(entree.ip_address or ""),
                _cellule_csv(entree.user_agent),
            ])
        return response