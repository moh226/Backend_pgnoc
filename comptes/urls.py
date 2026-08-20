from django.urls import path

from rest_framework_simplejwt.views import TokenRefreshView

from comptes.oauth import ConnexionGoogleAPIView, ConnexionGoogleCallbackView
from comptes.views import InscriptionInvestisseurAPIView, ConnexionAPIView
from comptes.views_agents import AgentListCreateAPIView, AgentRetrieveUpdateAPIView
from comptes.views_profil import ChangerMotDePasseAPIView, ProfilMoiAPIView

app_name = "comptes"

urlpatterns = [
    path("register/", InscriptionInvestisseurAPIView.as_view(), name="register"),
    path("login/", ConnexionAPIView.as_view(), name="login"),
    path("login/refresh/", TokenRefreshView.as_view(), name="login-refresh"),
    path("moi/profil/", ProfilMoiAPIView.as_view(), name="moi-profil"),
    path("moi/mot-de-passe/", ChangerMotDePasseAPIView.as_view(), name="moi-mot-de-passe"),
    path(
        "agents/",
        AgentListCreateAPIView.as_view(),
        name="agents-list-create",
    ),
    path(
        "agents/<uuid:pk>/",
        AgentRetrieveUpdateAPIView.as_view(),
        name="agents-detail",
    ),
    path(
        "oauth/google/login/",
        ConnexionGoogleAPIView.as_view(),
        name="google-login",
    ),
    path(
        "oauth/google/callback/",
        ConnexionGoogleCallbackView.as_view(),
        name="google-callback",
    ),
]