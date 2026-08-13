"""Modèles du domaine Dossiers : parcours KYC dynamique (EtapeKYC/ChampKYC).

Chaque SGI configure librement ses propres étapes et champs KYC
(aucun catalogue partagé entre SGI), conformément au diagramme de
classe validé pour le Sprint 2.
"""

import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _


class DossierQuerySet(models.QuerySet):
    """QuerySet exposant le cloisonnement multi-tenant pour les dossiers."""

    def visible_pour(self, utilisateur):
        """Filtre les dossiers visibles selon le rôle de `utilisateur`.

        - ADMIN_GENERAL : tout.
        - AGENT_SGI / ADMIN_SGI : uniquement les dossiers de SA SGI.
        - INVESTISSEUR : uniquement SES PROPRES dossiers.
        """
        if utilisateur.est_admin_general:
            return self.all()
        if utilisateur.est_admin_sgi or utilisateur.est_agent_sgi:
            if utilisateur.sgi_id is None:
                return self.none()
            return self.filter(sgi_id=utilisateur.sgi_id)
        if utilisateur.est_investisseur:
            return self.filter(utilisateur_id=utilisateur.id)
        return self.none()


class DossierManager(models.Manager.from_queryset(DossierQuerySet)):
    pass


class EtapeKYC(models.Model):
    """Une étape du parcours KYC configurée par une SGI.

    Contrairement à la version précédente, aucune distinction de type
    de compte (Physique/Morale/Mineur) n'est portée ici : cette
    distinction se fait désormais via un ChampKYC "Type de personne"
    normal, dont dépendent conditionnellement les autres champs
    (champ_parent/valeur_declencheur)
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("Identifiant"),
    )
    sgi = models.ForeignKey(
        "sgi.SGI",
        verbose_name=_("SGI propriétaire"),
        related_name="etapes_kyc",
        on_delete=models.CASCADE,
    )
    nom = models.CharField(
        _("Nom de l'étape"),
        max_length=255)
    ordre = models.PositiveIntegerField(
        _("Ordre"),
        default=0,
        help_text=_("Détermine la position de l'étape dans le parcours."),
    )
    actif = models.BooleanField(
        _("Actif"),
        default=True,
        help_text=_("Seule une étape active est proposée aux nouveaux dossiers."),
    )
    date_creation = models.DateTimeField(
        _("Date de création"),
        auto_now_add=True)

    class Meta:
        verbose_name = _("Étape KYC")
        verbose_name_plural = _("Étapes KYC")
        ordering = ["sgi", "ordre"]
        constraints = [
            models.UniqueConstraint(fields=["sgi", "ordre"],
                                    name="unique_ordre_par_sgi"),
        ]

    def __str__(self):
        return f"{self.nom} — {self.sgi.code_sgi}"


class ChampKYC(models.Model):
    """Un champ dynamique appartenant à une EtapeKYC.

    Structure EAV : ce modèle ne stocke que la DÉFINITION du champ.
    Les valeurs saisies par les investisseurs sont dans `ValeurChamp`
    (Étape 2.3). Supporte l'affichage conditionnel via `champ_parent`
    + `valeur_declencheur', et la validation de fichiers via
    `formats_acceptes'/'taille_max_mo' pour le type FICHIER.
    """

    class TypeChamp(models.TextChoices):
        TEXTE_COURT = "TEXTE_COURT", _("Texte court")
        TEXTE_LONG = "TEXTE_LONG", _("Texte long")
        NOMBRE = "NOMBRE", _("Nombre")
        DATE = "DATE", _("Date")
        BOOLEEN = "BOOLEEN", _("Case à cocher")
        CHOIX_UNIQUE = "CHOIX_UNIQUE", _("Choix unique")
        CHOIX_MULTIPLE = "CHOIX_MULTIPLE", _("Choix multiple")
        FICHIER = "FICHIER", _("Fichier joint")

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("Identifiant"),
    )
    etape = models.ForeignKey(
        EtapeKYC,
        verbose_name=_("Étape KYC"),
        related_name="champs",
        on_delete=models.CASCADE,
    )
    code = models.SlugField(
        _("Code technique"),
        max_length=100,
        help_text=_(
            "Généré automatiquement depuis le nom si non fourni. Clé "
            "stable utilisée par ValeurChamp — ne doit jamais changer "
            "après création."
        ),
    )
    nom = models.CharField(_("Nom affiché"),
                           max_length=255)
    type = models.CharField(_("Type de champ"),
                            max_length=20,
                            choices=TypeChamp.choices)
    obligatoire = models.BooleanField(_("Obligatoire"), default=True)
    ordre = models.PositiveIntegerField(_("Ordre d'affichage"), default=0)
    justification = models.TextField(
        _("Justification"),
        blank=True,
        help_text=_("Explique à l'investisseur pourquoi cette information est demandée."),
    )
    options_choix = models.JSONField(
        _("Options (CHOIX_UNIQUE / CHOIX_MULTIPLE)"),
        blank=True,
        null=True,
        help_text=_("Liste de chaînes, ex: [\"Salarié\", \"Indépendant\", \"Retraité\"]."),
    )
    champ_parent = models.ForeignKey(
        "self",
        verbose_name=_("Champ parent (condition d'affichage)"),
        related_name="champs_enfants",
        on_delete=models.CASCADE,
        null=True, blank=True,
        help_text=_(
            "Si renseigné, ce champ ne s'affiche que si `champ_parent` "
            "a pour valeur `valeur_declencheur`."
        ),
    )
    valeur_declencheur = models.CharField(
        _("Valeur déclencheur"),
        max_length=255,
        blank=True,
        help_text=_("Valeur du champ parent qui déclenche l'affichage de ce champ."),
    )
    formats_acceptes = models.CharField(
        _("Formats de fichiers acceptés"),
        max_length=255,
        blank=True,
        help_text=_("Ex: 'pdf,jpg,png'. Utilisé uniquement pour le type FICHIER."),
    )
    taille_max_mo = models.PositiveIntegerField(
        _("Taille maximale (Mo)"),
        null=True,
        blank=True,
        help_text=_("Utilisé uniquement pour le type FICHIER."),
    )
    actif = models.BooleanField(_("Actif"), default=True)

    class Meta:
        verbose_name = _("Champ KYC")
        verbose_name_plural = _("Champs KYC")
        ordering = ["etape", "ordre"]
        constraints = [
            models.UniqueConstraint(
                fields=["etape", "code"], name="unique_code_par_etape",
            ),
        ]

    def clean(self):
        """Valide la cohérence entre le type de champ et ses options associées."""
        types_avec_options = (self.TypeChamp.CHOIX_UNIQUE, self.TypeChamp.CHOIX_MULTIPLE)
        if self.type in types_avec_options:
            if not self.options_choix or not isinstance(self.options_choix, list):
                raise ValidationError(
                    {"options_choix": _("Une liste d'options non vide est requise pour ce type.")}
                )
        elif self.options_choix:
            raise ValidationError(
                {"options_choix": _("Les options ne sont utilisables qu'avec CHOIX_UNIQUE/CHOIX_MULTIPLE.")}
            )

        if self.type == self.TypeChamp.FICHIER:
            if not self.formats_acceptes:
                raise ValidationError(
                    {"formats_acceptes": _("Les formats acceptés sont requis pour un champ FICHIER.")}
                )
        elif self.formats_acceptes or self.taille_max_mo:
            raise ValidationError(_("`formats_acceptes`/`taille_max_mo` ne s'utilisent qu'avec le type FICHIER."))

        if self.champ_parent_id:
            if self.champ_parent_id == self.pk:
                raise ValidationError({"champ_parent": _("Un champ ne peut pas être son propre parent.")})
            if self.champ_parent.etape_id != self.etape_id:
                raise ValidationError({"champ_parent": _("Le champ parent doit appartenir à la même étape.")})
            if not self.valeur_declencheur:
                raise ValidationError(
                    {"valeur_declencheur": _("Une valeur déclencheur est requise si un champ parent est défini.")}
                )
        elif self.valeur_declencheur:
            raise ValidationError(
                {"valeur_declencheur": _("Une valeur déclencheur nécessite un champ parent.")}
            )

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = slugify(self.nom, allow_unicode=False).replace("-", "_")
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.nom} ({self.etape.nom})"



class Dossier(models.Model):
    """Un dossier d'ouverture de compte-titres soumis par un investisseur.

    Représente l'agrégat central du domaine métier : lié à un
    Utilisateur (investisseur), une SGI, et suit un cycle de vie
    strict par statuts (section 5 du cahier des charges).
    """

    class Statut(models.TextChoices):
        BROUILLON = "BROUILLON", _("Brouillon")
        SOUMIS = "SOUMIS", _("Soumis")
        EN_INSTRUCTION = "EN_INSTRUCTION", _("En instruction")
        VALIDE = "VALIDE", _("Validé")
        REJETE = "REJETE", _("Rejeté")

    class TypeSignature(models.TextChoices):
        OTP = "OTP", _("OTP")
        BIOMETRIQUE = "BIOMETRIQUE", _("Biométrique")

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("Identifiant"),
    )
    reference = models.CharField(
        _("Référence"),
        max_length=30,
        unique=True,
        editable=False,
        help_text=_("Référence humaine générée automatiquement (ex: PGNOC-2026-000123)."),
    )
    utilisateur = models.ForeignKey(
        "comptes.Utilisateur",
        verbose_name=_("Investisseur"),
        related_name="dossiers",
        on_delete=models.PROTECT,
        limit_choices_to={"role__code": "INVESTISSEUR"},
    )
    sgi = models.ForeignKey(
        "sgi.SGI",
        verbose_name=_("SGI destinataire"),
        related_name="dossiers",
        on_delete=models.PROTECT,
    )

    etape_courante = models.ForeignKey(
        EtapeKYC,
        verbose_name=_("Étape courante"),
        related_name="dossiers_en_cours",
        on_delete=models.PROTECT,
        null=True, blank=True,
        help_text=_("Étape à laquelle en est l'investisseur pendant la saisie (statut BROUILLON)."),
    )

    agent = models.ForeignKey(
        "comptes.Utilisateur", verbose_name=_("Agent en charge"),
        related_name="dossiers_instruits", on_delete=models.PROTECT,
        null=True, blank=True,
        limit_choices_to={"role__code": "AGENT_SGI"},
        help_text=_(
            "Renseigné quand un Agent SGI prend en charge le dossier "
            "(transition SOUMIS → EN_INSTRUCTION). Nommer le champ "
            "`agent` (plutôt que `agent_id`) est la convention Django "
            "idiomatique : le framework crée automatiquement la colonne "
            "SQL `agent_id`, identique à celle du document de conception — "
            "`dossier.agent` donne l'objet Utilisateur, `dossier.agent_id` "
            "donne directement l'UUID sans requête supplémentaire."
        ),
    )
    statut = models.CharField(
        _("Statut"),
        max_length=20,
        choices=Statut.choices,
        default=Statut.BROUILLON,
        db_index=True,
    )
    version = models.PositiveIntegerField(
        _("Version"), default=1,
        help_text=_("Incrémentée à chaque nouvelle soumission après un rejet/correction."),
    )
    progression_pct = models.PositiveSmallIntegerField(
        _("Progression (%)"),
        default=0,
        help_text=_("Pourcentage de champs obligatoires renseignés, calculé applicativement."),
    )
    motif_rejet = models.TextField(_("Motif de rejet"), blank=True)

    convention_acceptee = models.BooleanField(
        _("Convention tarifaire acceptée"),
        default=False,
        help_text=_(
            "Engagement irréversible de l'investisseur : requis pour "
            "soumettre le dossier dès lors que la SGI a publié sa "
            "convention tarifaire (UC16)."
        ),
    )

    # --- Signature électronique ---
    type_signature = models.CharField(
        _("Type de signature"),
        max_length=25,
        choices=TypeSignature.choices,
        blank=True,
    )
    donnee_signature = models.TextField(
        _("Donnée de signature"),
        blank=True,
        help_text=_(
            "Référence opaque à la preuve de signature (ex: identifiant de "
            "transaction OTP, hash du document signé). Ne stocke jamais de "
            "données biométriques brutes."
        ),
    )
    date_signature = models.DateTimeField(_("Date de signature"), null=True, blank=True)

    # --- Preuve de signature OTP (générée et vérifiée côté serveur) ---
    otp_hash = models.CharField(
        _("Hash du code OTP"),
        max_length=128,
        blank=True,
        help_text=_(
            "Empreinte PBKDF2 du code OTP en attente de vérification. "
            "Jamais le code en clair ; purgée dès la signature posée."
        ),
    )
    otp_expiration = models.DateTimeField(
        _("Expiration du code OTP"),
        null=True, blank=True,
    )
    ip_signature = models.GenericIPAddressField(
        _("Adresse IP de signature"),
        null=True, blank=True,
        help_text=_("IP du client au moment de la signature (preuve légale)."),
    )

    # --- Horodatage du cycle de vie ---
    date_creation = models.DateTimeField(_("Date de création"), auto_now_add=True)
    date_soumission = models.DateTimeField(_("Date de soumission"), null=True, blank=True)
    date_instruction = models.DateTimeField(_("Date de prise en instruction"), null=True, blank=True)
    date_decision = models.DateTimeField(_("Date de décision"), null=True, blank=True)

    objects = DossierManager()

    class Meta:
        verbose_name = _("Dossier")
        verbose_name_plural = _("Dossiers")
        ordering = ["-date_creation"]
        indexes = [
            models.Index(fields=["sgi", "statut"]),
        ]

    def clean(self):
        """Vérifie la cohérence des transitions de statut et de la signature."""
        if self.statut != self.Statut.BROUILLON and self.etape_courante_id is not None:
            # etape_courante ne sert que pendant la saisie en brouillon ;
            # une fois soumis, la progression par étape n'a plus d'usage.
            pass  # toléré : on ne force pas sa remise à None pour l'historique

        if self.statut == self.Statut.VALIDE and not self.type_signature:
            raise ValidationError(
                _("Un dossier ne peut être validé sans signature électronique.")
            )

        if self.utilisateur_id and not self.utilisateur.est_investisseur:
            raise ValidationError(
                {"utilisateur": _("Seul un utilisateur INVESTISSEUR peut être propriétaire d'un dossier.")}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        if self.reference:
            super().save(*args, **kwargs)
            return

        # Génération de la référence + insertion protégées contre les
        # créations concurrentes : deux requêtes simultanées pourraient
        # calculer le même compteur et violer la contrainte unique. On
        # réessaie donc en cas d'IntegrityError plutôt que de remonter un
        # 500. La transaction atomique isole chaque tentative.
        from django.db import IntegrityError, transaction

        derniere_erreur = None
        for _ in range(5):
            try:
                with transaction.atomic():
                    # select_for_update() exige une transaction active :
                    # le calcul de la référence est donc fait ici, à
                    # l'intérieur du bloc atomique.
                    self.reference = self._generer_reference()
                    super().save(*args, **kwargs)
                return
            except IntegrityError as exc:
                # Collision sur la référence : on recalcule et on réessaie.
                derniere_erreur = exc
                self.reference = ""
        # Épuisement des tentatives : on relève la dernière erreur.
        raise derniere_erreur

    def _generer_reference(self):
        """Génère une référence lisible : PGNOC-<année>-<compteur zero-paddé>."""
        from django.utils import timezone

        annee = timezone.now().year
        # select_for_update() verrouille les lignes de l'année en cours le
        # temps de la transaction, réduisant la fenêtre de course ; le retry
        # sur IntegrityError couvre le cas résiduel (première insertion de
        # l'année, absence de lignes à verrouiller).
        dernier = (
            Dossier.objects.select_for_update()
            .filter(reference__startswith=f"PGNOC-{annee}-")
            .order_by("-reference")
            .first()
        )
        compteur = int(dernier.reference.split("-")[-1]) + 1 if dernier else 1
        return f"PGNOC-{annee}-{compteur:06d}"

    def __str__(self):
        return f"{self.reference} ({self.get_statut_display()})"


class ValeurChamp(models.Model):
    """Valeur saisie par un investisseur pour un ChampKYC donné, dans un Dossier.

    Structure EAV : une ligne par (Dossier, ChampKYC). Stocke soit une
    valeur textuelle ('valeur', pour tous types sauf FICHIER), soit une
    référence de fichier MinIO ('fichier', pour le type FICHIER) —
    jamais les deux à la fois.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("Identifiant"),
    )
    dossier = models.ForeignKey(
        "dossiers.Dossier",
        verbose_name=_("Dossier"),
        related_name="valeurs_champs",
        on_delete=models.CASCADE,
    )
    champ = models.ForeignKey(
        ChampKYC,
        verbose_name=_("Champ KYC"),
        related_name="valeurs",
        on_delete=models.PROTECT,
        help_text=_(
            "PROTECT plutôt que CASCADE : on ne veut jamais qu'une "
            "suppression de champ efface silencieusement des réponses "
            "déjà soumises par un investisseur (traçabilité)."
        ),
    )
    valeur = models.TextField(
        _("Valeur"),
        blank=True,
        help_text=_(
            "Valeur textuelle brute. Pour CHOIX_MULTIPLE, stockée en "
            "JSON sérialisé (ex: '[\"Salarié\", \"Retraité\"]')."
        ),
    )
    fichier = models.CharField(
        _("Référence fichier"),
        max_length=500,
        blank=True,
        help_text=_(
            "Clé/chemin de l'objet sur MinIO (pas l'URL signée, qui "
            "est temporaire et générée à la demande — Étape 2.4)."
        ),
    )
    commentaire_agent = models.TextField(
        _("Commentaire de l'agent"),
        blank=True,
        help_text=_("Renseigné par un Agent SGI lors d'une demande de correction (UC09)."),
    )
    est_corrige = models.BooleanField(
        _("Corrigé"),
        default=False,
        help_text=_(
            "Passe à True quand l'investisseur ressaisit ce champ "
            "après un commentaire_agent, pour signaler à l'agent que "
            "la correction attend une nouvelle relecture."
        ),
    )
    date_creation = models.DateTimeField(_("Date de création"), auto_now_add=True)
    date_maj = models.DateTimeField(_("Dernière modification"), auto_now=True)

    class Meta:
        verbose_name = _("Valeur de champ")
        verbose_name_plural = _("Valeurs de champs")
        ordering = ["dossier", "champ__ordre"]
        constraints = [
            models.UniqueConstraint(
                fields=["dossier", "champ"],
                name="unique_valeur_par_dossier_et_champ",
            ),
        ]

    def clean(self):
        """Valide la cohérence type de champ / contenu de la valeur."""
        if self.champ.type == ChampKYC.TypeChamp.FICHIER:
            if not self.fichier:
                raise ValidationError({"fichier": _("Une référence de fichier est requise pour ce champ.")})
            if self.valeur:
                raise ValidationError({"valeur": _("Le champ `valeur` est inutilisé pour un champ FICHIER.")})
        else:
            if self.fichier:
                raise ValidationError({"fichier": _("`fichier` n'est utilisable que pour un champ de type FICHIER.")})
            if self.champ.obligatoire and not self.valeur:
                raise ValidationError({"valeur": _("Ce champ est obligatoire.")})
            if self.valeur:
                self._valider_typage_valeur()

        if self.champ.champ_parent_id:
            # Le champ conditionnel ne doit être renseigné que si la
            # valeur du champ parent, pour ce même dossier, correspond
            # bien à la valeur déclencheur attendue.
            try:
                valeur_parent = ValeurChamp.objects.get(
                    dossier_id=self.dossier_id,
                    champ_id=self.champ.champ_parent_id,
                ).valeur
            except ValeurChamp.DoesNotExist:
                valeur_parent = None

            if valeur_parent != self.champ.valeur_declencheur and (self.valeur or self.fichier):
                raise ValidationError(
                    _(
                        "Ce champ ne doit être renseigné que si '%(parent)s' vaut '%(attendu)s'."
                    ) % {
                        "parent": self.champ.champ_parent.nom,
                        "attendu": self.champ.valeur_declencheur,
                    }
                )

    def _valider_typage_valeur(self):
        """Validation typée de la valeur selon `ChampKYC.type` (données fiables).

        Règles :
          - NOMBRE : numérique (entier ou décimal, virgule française acceptée) ;
          - DATE : au format ISO AAAA-MM-JJ ;
          - BOOLEEN : oui/non, true/false, 1/0 ;
          - CHOIX_UNIQUE : la valeur doit appartenir aux options ;
          - CHOIX_MULTIPLE : JSON liste, chaque élément dans les options.
        """
        from datetime import date as date_type
        import json as module_json

        valeur = self.valeur.strip()
        type_champ = self.champ.type
        erreurs = {}

        if type_champ == ChampKYC.TypeChamp.NOMBRE:
            try:
                float(valeur.replace(",", "."))
            except ValueError:
                erreurs["valeur"] = _("Ce champ attend un nombre.")
        elif type_champ == ChampKYC.TypeChamp.DATE:
            try:
                date_type.fromisoformat(valeur)
            except ValueError:
                erreurs["valeur"] = _("Ce champ attend une date au format AAAA-MM-JJ.")
        elif type_champ == ChampKYC.TypeChamp.BOOLEEN:
            if valeur.lower() not in ("oui", "non", "true", "false", "1", "0"):
                erreurs["valeur"] = _("Ce champ attend « oui » ou « non ».")
        elif type_champ in (ChampKYC.TypeChamp.CHOIX_UNIQUE, ChampKYC.TypeChamp.CHOIX_MULTIPLE):
            options = self.champ.options_choix or []
            if type_champ == ChampKYC.TypeChamp.CHOIX_UNIQUE:
                valeurs = [valeur]
            else:
                try:
                    valeurs = module_json.loads(valeur)
                    if not isinstance(valeurs, list):
                        raise ValueError
                except ValueError:
                    erreurs["valeur"] = _("Ce champ attend une liste de choix.")
                    valeurs = []
            hors_options = [v for v in valeurs if v not in options]
            if hors_options:
                erreurs["valeur"] = _(
                    "La valeur « %(valeur)s » n'est pas dans les options proposées."
                ) % {"valeur": ", ".join(hors_options)}

        if erreurs:
            raise ValidationError(erreurs)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        contenu = self.fichier or self.valeur
        return f"{self.champ.nom} = {contenu} ({self.dossier.reference})"