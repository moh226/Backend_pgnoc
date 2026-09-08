"""Lecture du journal d'audit : réservée à l'Admin Général (§8.3).

Le journal est INSERT ONLY côté écriture ; ces vues n'exposent que la
lecture (liste paginée, filtres, export CSV/PDF pour les contrôles CREPMF).
"""

import csv
import io
import json
from datetime import date

from django.http import HttpResponse, StreamingHttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
)
from rest_framework import generics, permissions
from rest_framework import serializers as drf_serializers

from audit.models import JournalAudit
from audit.serializers import JournalAuditSerializer
from audit.services import journaliser
from comptes.permissions import EstAdminGeneral


def _tracer_export(request, format_export, params):
    """Trace un export du journal (donnée réglementaire sensible)."""
    journaliser(
        request.user,
        JournalAudit.Action.EXPORT_JOURNAL,
        "JournalAudit",
        f"{format_export}-{date.today().isoformat()}",
        apres={
            "format": format_export,
            "filtres": {
                cle: valeur for cle, valeur in params.items()
                if cle in ("action", "email", "date_debut", "date_fin")
            },
        },
        requete=request,
    )


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
        # L'export du journal est lui-même une donnée sensible (exfiltration
        # potentielle de toute la piste réglementaire) : l'accès est tracé.
        _tracer_export(request, "CSV", request.query_params)
        entrees = _filtrer_journal(request.query_params).iterator()

        def lignes_csv():
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([
                "date_action", "email_utilisateur", "action", "entite_concernee",
                "entite_id", "avant", "apres", "ip_address", "user_agent",
            ])
            yield buf.getvalue()
            for entree in entrees:
                buf = io.StringIO()
                w = csv.writer(buf)
                w.writerow([
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
                yield buf.getvalue()

        response = StreamingHttpResponse(lignes_csv(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="journal-audit-{date.today().isoformat()}.csv"'
        )
        return response


class JournalAuditExportPDFAPIView(generics.GenericAPIView):
    """Export PDF du journal d'audit (filtres identiques à la liste).

    GET /api/audit/journal/export-pdf/?action=…&date_debut=…&date_fin=…
    """

    permission_classes = (permissions.IsAuthenticated, EstAdminGeneral)
    serializer_class = drf_serializers.Serializer

    @extend_schema(
        parameters=_PARAMETRES_JOURNAL,
        responses={(200, "application/pdf"): OpenApiTypes.BINARY},
        description="Export PDF du journal d'audit pour les contrôles réglementaires CREPMF.",
    )
    def get(self, request):
        # L'export du journal est lui-même une donnée sensible : traçage.
        _tracer_export(request, "PDF", request.query_params)
        LIMITE_PDF = 500
        total_filtre = _filtrer_journal(request.query_params).count()
        entrees = list(_filtrer_journal(request.query_params)[:LIMITE_PDF])

        response = HttpResponse(content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="journal-audit-{date.today().isoformat()}.pdf"'
        )

        doc = SimpleDocTemplate(
            response,
            pagesize=landscape(A4),
            leftMargin=15 * mm,
            rightMargin=15 * mm,
            topMargin=20 * mm,
            bottomMargin=15 * mm,
        )

        styles = getSampleStyleSheet()
        elements = []

        title = Paragraph(
            f"Journal d'audit — {date.today().strftime('%d/%m/%Y')}",
            styles["Title"],
        )
        elements.append(title)
        elements.append(Spacer(1, 8 * mm))

        if not entrees:
            elements.append(Paragraph("Aucune entrée trouvée.", styles["Normal"]))
        else:
            header = [
                "Date", "Utilisateur", "Action", "Entité",
                "ID Entité", "Avant", "Après", "IP", "User-Agent",
            ]
            data = [header]
            for entree in entrees:
                avant = json.dumps(entree.avant, ensure_ascii=False)[:80] if entree.avant else ""
                apres = json.dumps(entree.apres, ensure_ascii=False)[:80] if entree.apres else ""
                data.append([
                    entree.date_action.strftime("%d/%m/%Y %H:%M"),
                    entree.utilisateur.email if entree.utilisateur else "",
                    entree.action,
                    entree.entite_concernee,
                    entree.entite_id[:20] if entree.entite_id else "",
                    avant,
                    apres,
                    entree.ip_address or "",
                    (entree.user_agent or "")[:40],
                ])

            table = Table(data, repeatRows=1)
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a237e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, 0), 7),
                ("FONTSIZE", (0, 1), (-1, -1), 6),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
            ]))
            elements.append(table)

            elements.append(Spacer(1, 5 * mm))
            elements.append(Paragraph(
                f"{len(entrees)} entrée(s) sur {total_filtre} — export généré le "
                f"{date.today().strftime('%d/%m/%Y')}"
                + (
                    f" (tronqué à {LIMITE_PDF} : utilisez l'export CSV pour "
                    "l'intégralité)" if total_filtre > LIMITE_PDF else ""
                ),
                styles["Normal"],
            ))

        doc.build(elements)
        return response