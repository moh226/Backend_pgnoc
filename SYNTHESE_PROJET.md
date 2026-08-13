# PGNOC-TI — Backend : synthèse du projet

> Document de travail pour la soutenance / présentation au maître de stage.
> État au **12 août 2026** — suite de tests : **178/178 verts**.

---

## 1. Contexte et objectif

**PGNOC-TI** (Plan de Gestion Numérique des Opérations de Compte-Titres
Individuel) est une plateforme d'ouverture **dématérialisée de comptes-titres**
dans l'espace UEMOA. L'investisseur constitue en ligne son dossier de
conformité (KYC / LCB-FT), une société de gestion (SGI) l'instruit, le
valide ou le rejette, le tout dans un cadre réglementaire traçable
(contrôles CREPMF).

Ce dépôt contient le **backend** (API REST) consommé par le frontend Vue.js.
Le développement suit un plan par phases couvrant tout le cycle de vie du
dossier, de l'inscription à la signature électronique.

---

## 2. Stack technique

| Brique | Choix |
|---|---|
| Langage / Framework | Python 3.12 · Django 5 · Django REST Framework |
| Authentification | SimpleJWT (accès 60 min / refresh 24 h) + OAuth Google (investisseurs) |
| Base de données | PostgreSQL 13+ (dont triggers de verrouillage) |
| Stockage documents | MinIO (S3-compatible), URLs signées |
| Files d'attente | Redis / Celery (prévu, optionnel au démarrage) |
| API | REST paginée, schéma OpenAPI (`/api/schema/`), doc Swagger/ReDoc |

---

## 3. Architecture applicative

| App | Rôle | Espace |
|---|---|---|
| `comptes` | Utilisateurs (4 rôles), JWT, OAuth, CRUD agents, **espace Admin Général (UC20-22)** | `/api/comptes/`, `/api/admin-general/` |
| `sgi` | Sociétés de gestion, convention tarifaire, présentation, fiche SGI | `/api/sgi/` |
| `dossiers` | Dossier, **workflow signé**, KYC dynamique, paramétrage admin SGI | `/api/dossiers/` |
| `audit` | **Journal d'audit immuable** (append-only) + export CSV | `/api/audit/journal/` |
| `notifications` | Notifications du workflow par investisseur | `/api/notifications/` |
| `administration` | Tableau de bord global, SGI partenaires, comptes internes | `/api/admin-general/` |

**RBAC strict** : Investisseur · Agent SGI · Admin SGI · Admin Général —
chaque espace est isolé par des classes de permission dédiées, et les
données sont **cloisonnées par SGI** (multi-tenant).

---

## 4. Ce qui a été fait

### Phase 1 — Fondations ✔
- Modèle complet (14 classes de la conception) : utilisateurs et profils,
  SGI, dossiers, étapes/champs KYC, valeurs, journal d'audit, notifications.
- Inscription investisseur (mot de passe fort validé) et connexion JWT.
- Journal d'audit dès la première action : qui, quoi, quand, **avant/après**
  en JSONB, IP + User-Agent.

### Phase 2 — Authentification & workflow ✔
- **OAuth Google** pour les investisseurs (vérification `email_verified`,
  refus des rôles non-investisseurs).
- **Workflow d'état signé** : BROUILLON → SOUMIS → EN_INSTRUCTION →
  VALIDE / REJETE → (re)SOUMIS, avec **9 transitions contrôlées**, chaque
  pas étant journalisé et notifiant l'intéressé.
- Cycle de corrections : l'agent commente les champs, l'investisseur
  corrige, re-soumet ; la **progression est recalculée honnêtement**.

### Phase 3 — Back-office ✔
- **3A/3B** : checklist de conformité KYC, saisie des valeurs, progression.
- **3C** : notifications (liste, marquage lu/non-lu).
- **3D** : paramétrage KYC par l'Admin SGI (CRUD étapes/champs : typage,
  ordre, fichiers, conditionnement, cloisonnement strict).
- **3E** : convention tarifaire PDF (vérifiée PDF) + présentation ; la
  **soumission exige l'acceptation de la convention** (preuve auditée).
- **3F** : **Espace Admin Général** — gestion des SGI partenaires
  (catalogue, suspension), gestion des comptes internes (création, rôles,
  activation, avec garde-fous : un admin ne peut pas se désactiver, le
  dernier Admin Général actif est protégé), tableau de bord global
  (dossiers par statut, SGI sans convention publiée, activité récente).

### Sécurité & fiabilité — revue de code et durcissement ✔
**Revue de ~6 600 lignes** : 5 failles critiques identifiées **et corrigées** :
1. **Contrôle sans preuve** → réponses obligatoires + décision liée à l'agent.
2. **Transitions non atomiques** → verrou pessimiste `SELECT FOR UPDATE`,
   audit dans la même transaction.
3. **Journal muet** → toute erreur d'écriture est désormais journalisée.
4. **Journal modifiable** → verrou **PostgreSQL append-only** (BEFORE
   UPDATE/DELETE interdit) — vérifié au niveau base.
