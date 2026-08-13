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
    avec sa SGI. Servie par la fiche publique de la SGI au moment de
    l'adhésion de l'investisseur.
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

    contenu = models.TextField(
        _("Contenu marketing"),
        blank=True,
        help_text=_("Texte de présentation de la SGI pour les investisseurs."),
    )

    date_publication = models.DateTimeField(
        _("Date de publication"),
        auto_now_add=True,
    )

    class Meta:
        verbose_name = _("Information de présentation")
        verbose_name_plural = _("Informations de présentation")

    def __str__(self):
        return f"Présentation de {self.sgi.nom}"