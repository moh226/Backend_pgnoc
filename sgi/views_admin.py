"""Publication de la convention tarifaire et de la présentation (UC16).

Réservé à l'Admin SGI (`EstAdminSGI`), cloisonné à sa propre SGI :
pas d'identifiant de SGI à fournir, elle est déduite du compte. Le
fichier PDF est stocké sous un nom généré côté serveur (UUID), jamais
le nom client, comme pour les justificatifs des dossiers.

Le format est vérifié par l'extension ET par les magic bytes du
contenu (même durcissement que les justificatifs KYC : le nom et le
Content-Type envoyés par le client sont falsifiables), et la taille
est plafonnée.
"""

import uuid

from django.core.files.storage import default_storage
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, parsers, permissions, serializers, status
from rest_framework.response import Response

from audit.models import JournalAudit
from audit.services import journaliser
from comptes.permissions import EstAdminSGI
from sgi.models import ConventionTarifaire, InformationPresentation
from sgi.serializers import (
    PresentationAdminEntreeSerializer,
    PresentationSectionsSerializer,
    sections_de_presentation,
)

# Signature binaire d'un fichier PDF (« %PDF »).
_MAGIC_PDF = b"\x25\x50\x44\x46"

_CONVENTION_ENTREE = inline_serializer(
    "ConventionTarifaireEntree",
    {
        "titre": serializers.CharField(required=False),
        "fichier_pdf": serializers.FileField(required=False),
    },
)
_CONVENTION_SORTIE = inline_serializer(
    "ConventionTarifaire",
    {
        "titre": serializers.CharField(),
        "fichier": serializers.CharField(),
        "url_signee": serializers.CharField(),
    },
)

# Plafond de taille de la convention tarifaire.
TAILLE_MAX_CONVENTION_MO = 10


def _valider_pdf(fichier):
    """Retourne un message d'erreur si `fichier` n'est pas un PDF valide.

    Trois contrôles : extension, magic bytes du contenu réel, taille.
    Retourne `None` quand le fichier est acceptable.
    """
    extension = fichier.name.rsplit(".", 1)[-1].lower() if "." in fichier.name else ""
    if extension != "pdf":
        return "Seul le format PDF est accepté."

    signature = fichier.read(len(_MAGIC_PDF))
    fichier.seek(0)
    if not signature.startswith(_MAGIC_PDF):
        return "Le contenu du fichier n'est pas un PDF valide."

    taille_mo = fichier.size / (1024 * 1024)
    if taille_mo > TAILLE_MAX_CONVENTION_MO:
        return (
            f"Fichier trop volumineux ({taille_mo:.1f} Mo, "
            f"max {TAILLE_MAX_CONVENTION_MO} Mo)."
        )
    return None


def _obtenir_ou_creer(model, **kwargs):
    """get_or_create immunisé contre la course de création.

    Deux PUT simultanés peuvent déclencher la même création (unicité
    OneToOne SGI↔convention) : la seconde échoue sur la contrainte
    unique avec un IntegrityError au lieu d'un 500. Le `get_or_create`
    est isolé dans une transaction de secours ; en cas de collision on
    relit simplement l'enregistrement qui existe déjà.
    """
    from django.db import IntegrityError, transaction

    try:
        with transaction.atomic():
            return model.objects.get_or_create(**kwargs)
    except IntegrityError:
        return model.objects.get(**kwargs), False


def _gerer_convention(request):
    """Lit ou écrit la convention de la SGI de l'admin connecté."""
    sgi_id = request.user.sgi_id
    convention, _ = _obtenir_ou_creer(ConventionTarifaire, sgi_id=sgi_id)

    if request.method == "GET":
        return Response({
            "titre": convention.titre,
            "fichier": convention.fichier_pdf.name or "",
            "url_signee": default_storage.url(convention.fichier_pdf.name)
            if convention.fichier_pdf else "",
            "date_publication": convention.date_publication,
            "date_modification": convention.date_modification,
        })

    fichier = request.FILES.get("fichier_pdf")
    ancien = convention.fichier_pdf.name or ""
    avant = {
        "titre": convention.titre,
        "fichier_pdf": ancien,
    }
    if fichier:
        erreur = _valider_pdf(fichier)
        if erreur:
            return Response(
                {"fichier_pdf": erreur},
                status=status.HTTP_400_BAD_REQUEST,
            )
        chemin = f"sgi/conventions/{sgi_id}/{uuid.uuid4()}.pdf"
        convention.fichier_pdf.name = default_storage.save(chemin, fichier)
        if ancien and ancien != convention.fichier_pdf.name:
            try:
                default_storage.delete(ancien)
            except Exception:
                pass

    convention.titre = request.data.get("titre", convention.titre)
    convention.save(update_fields=["titre", "fichier_pdf", "date_modification"])

    # Document réglementaire : toute publication/modification est
    # tracée dans le journal d'audit (conformité CREPMF).
    journaliser(
        request.user,
        JournalAudit.Action.MODIFICATION_CONVENTION,
        "ConventionTarifaire",
        str(sgi_id),
        avant=avant,
        apres={
            "titre": convention.titre,
            "fichier_pdf": convention.fichier_pdf.name or "",
        },
        requete=request,
    )

    return Response({
        "titre": convention.titre,
        "fichier": convention.fichier_pdf.name,
        "url_signee": default_storage.url(convention.fichier_pdf.name),
    }, status=status.HTTP_200_OK)


