import uuid
from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view, inline_serializer
from rest_framework import generics, permissions, status, parsers
from rest_framework import serializers as drf_serializers
from rest_framework.response import Response

from audit.models import JournalAudit
from audit.services import journaliser
from comptes.permissions import EstInvestisseur
from dossiers.mixins import DossierProprietaireMixin
from dossiers.models import ChampKYC, Dossier, EtapeKYC, ValeurChamp
from dossiers.permissions import PeutAccederAuDossier
from dossiers.services import generer_code_otp, poser_signature_otp, recalculer_progression
from dossiers.workflow import transiter
from dossiers.serializers import (
    DossierCreationSerializer, DossierDetailSerializer, DossierListSerializer,
    EtapeKYCSerializer, ValeurChampSerializer, TeleversementFichierSerializer,
    SignerDossierSerializer, SoumettreDossierSerializer,
)


def _est_uuid_valide(valeur):
    """True si `valeur` est un UUID bien formé (évite un 500 sur filtre)."""
    try:
        uuid.UUID(str(valeur))
        return True
    except (ValueError, AttributeError):
        return False


@extend_schema_view(
    get=extend_schema(
        parameters=[
            OpenApiParameter(
                "sgi", OpenApiTypes.UUID, OpenApiParameter.QUERY,
                required=True, description="Identifiant de la SGI — obligatoire.",
            ),
        ],
    ),
)
class EtapeKYCListAPIView(generics.ListAPIView):
    """Liste les étapes KYC actives d'une SGI.

    GET /api/dossiers/etapes-kyc/?sgi=<uuid>

    Découvre le parcours à remplir avant/pendant la création du
    dossier (UC03/UC04).
    """

    serializer_class = EtapeKYCSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_queryset(self):
        # Le paramètre `sgi` est OBLIGATOIRE : sans lui, la vue renverrait
        # les étapes KYC de TOUTES les SGI à n'importe quel utilisateur
        # authentifié, ce qui constituerait une fuite inter-SGI. On force
        # donc son filtrage plutôt que de tout exposer par défaut.
        sgi_id = self.request.query_params.get("sgi")
        if not sgi_id:
            raise drf_serializers.ValidationError(
                {"sgi": "Le paramètre de requête `sgi` est obligatoire."}
            )
        if not _est_uuid_valide(sgi_id):
            raise drf_serializers.ValidationError(
                {"sgi": "Le paramètre `sgi` doit être un identifiant UUID valide."}
            )

        return (
            EtapeKYC.objects.filter(
                actif=True,
                sgi_id=sgi_id,
                sgi__est_active=True,
            )
            .prefetch_related(
                Prefetch("champs", queryset=ChampKYC.objects.filter(actif=True))
            )
        )


def _appliquer_filtres_attente(qs, params):
    """Filtres de la file d'attente (UC13) : statut, recherche, dates, tri.

    Filtres supportés (tous optionnels, cumulables) :
      - `statut`       répétable (ex: ?statut=SOUMIS&statut=EN_INSTRUCTION) ;
      - `recherche`    sur la référence du dossier OU l'email de l'investisseur ;
      - `date_debut` / `date_fin`  (AAAA-MM-JJ) sur la date de soumission ;
      - `tri=anciens`  les dossiers non encore traités en premier (FCFS).

    Les dates invalides renvoient une erreur 400 explicite plutôt qu'un 500.
    """
    if "statut" in params:
        statuts = params.getlist("statut")
        if statuts:
            qs = qs.filter(statut__in=statuts)

    recherche = params.get("recherche", "").strip()
    if recherche:
        qs = qs.filter(
            Q(reference__icontains=recherche)
            | Q(utilisateur__email__icontains=recherche)
        )

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
        qs = qs.filter(date_soumission__date__gte=date_debut)
    if date_fin := _lire_date("date_fin"):
        qs = qs.filter(date_soumission__date__lte=date_fin)

    if params.get("tri") == "anciens":
        # Le plus ancien SOUMIS d'abord : traite les dossiers reçus dans
        # l'ordre d'arrivée (les brouillons vides se retrouvent en queue).
        qs = qs.order_by("date_soumission", "date_creation")

    return qs


