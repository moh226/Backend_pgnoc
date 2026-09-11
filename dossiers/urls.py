"""Routes URL de l'app dossiers."""

from django.urls import path

from dossiers.views import (
    DossierAccepterConventionAPIView, DossierDetailAPIView,
    DossierGenererOtpAPIView, DossierListCreateAPIView,
    DossierSignerAPIView, DossierSoumettreAPIView,
    EtapeKYCListAPIView, InvestisseurDashboardAPIView,
    ValeurChampListCreateAPIView,
    ValeurChampFichierUploadAPIView, ValeurChampFichierUrlAPIView,
    ValeurSelfieAuthenticiteAPIView,
)
from dossiers.views_agent import (
    DossierActiverAPIView, DossierPrendreEnChargeAPIView, DossierValiderAPIView,
    DossierRejeterAPIView, ValeurChampCommenterAPIView, DossierTransfererAPIView,
)
from dossiers.views_depot import (
    DepotDetailAgentAPIView, DepotListeAgentAPIView,
    DepotMinimumInvestisseurAPIView, DepotVerifierAgentAPIView,
)
from dossiers.views_kyc_admin import (
    ChampKYCListCreateAPIView, ChampKYCRetrieveUpdateDestroyAPIView,
    EtapeKYCListCreateAPIView, EtapeKYCRetrieveUpdateDestroyAPIView,
)

app_name = "dossiers"

urlpatterns = [
    path("investisseur/dashboard/",
         InvestisseurDashboardAPIView.as_view(),
         name="investisseur-dashboard",
         ),

    path("etapes-kyc/",
         EtapeKYCListAPIView.as_view(),
         name="etapes-kyc"
         ),

    path("dossiers/",
         DossierListCreateAPIView.as_view(),
         name="dossier-list-create"
         ),

    path("dossiers/<uuid:pk>/",
         DossierDetailAPIView.as_view(),
         name="dossier-detail"
         ),

    path("dossiers/<uuid:dossier_pk>/soumettre/",
         DossierSoumettreAPIView.as_view(),
         name="dossier-soumettre"
         ),

    path("dossiers/<uuid:dossier_pk>/accepter-convention/",
         DossierAccepterConventionAPIView.as_view(),
         name="dossier-accepter-convention",
         ),

    path("dossiers/<uuid:dossier_pk>/generer-otp/",
         DossierGenererOtpAPIView.as_view(),
         name="dossier-generer-otp",
         ),

    path("dossiers/<uuid:dossier_pk>/signer/",
         DossierSignerAPIView.as_view(),
         name="dossier-signer",
         ),

    path("dossiers/<uuid:dossier_pk>/prendre-en-charge/",
         DossierPrendreEnChargeAPIView.as_view(),
         name="dossier-prendre-en-charge",
         ),

    path("dossiers/<uuid:dossier_pk>/commenter/",
         ValeurChampCommenterAPIView.as_view(),
         name="dossier-commenter",
         ),

    path("dossiers/<uuid:dossier_pk>/valider/",
         DossierValiderAPIView.as_view(),
         name="dossier-valider",
         ),

    path("dossiers/<uuid:dossier_pk>/rejeter/",
         DossierRejeterAPIView.as_view(),
         name="dossier-rejeter",
         ),

    path("dossiers/<uuid:dossier_pk>/transferer/",
         DossierTransfererAPIView.as_view(),
         name="dossier-transferer",
         ),

    path("dossiers/<uuid:dossier_pk>/activer/",
         DossierActiverAPIView.as_view(),
         name="dossier-activer",
         ),

    path("dossiers/<uuid:dossier_pk>/depot-minimum/",
         DepotMinimumInvestisseurAPIView.as_view(),
         name="dossier-depot-minimum",
         ),

    path("depots/",
         DepotListeAgentAPIView.as_view(),
         name="depot-liste",
         ),

    path("depots/<uuid:pk>/",
         DepotDetailAgentAPIView.as_view(),
         name="depot-detail",
         ),

    path("depots/<uuid:pk>/verifier/",
         DepotVerifierAgentAPIView.as_view(),
         name="depot-verifier",
         ),

    path(
        "dossiers/<uuid:dossier_pk>/valeurs/",
        ValeurChampListCreateAPIView.as_view(),
        name="dossier-valeurs",
    ),

    path(
        "dossiers/<uuid:dossier_pk>/valeurs/fichier/",
        ValeurChampFichierUploadAPIView.as_view(),
        name="dossier-valeur-fichier-upload",
    ),

    path(
        "dossiers/<uuid:dossier_pk>/valeurs/<uuid:valeur_pk>/url/",
        ValeurChampFichierUrlAPIView.as_view(),
        name="dossier-valeur-fichier-url",
    ),

    path(
        "dossiers/<uuid:dossier_pk>/valeurs/<uuid:valeur_pk>/authenticite/",
        ValeurSelfieAuthenticiteAPIView.as_view(),
        name="dossier-valeur-selfie-authenticite",
    ),

    path("admin/etapes-kyc/",
         EtapeKYCListCreateAPIView.as_view(),
         name="admin-etapes-kyc",
         ),

    path("admin/etapes-kyc/<uuid:pk>/",
         EtapeKYCRetrieveUpdateDestroyAPIView.as_view(),
         name="admin-etape-kyc",
         ),

    path("admin/champs-kyc/",
         ChampKYCListCreateAPIView.as_view(),
         name="admin-champs-kyc",
         ),

    path("admin/champs-kyc/<uuid:pk>/",
         ChampKYCRetrieveUpdateDestroyAPIView.as_view(),
         name="admin-champ-kyc",
         ),
]