5. **OAuth à risque** → `email_verified` exigé + blocage des rôles
   non-investisseurs (anti prise de contrôle de comptes).

**Durcissement complémentaire** :
- Échecs de connexion journalisés (brute-force visible dans le journal).
- **Validation typée** des réponses KYC (nombre, date ISO, booléen,
  choix limités) ; un champ « non »/vide ne « remplit » plus le dossier.
- **Uploads durcis** : vérification des **magic bytes** (un exécutable
  déguisé en PDF est rejeté), taille max obligatoire, purge des motifs de
  rejet à la re-soumission, champs désactivés exclus, plus de fuite de clé
  de stockage.
- **Signature électronique OTP** : code à usage unique généré côté serveur,
  preuve cryptographique horodatée (PBKDF2 + SHA-256) liant dossier,
  référence, agent et IP — à la place du champ texte libre initial.
- **Contraintes au niveau base** : cohérence rôle/SGI par **trigger
  PostgreSQL** (écriture directe en violation refusée), en plus de la
  validation applicative.

### Qualité ✔
- **178 tests automatisés** (unitaires + API + base) : suite complète verte.
- **Schéma OpenAPI sans erreur ni warning** (drf-spectacular) : les 39
  endpoints sont tous documentés (`/api/schema/`, `/api/docs/`).
- Hygiène git : `.env.example`, README détaillé, `.idea/` désindexé.

### Industrialisation ✔
- **Conteneurisation** : `Dockerfile` (Gunicorn 4 workers, timeout 60) et
  `docker-compose.yml` (PostgreSQL 16 + MinIO + web, healthchecks,
  `migrate` + `collectstatic` au démarrage).
- **Serving statique** : WhiteNoise (`CompressedManifestStaticFilesStorage`)
  + `STATIC_ROOT`, prêt derrière un reverse-proxy.
- **CI** : workflow GitHub Actions (check Django, suite de tests, génération
  du schéma) sur PostgreSQL.
- **Son de cloche production** : endpoint public `/healthz/` (200/503 selon
  l'état de la base), config LOGGING complète (fichiers `application.log` /
  `audit.log` à rotation, niveau piloté par variables d'environnement).

---

## 5. Ce qui reste à faire

| Priorité | Sujet | Détail |
|---|---|---|
| Faite | **Déploiement production** | Contexte Docker + CI (PostgreSQL, MinIO, Gunicorn, WhiteNoise, `/healthz/`) — reste : HTTPS/HSTS et secrets hors code côté hébergeur |
| Moyenne | **Export PDF du journal** | Spécifié CSV **et PDF** (§8.3) — CSV fait, PDF restant |
| Faible | **Mot de passe oublié** | Réinitialisation par email (non implémentée) |
| Faible | **Cache Redis** des formulaires KYC | Spécifié §8.2 — non implémenté (perf) |
| Évolutions | **Chapitre 9 de la conception** | Messagerie intégrée, OCR/IA des pièces, signature biométrique, BI, API publique |
| Front | **Consommation par le frontend Vue.js** | Brancher les routes ci-dessus |

---

## 6. Résumé de présentation (oral ~2 min)

> **PGNOC-TI** est une plateforme d'ouverture de compte-titres 100 %
> dématérialisée : l'investisseur constitue son dossier KYC en ligne,
> une société de gestion l'instruit, et chaque décision est tracée dans
> un journal d'audit que rien ne peut altérer.
>
> J'ai développé le backend complet en **Django** : inscription et
> connexion (JWT + OAuth Google), workflow signé du dossier avec cycle
> de corrections, paramétrage KYC par les SGI, conventions tarifaires,
> notifications, et un espace Admin Général de supervision.
>
> Trois points forts :
> 1. **Traçabilité conforme CREPMF** — journal d'audit immuable au niveau
>    base (append-only), avec état avant/après, IP et horodatage.
> 2. **Sécurité défendue en profondeur** — signature électronique OTP
>    serveur, vérification des vrais types de fichiers, verrouillage
>    concurrentiel des transitions, contraintes portées par PostgreSQL.
> 3. **Fiabilité prouvée** — **178 tests automatisés** couvrant API,
>    workflow et base de données ; une revue de ~6 600 lignes a permis
>    de corriger 5 failles critiques avant mise en service.

---

## 7. Chiffres clés (à afficher en slide)

| Indicateur | Valeur |
|---|---|
| Tests automatisés | **178 / 178 verts** |
| Apps Django | 6 métier + projet |
| Endpoints d'API | **39** (schéma OpenAPI 0 erreur / 0 warning) |
| Failles critiques corrigées en revue | 5 |
| Rôles RBAC | 4 (Investisseur, Agent, Admin SGI, Admin Général) |
| Verrous PostgreSQL | 2 (audit append-only, cohérence rôle/SGI) |
| Étapes du cycle de vie dossier | 5 statuts, 9 transitions |
| Industrialisation | Docker + Compose (PostgreSQL, MinIO, Gunicorn, WhiteNoise), CI GitHub Actions, `/healthz/` |
