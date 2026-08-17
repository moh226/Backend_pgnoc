"""
Modèle SGI (Société de Gestion et d'Intermédiation).
"""

from django.db import models
import uuid
from django.utils.translation import gettext_lazy as _


class SGI(models.Model):
    """Une Société de Gestion et d'Intermédiation cliente de la plateforme.

    C'est l'entité racine du cloisonnement multi-tenant : toute
    donnée métier (utilisateurs internes, dossiers, formulaires)
    est rattachée directement ou indirectement à une SGI unique.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("Identifiant"),
    )

    nom = models.CharField(
        _("Nom"),
        max_length=255
    )

    code_sgi = models.CharField(
        _("Code SGI"),
        max_length=20,
        unique=True,
        db_index=True,
        help_text=_("Code réglementaire unique attribué par le CREPMF."),
    )

    logo = models.ImageField(
        _("Logo"),
        upload_to="sgi/logos/",
        blank=True,
        null=True,
    )

    est_active = models.BooleanField(
        _("Active"),
        default=True,
        help_text=_("Une SGI désactivée ne peut plus recevoir de nouveaux dossiers."),
    )

    date_creation = models.DateTimeField(
        _("Date de création"),
        auto_now_add=True
    )

    class Meta:
        verbose_name = _("SGI")
        verbose_name_plural = _("SGI")
        ordering = ["nom"]

    def __str__(self):
        return f"{self.nom} ({self.code_sgi})"


class ConventionTarifaire(models.Model):
    """Convention tarifaire publiée par une SGI (UC16).

    Relation OneToOne (composition) avec la SGI : si la SGI est
    supprimée, sa convention l'est aussi (section 6.2.2 du document de
    conception). Tant qu'aucune convention n'est publiée, l'accord n'est
    pas exigible ; dès qu'elle l'est, la soumission des dossiers de
    cette SGI exige l'acceptation de l'investisseur.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("Identifiant"),
    )

    sgi = models.OneToOneField(
        SGI,
        verbose_name=_("SGI"),
        related_name="convention",
        on_delete=models.CASCADE,
    )

    titre = models.CharField(
        _("Titre"),
        max_length=255,
        blank=True,
        help_text=_("Intitulé de la convention tel qu'affiché à l'investisseur."),
    )

    fichier_pdf = models.FileField(
        _("Fichier PDF"),
        upload_to="sgi/conventions/",
        blank=True,
        help_text=_("Document PDF de la convention tarifaire en vigueur."),
    )

    date_publication = models.DateTimeField(
        _("Date de publication"),
        auto_now_add=True,
    )

    date_modification = models.DateTimeField(
        _("Date de modification"),
        auto_now=True,
    )

    class Meta:
        verbose_name = _("Convention tarifaire")
        verbose_name_plural = _("Conventions tarifaires")

    def __str__(self):
        return f"Convention de {self.sgi.nom}"

    def est_publiee(self):
        """Une convention n'est publiée que si son PDF est déposé.

        Le titre seul est une métadonnée d'affichage : il n'engage pas
        l'investisseur. C'est bien le PDF qui fait foi, et c'est déjà le
        critère utilisé par la fiche publique (`SGIFicheSerializer`,
        `signe_requis`) et par le blocage de soumission du workflow.
        """
        return bool(self.fichier_pdf)