@extend_schema_view(
    get=extend_schema(
        parameters=[
            OpenApiParameter(
                "statut", OpenApiTypes.STR, OpenApiParameter.QUERY,
                many=True, description="Filtre de statut, répétable "
                "(ex: ?statut=SOUMIS&statut=EN_INSTRUCTION).",
            ),
            OpenApiParameter(
                "recherche", OpenApiTypes.STR, OpenApiParameter.QUERY,
                description="Référence du dossier ou email de l'investisseur.",
            ),
            OpenApiParameter(
                "date_debut", OpenApiTypes.DATE, OpenApiParameter.QUERY,
                description="Date de soumission minimale (AAAA-MM-JJ).",
            ),
            OpenApiParameter(
                "date_fin", OpenApiTypes.DATE, OpenApiParameter.QUERY,
                description="Date de soumission maximale (AAAA-MM-JJ).",
            ),
            OpenApiParameter(
                "tri", OpenApiTypes.STR, OpenApiParameter.QUERY,
                description="`anciens` : dossiers non traités en premier (FCFS).",
            ),
        ],
    ),
)
class DossierListCreateAPIView(generics.ListCreateAPIView):
    """Liste (cloisonnée) et création de dossiers.

    GET  /api/dossiers/dossiers/  → selon le rôle (UC07 pour le personnel SGI)
    POST /api/dossiers/dossiers/  → création par un investisseur (UC03)
    """

    def get_queryset(self):
        qs = Dossier.objects.visible_pour(self.request.user).select_related(
            "utilisateur", "sgi", "etape_courante"
        )
        return _appliquer_filtres_attente(qs, self.request.query_params)

    def get_serializer_class(self):
        return DossierCreationSerializer if self.request.method == "POST" else DossierListSerializer

    def get_permissions(self):
        if self.request.method == "POST":
            return [permissions.IsAuthenticated(), EstInvestisseur()]
        return [permissions.IsAuthenticated()]

    def perform_create(self, serializer):
        dossier = serializer.save()
        # Création d'un dossier = action sensible : tracée au journal.
        journaliser(
            self.request.user,
            JournalAudit.Action.CREATION_DOSSIER,
            "Dossier",
            str(dossier.pk),
            apres={"reference": dossier.reference, "sgi_id": str(dossier.sgi_id)},
            requete=self.request,
        )


class DossierDetailAPIView(generics.RetrieveAPIView):
    """Détail d'un dossier, avec ses valeurs de champs.

    GET /api/dossiers/dossiers/<id>/
    """

    serializer_class = DossierDetailSerializer
    permission_classes = (permissions.IsAuthenticated, PeutAccederAuDossier)
    queryset = Dossier.objects.select_related("utilisateur", "sgi").prefetch_related(
        "valeurs_champs__champ"
    )


class ValeurChampListCreateAPIView(DossierProprietaireMixin, generics.ListCreateAPIView):
    """Liste/écrit les valeurs de champs KYC d'un dossier (UC04).

    GET  /api/dossiers/dossiers/<dossier_pk>/valeurs/
    POST /api/dossiers/dossiers/<dossier_pk>/valeurs/

    Seul l'investisseur propriétaire peut écrire, uniquement tant que
    le dossier est en BROUILLON. Un POST sur un champ déjà rempli fait
    un upsert (mise à jour), pas un doublon.
    """

    serializer_class = ValeurChampSerializer
    permission_classes = (permissions.IsAuthenticated, EstInvestisseur)

    def get_queryset(self):
        return ValeurChamp.objects.filter(dossier=self.get_dossier()).select_related("champ")

    def create(self, request, *args, **kwargs):
        dossier = self.get_dossier()
        if conflit := self.verifier_dossier_modifiable(dossier):
            return conflit

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        champ = serializer.validated_data["champ"]

        existante = ValeurChamp.objects.filter(dossier=dossier, champ=champ).first()
        # Si l'agent avait commenté ce champ (demande de correction),
        # la ressaisie de l'investisseur le marque comme corrigé, prêt
        # pour une nouvelle relecture (UC09).
        est_corrige = bool(existante and existante.commentaire_agent)

        try:
            instance, cree = ValeurChamp.objects.update_or_create(
                dossier=dossier,
                champ=champ,
                defaults={
                    "valeur": serializer.validated_data.get("valeur", ""),
                    "est_corrige": est_corrige,
                },
            )
        except ValidationError as exc:
            # 'ValeurChamp.save()' appelle 'full_clean()' : sans cette
            # conversion, une erreur métier remonterait en 500 au lieu
            # d'une réponse 400 exploitable par le client.
            raise drf_serializers.ValidationError(
                exc.message_dict if hasattr(exc, "error_dict") else exc.messages
            )

        # Le remplissage change la complétude du dossier : on recalcule
        # le pourcentage de progression immédiatement.
        recalculer_progression(dossier)

        return Response(
            self.get_serializer(instance).data,
            status=status.HTTP_201_CREATED if cree else status.HTTP_200_OK,
        )



