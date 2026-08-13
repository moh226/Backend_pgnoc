# PGNOC-TI — Backend

Backend Django du **Plan de Gestion Numérique des Opérations de Compte-Titres
Individuel (PGNOC-TI)** : ouverture dématérialisée d'un compte-titres UEMOA.
Appelée par le frontend (Vue.js) via une API REST (DRF) sécurisée par JWT.

## Stack

- Python 3.x, Django 5, Django REST Framework, SimpleJWT
- PostgreSQL, Redis/Celery (optionnel), MinIO (stockage S3 des documents)
- Inscription et connexion par mot de passe **et** OAuth Google (investisseurs)

## Architecture

| App            | Rôle                                                              |
|----------------|-------------------------------------------------------------------|
| `comptes`      | Investisseurs, agents SGI, administrateurs ; JWT + OAuth Google   |
| `sgi`          | Sociétés de gestion ; convention tarifaire, présentation, fiche   |
| `dossiers`     | Cœur : dossier, workflow signé (soumission/validation/rejet…),    |
|                | checklist de conformité, demandes de correction, signature OTP    |
| `audit`        | Journal d'audit **append-only** (immutable, protégé par trigger)  |
| `notifications`| Notifications des événements du workflow par investisseur         |
| `pgnoc`        | Configuration projet (settings, URLs racine)                      |

## Prérequis

- Python 3.11+ et `venv`
- PostgreSQL 13+ (utilisateur avec droit `CREATEDB` pour les tests)
- MinIO (ou back-end S3 compatible) pour les documents signés

## Installation

```bash
# 1. Environnement virtuel
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt

# 2. Configuration
cp .env.example .env          # puis renseigner les secrets
createdb -O pgnoc_user pgnoc_ti

# 3. Base de données
./env/bin/python manage.py migrate

# 4. Démarrage (développement)
./env/bin/python manage.py runserver
```

### Démarrage conteneurisé (PostgreSQL + MinIO + API)

```bash
cp .env.example .env
docker compose up --build
# API sur http://localhost:8000 — migrations et collectstatic automatiques.
# Sonde de santé : http://localhost:8000/healthz/
```

## Tests

La suite complète (172 tests) tourne sur une base PostgreSQL temporaire isolée :

```bash
./env/bin/python manage.py test --noinput
```

## Production

- Serveur : **Gunicorn** (4 workers, timeout 60 s) servi par le `Dockerfile`.
- Statiques : **WhiteNoise** (`collectstatic` à la construction).
- Journaux : console + fichiers rotatifs `logs/application.log` et
  `logs/audit.log` (échecs de traçabilité audit isolés) ; variables
  `DJANGO_LOG_*` (voir `.env.example`).
- CI : `.github/workflows/ci.yml` — system check + suite de tests + schéma
  OpenAPI sur PostgreSQL.

## Points d'entrée principaux

| Méthode | Route                                        | Rôle                       |
|---------|----------------------------------------------|----------------------------|
| GET     | `/healthz/`                                  | Sonde de santé (public)    |
| POST    | `/api/comptes/register/`                     | Inscription investisseur   |
| POST    | `/api/comptes/login/`                        | Connexion (JWT)            |
| GET     | `/api/comptes/oauth/google/authorize/`       | Début OAuth Google         |
| POST    | `/api/dossiers/`                             | Ouverture d'un dossier     |
| POST    | `/api/dossiers/<uuid>/soumettre/`            | Soumission (convention OK) |
| POST    | `/api/dossiers/<uuid>/signer/`               | Signature électronique OTP |
| POST    | `/api/dossiers/<uuid>/valeurs/fichier/`      | Pièces justificatives      |
| GET     | `/api/etapes-kyc/`                           | Parcours de conformité     |
| GET/PUT | `/api/admin/etapes-kyc/` …                   | Paramétrage KYC (admin)    |
| GET/PUT | `/api/convention/`, `/api/presentation/`     | Éditions SGI (admin)       |
| GET     | `/api/notifications/`                        | Notifications du workflow  |
| GET     | `/api/journal/`                              | Journal d'audit (admin)    |
| GET     | `/api/admin-general/dashboard/`              | Tableau de bord (admin gén.)|
| GET     | `/api/sgi/<uuid>/`                           | Fiche de la SGI            |

Schéma OpenAPI : `./env/bin/python manage.py spectacular --file schema.yml`.