class InformationPresentation(models.Model):
    """Présentation commerciale (contenu marketing) d'une SGI (UC16).

    Même relation OneToOne/composition que la convention : supprimée
    avec sa SGI. Servie par la fiche de la SGI au moment de l'adhésion
    de l'investisseur. Structurée en sections : identité juridique,
    agrément, mission/vision, activités, équipe, ancrage régional,
    références et contact.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("Identifiant"),
    )

    sgi = models.OneToOneField(
        SGI,
        verbose_name=_("SGI"),
        related_name="presentation",
        on_delete=models.CASCADE,
    )

    # --- Identité juridique ---
    forme_sociale = models.CharField(_("Forme sociale"), max_length=50, blank=True)
    date_creation_societe = models.DateField(
        _("Date de création"), null=True, blank=True,
    )
    capital_social = models.CharField(_("Capital social"), max_length=100, blank=True)

    # --- Agrément réglementaire ---
    numero_agrement = models.CharField(_("Numéro d'agrément"), max_length=100, blank=True)
    date_agrement = models.DateField(_("Date d'agrément"), null=True, blank=True)
    autorite_agrement = models.CharField(
        _("Autorité d'agrément"),
        max_length=100,
        blank=True,
        help_text=_("Ex : AMF-UEMOA (ex-CREPMF) — rappelée par défaut côté affichage."),
    )

    # --- Mission / vision ---
    mission = models.TextField(_("Mission"), blank=True)
    vision = models.TextField(_("Vision"), blank=True)

    # --- Ancrage régional ---
    ancrage_regional = models.TextField(_("Ancrage régional"), blank=True)

    # --- Contact et accès ---
    adresse = models.CharField(_("Adresse"), max_length=255, blank=True)
    telephone = models.CharField(_("Téléphone"), max_length=30, blank=True)
    email_contact = models.EmailField(_("Email de contact"), blank=True)
    site_web = models.URLField(_("Site web"), blank=True)

    # Ancien champ texte libre : conservé pour la rétrocompatibilité
    # (fallback de la fiche tant que les sections ne sont pas remplies).
    contenu = models.TextField(
        _("Contenu marketing (historique)"),
        blank=True,
        help_text=_("Ancien texte libre, conservé à titre de récupération."),
    )

    date_publication = models.DateTimeField(
        _("Date de publication"),
        auto_now=True,
    )

    class Meta:
        verbose_name = _("Information de présentation")
        verbose_name_plural = _("Informations de présentation")

    def __str__(self):
        return f"Présentation de {self.sgi.nom}"

    def est_renseignee(self) -> bool:
        """Vrai si au moins une section porte une information."""
        champs = (
            self.forme_sociale, self.date_creation_societe, self.capital_social,
            self.numero_agrement, self.date_agrement, self.autorite_agrement,
            self.mission, self.vision, self.ancrage_regional,
            self.adresse, self.telephone, self.email_contact, self.site_web,
        )
        return any(champs) or self.activites.exists() or self.membres.exists() or self.references.exists()



class ActivitePresentation(models.Model):
    """Pôle d'activité mis en avant dans la présentation d'une SGI.

    Liste ordonnée par `ordre` : intermédiation, ingénierie financière,
    conseil stratégique…
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("Identifiant"),
    )

    presentation = models.ForeignKey(
        InformationPresentation,
        verbose_name=_("Présentation"),
        related_name="activites",
        on_delete=models.CASCADE,
    )

    titre = models.CharField(_("Titre"), max_length=200)
    description = models.TextField(_("Description"), blank=True)
    ordre = models.PositiveSmallIntegerField(_("Ordre"), default=0)

    class Meta:
        ordering = ["ordre", "id"]
        verbose_name = _("Activité présentée")
        verbose_name_plural = _("Activités présentées")

    def __str__(self):
        return self.titre


class MembreEquipe(models.Model):
    """Membre dirigeant mis en avant dans la présentation d'une SGI."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("Identifiant"),
    )

    presentation = models.ForeignKey(
        InformationPresentation,
        verbose_name=_("Présentation"),
        related_name="membres",
        on_delete=models.CASCADE,
    )

    nom = models.CharField(_("Nom"), max_length=200)
    fonction = models.CharField(_("Fonction"), max_length=200, blank=True)
    ordre = models.PositiveSmallIntegerField(_("Ordre"), default=0)

    class Meta:
        ordering = ["ordre", "id"]
        verbose_name = _("Membre de l'équipe")
        verbose_name_plural = _("Membres de l'équipe")

    def __str__(self):
        return f"{self.nom} — {self.fonction or 'sans fonction'}"


class ReferencePresentation(models.Model):
    """Référence / réalisation ou distinction citée par la SGI."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("Identifiant"),
    )

    presentation = models.ForeignKey(
        InformationPresentation,
        verbose_name=_("Présentation"),
        related_name="references",
        on_delete=models.CASCADE,
    )

    titre = models.CharField(_("Titre"), max_length=200)
    annee = models.CharField(_("Année"), max_length=20, blank=True)
    description = models.TextField(_("Description"), blank=True)
    ordre = models.PositiveSmallIntegerField(_("Ordre"), default=0)

    class Meta:
        ordering = ["ordre", "id"]
        verbose_name = _("Référence présentée")
        verbose_name_plural = _("Références présentées")

    def __str__(self):
        return self.titre
