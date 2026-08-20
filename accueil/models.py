"""Page d'accueil publique, paramétrable par l'Admin Général (UC Accueil).

La homepage de la plateforme est une liste ordonnée de blocs typés
(HERO, RÉASSURANCE, CHIFFRES, ÉTAPES, SÉCURITÉ, TÉMOIGNAGES, FAQ,
APPEL_ACTION). L'Admin Général édite chaque bloc (texte, contenu
structuré, image), en déplace l'ordre, l'active/désactive et publie
l'ensemble ; le public ne voit que les blocs actifs et publiés.

Conçu sur le même pattern que la présentation SGI (brouillon →
publication, audit avant/après) pour rester conforme CREPMF.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _


class BlocAccueil(models.Model):
    """Un bloc de la page d'accueil (un seul par type)."""

    class TypeBloc(models.TextChoices):
        HERO = "HERO", _("Accroche principale")
        REASSURANCE = "REASSURANCE", _("Bandeau de réassurance")
        CHIFFRES = "CHIFFRES", _("Chiffres clés et partenaires")
        ETAPES = "ETAPES", _("Comment ça marche")
        SECURITE = "SECURITE", _("Sécurité et conformité")
        TEMOIGNAGES = "TEMOIGNAGES", _("Témoignages")
        FAQ = "FAQ", _("Questions fréquentes")
        APPEL_ACTION = "APPEL_ACTION", _("Appel à l'action final")

    # Structure attendue de `contenu` (ValidationError du serializer) :
    #   HERO         : {"cta_principal", "lien_principal",
    #                  "cta_secondaire", "lien_secondaire"}
    #   REASSURANCE  : {"mentions": ["…", …]}
    #   CHIFFRES     : {"chiffres": [{"valeur", "libelle"}, …]}
    #   ETAPES       : {"etapes": [{"titre", "description"}, …]}
    #   SECURITE     : {"cartes": [{"titre", "description"}, …]}
    #   TEMOIGNAGES  : {"temoignages": [{"nom", "role", "texte"}, …]}
    #   FAQ          : {"questions": [{"question", "reponse"}, …]}
    #   APPEL_ACTION : {"cta", "lien", "slogan"}
    TYPE_TOUCHE = {
        TypeBloc.HERO,
        TypeBloc.REASSURANCE,
        TypeBloc.CHIFFRES,
        TypeBloc.ETAPES,
        TypeBloc.SECURITE,
        TypeBloc.TEMOIGNAGES,
        TypeBloc.FAQ,
        TypeBloc.APPEL_ACTION,
    }

    type = models.CharField(
        _("Type de bloc"), max_length=30, choices=TypeBloc.choices,
        unique=True, db_index=True,
    )
    titre = models.CharField(_("Titre"), max_length=200, blank=True)
    contenu = models.JSONField(
        _("Contenu structuré"), default=dict, blank=True,
        help_text=_("Structure propre au type de bloc (voir serializers.py)."),
    )
    image = models.ImageField(
        _("Image"), upload_to="accueil/", blank=True, null=True,
    )
    actif = models.BooleanField(_("Actif"), default=True)
    ordre = models.PositiveIntegerField(_("Ordre d'affichage"), default=0)
    date_publication = models.DateTimeField(
        _("Date de publication"), null=True, blank=True,
        help_text=_("Renseignée à la première publication (brouillon → public)."),
    )
    date_maj = models.DateTimeField(_("Dernière modification"), auto_now=True)

    class Meta:
        verbose_name = _("Bloc de la page d'accueil")
        verbose_name_plural = _("Blocs de la page d'accueil")
        ordering = ["ordre", "type"]

    def est_publie(self):
        return self.date_publication is not None

    def __str__(self):
        return f"{self.get_type_display()} ({self.type})"