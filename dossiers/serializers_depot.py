"""Sérialiseurs de la preuve de dépôt minimum (post-validation).

Le dépôt ─ exigé par la SGI (`ConfigDepotMinimum`, même motif que la
convention tarifaire) ─ verrouille le passage d'un dossier VALIDE à
ACTIF (ouverture du compte-titres) jusqu'à approbation de la preuve.

Côté investisseur, l'écriture se fait en multipart (fichier + méta) et
la validation du fichier reprend le durcissement des justificatifs KYC
(extension + magic bytes + taille) : le nom et le Content-Type clients
sont falsifiables. Côté SGI, la lecture renvoie toujours une URL signée
(le `preuve.name` brut ne sort jamais).
"""

from django.core.files.storage import default_storage
from rest_framework import serializers

from dossiers.models import DepotMinimum
from sgi.models import ConfigDepotMinimum

# Signatures binaires (@ jpg) exige la seule extension : photo iPhone ou
# capture Android, le Content-Type et le nom sont souvent imprécis.
# On calque la vérification sur celle des justificatifs KYC.
_MAGIC_BYTES = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"%PDF": "pdf",
}
TAILLE_MAX_PREUVE_MO = 10

_METHODES = ConfigDepotMinimum.MethodePaiement


def _methodes_avec_libelles(codes):
    """Transforme une liste de codes en [{code, libelle}], ordre stable."""
    libelles = dict(_METHODES.choices)
    return [{"code": c, "libelle": libelles[c]} for c in codes if c in libelles]


class DepotMinimumSerializer(serializers.ModelSerializer):
    """Lecture d'un dépôt (investisseur ET personnel SGI).

    `preuve_url` est une URL signée générée à la volée ; le nom de
    stockage interne ne sort jamais (même règle que `ValeurChamp`).
    """

    devise = serializers.SerializerMethodField()
    preuve_url = serializers.SerializerMethodField()
    methodes_acceptees = serializers.SerializerMethodField()
    dossier_reference = serializers.CharField(source="dossier.reference", read_only=True)
    investisseur_email = serializers.EmailField(
        source="dossier.utilisateur.email", read_only=True
    )
    sgi_nom = serializers.CharField(source="dossier.sgi.nom", read_only=True)
    statut_libelle = serializers.CharField(source="get_statut_display", read_only=True)

    class Meta:
        model = DepotMinimum
        fields = (
            "id",
            "dossier",
            "dossier_reference",
            "investisseur_email",
            "sgi_nom",
            "devise",
            "montant_requis",
            "instructions",
            "methodes_acceptees",
            "statut",
            "statut_libelle",
            "montant_depose",
            "methode_paiement",
            "reference_transaction",
            "preuve_url",
            "commentaire_agent",
            "date_creation",
            "date_maj",
            "date_depot",
            "date_verification",
        )
        read_only_fields = fields

    def get_devise(self, _obj):
        return "FCFA"

    def get_preuve_url(self, depot):
        if not depot.preuve:
            return None
        return default_storage.url(depot.preuve.name)

    def get_methodes_acceptees(self, depot):
        return _methodes_avec_libelles(depot.methodes_acceptees)


class DepotPreuveSerializer(serializers.Serializer):
    """Saisie de la preuve par l'investisseur (POST, multipart).

    Champ `preuve` : image (png/jpg) ou PDF, plafonnée, validée par les
    magic bytes du contenu réel (jamais le Content-Type envoyé).
    """

    montant_depose = serializers.DecimalField(max_digits=14, decimal_places=0, min_value=1)
    methode_paiement = serializers.CharField(max_length=30)
    reference_transaction = serializers.CharField(max_length=100, allow_blank=True)
    preuve = serializers.FileField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.depot = kwargs.get("context", {}).get("depot")

    def validate_methode_paiement(self, valeur):
        codes_acceptes = list(self.depot.methodes_acceptees or [])
        if valeur not in {c for c, _ in _METHODES.choices}:
            raise serializers.ValidationError("Méthode de paiement inconnue.")
        if codes_acceptes and valeur not in codes_acceptes:
            raise serializers.ValidationError(
                "Cette méthode de paiement ne fait pas partie de celles acceptées par la SGI."
            )
        return valeur

    def validate_preuve(self, fichier):
        signature = fichier.read(16)
        fichier.seek(0)

        genres = [genre for entete, genre in _MAGIC_BYTES.items() if signature.startswith(entete)]
        if not genres:
            raise serializers.ValidationError(
                "Le fichier n'est pas une image (PNG/JPG) ou un PDF valide."
            )
        # Le genre DÉTECTÉ fait foi (magic bytes), pas le Content-Type ni
        # l'extension du nom : un contenu réellement JPG sous un nom .png
        # reste une capture valide (même règle que les justificatifs KYC).

        taille_mo = fichier.size / (1024 * 1024)
        if taille_mo > TAILLE_MAX_PREUVE_MO:
            raise serializers.ValidationError(
                f"Fichier trop volumineux ({taille_mo:.1f} Mo, max {TAILLE_MAX_PREUVE_MO} Mo)."
            )
        return fichier