class ValeurChampFichierUploadAPIView(DossierProprietaireMixin, generics.GenericAPIView):
    """Téléverse un justificatif vers MinIO pour un champ KYC de type FICHIER (UC05).

    POST /api/dossiers/dossiers/<dossier_pk>/valeurs/fichier/
    Content-Type: multipart/form-data
    Champs : champ=<uuid>, fichier=<binaire>
    """

    serializer_class = TeleversementFichierSerializer
    permission_classes = (permissions.IsAuthenticated, EstInvestisseur)
    parser_classes = (parsers.MultiPartParser, parsers.FormParser)

    def post(self, request, *args, **kwargs):
        dossier = self.get_dossier()
        if conflit := self.verifier_dossier_modifiable(dossier):
            return conflit

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        champ = serializer.validated_data["champ"]
        fichier = serializer.validated_data["fichier"]

        # Nom de fichier généré côté serveur (jamais le nom d'origine
        # du client) : évite les collisions et toute tentative
        # d'injection de chemin (path traversal) via le nom envoyé.
        extension = fichier.name.rsplit(".", 1)[-1].lower()
        chemin = f"dossiers/{dossier.id}/{champ.code}/{uuid.uuid4()}.{extension}"

        # On mémorise l'éventuel fichier déjà présent AVANT l'écriture
        # pour pouvoir le supprimer ensuite : sans ce nettoyage, un
        # remplacement laisserait l'ancien objet orphelin sur MinIO.
        existante = ValeurChamp.objects.filter(dossier=dossier, champ=champ).first()
        ancien_fichier = existante.fichier if existante else ""

        chemin_enregistre = default_storage.save(chemin, fichier)

        try:
            instance, _ = ValeurChamp.objects.update_or_create(
                dossier=dossier, champ=champ,
                defaults={
                    "fichier": chemin_enregistre,
                    "valeur": "",
                    # Un remplacement après demande de correction doit
                    # rester visible de l'agent : on passe est_corrige à
                    # True quand un commentaire attend une relecture.
                    "est_corrige": bool(existante and existante.commentaire_agent),
                },
            )
        except ValidationError as exc:
            # La validation métier a échoué : le fichier qu'on venait
            # d'écrire ne sera référencé par personne, on le supprime
            # pour ne pas le laisser orphelin.
            default_storage.delete(chemin_enregistre)
            raise drf_serializers.ValidationError(
                exc.message_dict if hasattr(exc, "error_dict") else exc.messages
            )

        # Suppression de l'ancien justificatif remplacé (best-effort :
        # une erreur de stockage ne doit pas invalider l'upload réussi).
        if ancien_fichier and ancien_fichier != chemin_enregistre:
            try:
                default_storage.delete(ancien_fichier)
            except Exception:
                pass

        recalculer_progression(dossier)

        return Response(
            {
                "id": instance.id,
                "champ": champ.id,
                "url_signee": default_storage.url(chemin_enregistre),
            },
            status=status.HTTP_201_CREATED,
        )


class ValeurChampFichierUrlAPIView(generics.GenericAPIView):
    """Génère une URL signée temporaire pour consulter un document déjà téléversé.

    GET /api/dossiers/dossiers/<dossier_pk>/valeurs/<valeur_pk>/url/

    Accessible à l'investisseur propriétaire ET au personnel de la SGI
    concernée (l'agent doit pouvoir relire les justificatifs, UC08).
    """

    permission_classes = (permissions.IsAuthenticated,)

    serializer_class = drf_serializers.Serializer

    @extend_schema(responses={200: inline_serializer(
        "UrlSignee",
        {"url_signee": drf_serializers.CharField()},
    )})
    def get(self, request, dossier_pk, valeur_pk):
        dossier = get_object_or_404(Dossier, pk=dossier_pk)
        if not PeutAccederAuDossier().has_object_permission(request, self, dossier):
            self.permission_denied(request, message="Vous n'avez pas accès à ce dossier.")

        valeur = get_object_or_404(ValeurChamp, pk=valeur_pk, dossier=dossier)
        if not valeur.fichier:
            return Response({"detail": "Aucun fichier associé à ce champ."}, status=status.HTTP_404_NOT_FOUND)

        return Response({"url_signee": default_storage.url(valeur.fichier)})


class DossierGenererOtpAPIView(DossierProprietaireMixin, generics.GenericAPIView):
    """Génère un code OTP pour la signature électronique (preuve serveur).

    POST /api/dossiers/dossiers/<id>/generer-otp/

    Le code (6 chiffres, valable 5 minutes) n'est jamais stocké en clair
    : seul son hash PBKDF2 est conservé. En production, le code partirait
    par SMS/email (canal hors-bande) ; l'API ne le renvoie en clair que
    quand `DEBUG=True` (développement/démo). Limité en débit (scope
    `otp`) pour ralentir un éventuel usage abusif.
    """

    permission_classes = (permissions.IsAuthenticated, EstInvestisseur)
    throttle_scope = "otp"

    serializer_class = drf_serializers.Serializer

    @extend_schema(responses={200: inline_serializer(
        "CodeOtp",
        {
            "code": drf_serializers.CharField(required=False, allow_null=True),
            "expiration": drf_serializers.DateTimeField(),
        },
    )})
    def post(self, request, dossier_pk):
        dossier = self.get_dossier()
        if conflit := self.verifier_dossier_modifiable(dossier):
            return conflit
        code = generer_code_otp(dossier)
        return Response(
            {
                # En production, le code est acheminé par un canal
                # hors-bande (SMS/email) : jamais renvoyé en clair.
                "code": code if settings.DEBUG else None,
                "expiration": dossier.otp_expiration,
            },
            status=status.HTTP_200_OK,
        )