class ConventionTarifaireAdminAPIView(generics.GenericAPIView):
    """Public / met à jour la convention tarifaire de MA SGI.

    GET /api/sgi/admin/convention/
    PUT /api/sgi/admin/convention/   (multipart : titre, fichier_pdf)
    """

    permission_classes = (permissions.IsAuthenticated, EstAdminSGI)
    parser_classes = (parsers.MultiPartParser, parsers.FormParser)
    serializer_class = serializers.Serializer

    @extend_schema(request=_CONVENTION_ENTREE, responses={200: _CONVENTION_SORTIE})
    def get(self, request):
        return _gerer_convention(request)

    @extend_schema(request=_CONVENTION_ENTREE, responses={200: _CONVENTION_SORTIE})
    def put(self, request):
        return _gerer_convention(request)


class PresentationAdminAPIView(generics.GenericAPIView):
    """Lit / met à jour la présentation structurée (sections « À propos ») de MA SGI.

    GET /api/sgi/admin/presentation/
    PUT /api/sgi/admin/presentation/  (JSON complet, toute section)

    La sauvegarde équivaut à la publication : elle est immédiatement
    servie aux investisseurs. Les listes (activités, équipe, références)
    sont remplacées intégralement quand leur clé est présente.
    """

    permission_classes = (permissions.IsAuthenticated, EstAdminSGI)
    serializer_class = PresentationAdminEntreeSerializer

    def _presentation(self, request):
        presentation, _ = _obtenir_ou_creer(
            InformationPresentation, sgi_id=request.user.sgi_id,
        )
        return presentation

    def _sortie(self, presentation):
        return {
            **sections_de_presentation(presentation),
            "date_publication": presentation.date_publication,
        }

    @extend_schema(
        request=False,
        responses={200: PresentationSectionsSerializer},
    )
    def get(self, request):
        return Response(self._sortie(self._presentation(request)))

    @extend_schema(
        request=PresentationAdminEntreeSerializer,
        responses={200: PresentationSectionsSerializer},
    )
    def put(self, request):
        presentation = self._presentation(request)
        serializer = PresentationAdminEntreeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        donnees = serializer.validated_data

        avant = sections_de_presentation(presentation)
        for champ in (
            "forme_sociale", "date_creation_societe", "capital_social",
            "numero_agrement", "date_agrement", "autorite_agrement",
            "mission", "vision", "ancrage_regional",
            "adresse", "telephone", "email_contact", "site_web",
        ):
            if champ in donnees:
                setattr(presentation, champ, donnees[champ])
        presentation.save()

        self._remplacer_listes(presentation, "activites", donnees.get("activites"))
        self._remplacer_listes(presentation, "membres", donnees.get("membres"))
        self._remplacer_listes(presentation, "references", donnees.get("references"))

        apres = sections_de_presentation(presentation)
        journaliser(
            request.user,
            JournalAudit.Action.MODIFICATION_PRESENTATION,
            "InformationPresentation",
            str(presentation.sgi_id),
            avant=avant,
            apres=apres,
            requete=request,
        )
        return Response(self._sortie(presentation))

    @staticmethod
    def _remplacer_listes(presentation, nom, elements):
        """Écrit l'intégralité d'une liste ordonnée si elle est fournie."""
        if elements is None:
            return
        relation = getattr(presentation, nom)
        relation.all().delete()
        for position, element in enumerate(elements):
            relation.create(ordre=position, **element)