class DossierSignerAPIView(DossierProprietaireMixin, generics.GenericAPIView):
    """Pose la signature électronique OTP après vérification serveur.

    POST /api/dossiers/dossiers/<id>/signer/  (JSON : otp_code)

    Vérifie le code reçu (valide, non expiré, à usage unique) puis pose
    la preuve chaînée : `donnee_signature` = hash de la référence du
    dossier, de l'utilisateur, de la SGI, de l'horodatage, de l'IP et du
    code. Le dossier n'est soumissible et validable qu'une fois signé.
    """

    serializer_class = SignerDossierSerializer
    permission_classes = (permissions.IsAuthenticated, EstInvestisseur)
    throttle_scope = "otp"

    def post(self, request, dossier_pk):
        dossier = self.get_dossier()
        if conflit := self.verifier_dossier_modifiable(dossier):
            return conflit

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            preuve = poser_signature_otp(dossier, serializer.validated_data["otp_code"], request)
        except ValidationError as exc:
            raise drf_serializers.ValidationError(exc.messages)

        return Response(preuve, status=status.HTTP_200_OK)


class DossierAccepterConventionAPIView(DossierProprietaireMixin, generics.GenericAPIView):
    """Accord irréversible de l'investisseur sur la convention tarifaire (UC16).

    POST /api/dossiers/dossiers/<id>/accepter-convention/

    Exige une convention publiée par la SGI destinataire (409 sinon),
    un dossier en BROUILLON ou REJETE (409 sinon). Idempotent : une
    seconde acceptation renvoie simplement l'état actuel.
    """

    permission_classes = (permissions.IsAuthenticated, EstInvestisseur)
    serializer_class = DossierDetailSerializer

    @extend_schema(responses={200: inline_serializer(
        "AcceptationConvention",
        {
            "detail": drf_serializers.CharField(),
            "convention_acceptee": drf_serializers.BooleanField(),
        },
    )})
    def post(self, request, dossier_pk):
        dossier = self.get_dossier()
        if dossier.statut not in (Dossier.Statut.BROUILLON, Dossier.Statut.REJETE):
            return Response(
                {"detail": "Seul un dossier en BROUILLON ou REJETE peut accepter "
                           "la convention."},
                status=status.HTTP_409_CONFLICT,
            )

        if not (
            hasattr(dossier.sgi, "convention")
            and dossier.sgi.convention.fichier_pdf
        ):
            return Response(
                {"detail": "Aucune convention tarifaire publiée par cette SGI."},
                status=status.HTTP_409_CONFLICT,
            )

        if not dossier.convention_acceptee:
            dossier.convention_acceptee = True
            dossier.save(update_fields=["convention_acceptee"])
            journaliser(
                request.user,
                JournalAudit.Action.ACCEPTATION_CONVENTION,
                "Dossier",
                str(dossier.pk),
                avant={"convention_acceptee": False},
                apres={"convention_acceptee": True},
                requete=request,
            )

        return Response({"detail": "Convention tarifaire acceptée.",
                         "convention_acceptee": dossier.convention_acceptee})


class DossierSoumettreAPIView(DossierProprietaireMixin, generics.GenericAPIView):
    """Soumission d'un dossier par son investisseur (UC10).

    POST /api/dossiers/dossiers/<id>/soumettre/

    Autorise les départs BROUILLON (première soumission) et REJETE
    (resoumission corrigée — UC12). Refuse les autres états (409),
    refuse la soumission si le dossier est incomplet (400, précondition
    vérifiée par la machine à états). La signature électronique est
    posée AVANT, via `generer-otp/` puis `signer/`.
    """

    serializer_class = SoumettreDossierSerializer
    permission_classes = (permissions.IsAuthenticated, EstInvestisseur)

    def post(self, request, dossier_pk):
        dossier = self.get_dossier()

        if dossier.statut not in (Dossier.Statut.BROUILLON, Dossier.Statut.REJETE):
            return Response(
                {"detail": "Seul un dossier en BROUILLON ou REJETE peut être soumis."},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            transiter(
                dossier, Dossier.Statut.SOUMIS,
                utilisateur=request.user, requete=request,
            )
        except ValidationError as exc:
            raise drf_serializers.ValidationError(exc.messages)

        dossier.refresh_from_db()
        return Response(
            DossierDetailSerializer(dossier).data,
            status=status.HTTP_200_OK,
        )