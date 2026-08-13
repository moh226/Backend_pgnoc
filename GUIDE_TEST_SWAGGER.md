# Guide de test de l'API PGNOC-TI sur Swagger

> L'API expose sa **documentation interactive** à `http://127.0.0.1:8000/api/docs/`
> (interface Swagger UI) — chaque endpoint y est documenté, testable et
> nécessite d'abord de s'authentifier via le bouton **Authorize**.

---

## 0. Prise en main (5 min)

### 0.1 Préparer la base (une seule fois)

```bash
cd backend
./env/bin/python manage.py migrate
./env/bin/python manage.py seed_demo
```

`seed_demo` crée un jeu de données complet et **idempotent** (relançable
sans risque) :

| Identifiant | Rôle | Mot de passe |
|---|---|---|
| `investisseur@demo.pgnoc` | Investisseur | `MotDePasse-Demo-2026!` |
| `agent-sgi@demo.pgnoc` | Agent SGI | `MotDePasse-Demo-2026!` |
| `admin-sgi@demo.pgnoc` | Admin SGI | `MotDePasse-Demo-2026!` |
| `admin-general@demo.pgnoc` | Admin Général | `MotDePasse-Demo-2026!` |

Il crée aussi la **SGI « SGI Démo UEMOA »** (code `DEMO`) avec sa
**convention tarifaire PDF** déjà publiée.

### 0.2 Lancer le serveur

```bash
./env/bin/python manage.py runserver
```

Puis ouvrir : <http://127.0.0.1:8000/api/docs/>

> **Stockage des fichiers** : par défaut (sans MinIO), les justificatifs
> et PDF sont stockés dans `media/` et servis par Django (`/media/...`) —
> aucun conteneur requis. Pour le mode S3 réel, renseigner
> `MINIO_ENDPOINT_URL` (`.env`) et lancer MinIO.

---

## 1. Comprendre l'interface Swagger

- Chaque **bloc coloré** = une action HTTP : `GET` (vert), `POST`(orange),
  `PUT` (bleu), `PATCH` (violet), `DELETE` (rouge).
- **Cliquer sur un endpoint** → bouton **Try it out** → renseigner les
  paramètres / le body → **Execute**.
- Les **paramètres en gris** (`sgi`, `dossier_pk`, `pk`…) sont des
  identifiants **UUID** à copier depuis les réponses précédentes.
- Un body `{ "champ": "...", "valeur": "..." }` est du **JSON** saisi en
  dur ; un body `multipart/form-data` est un **formulaire de fichier**
  (bouton de sélection de fichier).

### 1.1 S'authentifier (étape obligatoire)

1. `POST /api/comptes/login/` → **Try it out** → body :
   `{ "email": "investisseur@demo.pgnoc", "password": "MotDePasse-Demo-2026!" }`
   → **Execute**.
2. Copier la valeur de `"access"` dans la réponse.
3. Cliquer sur **Authorize** (en haut à droite) → coller dans le champ :
   `Bearer <token-copié>` (le préfixe `Bearer ` est obligatoire) → **Authorize**.
4. Tous les appels authentifiés renvoient désormais 200 — sans ce
   header : `401 {"detail": "Les identifiants d'authentification n'ont pas été fournis."}`.

> **Validité du token** : 15 min. Quand il expire (401), soit re-login,
> soit `POST /api/comptes/login/refresh/` avec `{ "refresh": "..." }`
> pour obtenir un nouveau `access`.

---

## 2. Parcours INVESTISSEUR — de l'inscription à la signature

C'est le parcours de bout en bout à tester en premier. Suivre l'ordre ;
une étape dépend des réponses des précédentes.

| # | Action | Endpoint | Body / Paramètres | Résultat attendu |
|---|---|---|---|---|
| 1 | **Créer un compte** | `POST /api/comptes/register/` | `{ "email": "moi@exemple.com", "prenom": "Aïcha", "nom": "Diallo", "password": "Secret-PnG-2026!", "password_confirmation": "Secret-PnG-2026!" }` | `201` + `"id"` (UUID) |
| 2 | **Se connecter** | `POST /api/comptes/login/` | `{ "email": …, "password": … }` | `200` + `access`, `refresh`, `utilisateur` |
| 3 | **Authorize** dans Swagger | — | `Bearer <access>` | — |
| 4 | **Lister les SGI** | `GET /api/sgi/` | — | Liste JSON, noter le `"id"` de la SGI Démo |
| 5 | **Fiche de la SGI** | `GET /api/sgi/{id}/` | id de la SGI | `presentation`, `convention` (dont `signe_requis`) |
| 6 | **Parcours KYC** | `GET /api/dossiers/etapes-kyc/?sgi={id}` | paramètre `sgi` obligatoire | Étapes + champs à remplir, noter `"etape_courante"` |
| 7 | **Créer le dossier** | `POST /api/dossiers/dossiers/` | `{ "sgi": "<id-SGI>", "etape_courante": "<id-étape-1>" }` | `201`, noter le `"id"` du dossier |
| 8 | **Accepter la convention** | `POST /api/dossiers/dossiers/{dossier_id}/accepter-convention/` | — | `200 {"detail": "Convention tarifaire acceptée.", …}` — `409` si pas de PDF publié |
| 9 | **Renseigner un champ texte** | `POST /api/dossiers/dossiers/{dossier_id}/valeurs/` | `{ "champ": "<id-du-champ>", "valeur": "Valeur saisie" }` | `201` ; vérifier la **progression** dans le détail du dossier |
| 10 | **Téléverser un justificatif** | `POST /api/dossiers/dossiers/{dossier_id}/valeurs/fichier/` *(multipart)* | `champ` = id du champ FICHIER, `fichier` = un PDF | `201` ; un non-PDF est **rejeté `400`** (magic bytes) |
| 11 | **URL signée** | `GET /api/dossiers/dossiers/{dossier_id}/valeurs/{valeur_id}/url/` | — | `{ "url_signee": "/media/…" }`, à coller dans le navigateur |
| 12 | **Soumettre** | `POST /api/dossiers/dossiers/{dossier_id}/soumettre/` | — | `200`, statut → `SOUMIS` |
| 13 | **Générer l'OTP** | `POST /api/dossiers/dossiers/{dossier_id}/generer-otp/` | — | `{ "code": "123456", "expiration": … }` |
| 14 | **Signer** | `POST /api/dossiers/dossiers/{dossier_id}/signer/` | `{ "otp_code": "123456" }` | `200`, preuve horodatée ; un mauvais code → `400` |
| 15 | **Détail du dossier** | `GET /api/dossiers/dossiers/{dossier_id}/` | — | l'état complet : statut `EN_INSTRUCTION`, progression, signature |
| 16 | **Notifications** | `GET /api/notifications/` puis `GET /api/notifications/non-lues/` | optionnel `?non_lues=true` | notifications du workflow |
| 17 | **Marquer lue** | `POST /api/notifications/{notification_id}/marquer-lue/` | — | `200` |

> **Piège classique** : un `POST` de valeur sur un champ déjà rempli est
> refusé (**`409`**) — il faut passer le champ en relecture via l'agent
> (étape 2), puis l'investisseur peut corriger.
> Le dossier ne se signe qu'après soumission ET prise en charge : la
> valeur de transition dépend de l'état courant.

---

## 3. Parcours AGENT SGI — l'instruction

Reprendre en tant que `agent-sgi@demo.pgnoc` (nouveau login → Authorize).

| # | Action | Endpoint | Body / Paramètres | Résultat attendu |
|---|---|---|---|---|
| 1 | **Lister les dossiers** | `GET /api/dossiers/dossiers/` | — | liste (dossiers de sa SGI) |
| 2 | **Prendre en charge** | `POST /api/dossiers/dossiers/{id}/prendre-en-charge/` | — | statut `EN_INSTRUCTION` (un dossier non SOUMIS → `409`) |
| 3 | **Relire les valeurs** | `GET /api/dossiers/dossiers/{id}/valeurs/` | — | les saisies + justificatifs |
| 4 | **URL d'un justificatif** | `GET /api/dossiers/dossiers/{id}/valeurs/{valeur_id}/url/` | — | URL à ouvrir |
| 5 | **Demander une correction** | `POST /api/dossiers/dossiers/{id}/commenter/` | `{ "valeur": "<valeur_id>", "commentaire": "Pièce illisible" }` | `200`, le champ repasse à corriger |
| 6 | **Valider / Rejeter** | `POST /api/dossiers/dossiers/{id}/valider/` ou `/rejeter/` | — | `200`, statut `VALIDE` / `REJETE` |

> **À noter** : le cycle de correction — l'agent commente (5), l'investisseur
> corrige et **re-soumet**, l'agent revalide. La décision est liée à
> l'agent connecté (traçable dans le journal).

---

## 4. Parcours ADMIN SGI — le paramétrage

Reprendre en tant que `admin-sgi@demo.pgnoc`.

### 4.1 Paramétrer le parcours KYC (UC15)

| # | Action | Endpoint | Body | Résultat attendu |
|---|---|---|---|---|
| 1 | **Créer une étape** | `POST /api/dossiers/admin/etapes-kyc/` | `{ "nom": "Identité", "ordre": 1 }` | `201`, noter son `"id"` |
| 2 | **Créer un champ** | `POST /api/dossiers/admin/champs-kyc/` | `{ "etape": "<id-étape>", "code": "nom_complet", "nom": "Nom complet", "type": "TEXTE_COURT", "obligatoire": true, "ordre": 1 }` | `201` |

`type` accepte : `TEXTE_COURT`, `TEXTE_LONG`, `NOMBRE`, `DATE`,
`CHOIX_UNIQUE`, `CHOIX_MULTIPLE`, `FICHIER`.

- **CHOIX_*** : ajouter `"options_choix": ["Option A", "Option B"]`.
- **FICHIER** : exiger `"taille_max_mo": 5` (refus sans, `400`), et
  optionnellement `"formats_acceptes"`.
- **Conditionnel** (champ affiché seulement si…) : renseigner
  `"champ_parent"` + `"valeur_declencheur"`.
- **Modifier** : `PATCH /api/dossiers/admin/etapes-kyc/{id}/` et
  `PATCH /api/dossiers/admin/champs-kyc/{id}/` — **DELETE** pour retirer.
- Le cloisonnement : un admin SGI ne voit ni ne modifie les données
  d'une autre SGI (`403`).

### 4.2 Publier la convention et la présentation (UC16)

| Action | Endpoint | Body | Résultat |
|---|---|---|---|
| Lire | `GET /api/sgi/admin/convention/` | — | état actuel |
| **Publier / mettre à jour** | `PUT /api/sgi/admin/convention/` *(multipart)* | `titre` + `fichier_pdf` (un vrai PDF) | `200` + `url_signee` ; fichier non-PDF → `400` |
| Lire / écrire la présentation | `GET` / `PUT /api/sgi/admin/presentation/` | `{ "contenu": "Bienvenue chez…" }` | `200` |

> La **soumission d'un dossier est bloquée** (`409`) tant que la SGI n'a
> pas publié sa convention.

### 4.3 Gérer ses agents (UC18)

| Action | Endpoint | Body | Résultat |
|---|---|---|---|
| Lister | `GET /api/comptes/agents/` | — | les agents de **sa** SGI |
| Créer | `POST /api/comptes/agents/` | `{ "email": "agent2@demo.pgnoc", "prenom": "…", "nom": "…", "mot_de_passe": "…" }` | `201` — le mot de passe (ou un mot de passe généré) est renvoyé **une seule fois** (`mot_de_passe_initial`) |
| Modifier | `PATCH /api/comptes/agents/{id}/` | `{ "is_active": false }` | plus d'accès (`403`) |

---

## 5. Parcours ADMIN GÉNÉRAL — la supervision

Reprendre en tant que `admin-general@demo.pgnoc`.

| # | Action | Endpoint | Body / Paramètres | Résultat attendu |
|---|---|---|---|---|
| 1 | **Tableau de bord** | `GET /api/admin-general/dashboard/` | — | volumes par statut, SGI sans convention, activité récente |
| 2 | **Créer une SGI** | `POST /api/admin-general/sgi/` | `{ "nom": "SGI Test", "code_sgi": "TST01", "est_active": true }` | `201`, noter l'`"id"` |
| 3 | **Suspension** | `PATCH /api/admin-general/sgi/{id}/` | `{ "est_active": false }` | les nouveaux dossiers sont refusés (`409`) |
| 4 | **Créer un compte interne** | `POST /api/admin-general/utilisateurs/` | `{ "email": "resp@test.com", "role": "ADMIN_SGI", "nom": "…", "mot_de_passe": "…", "sgi": "<id-SGI>" }` | `201` — rôles possibles : `AGENT_SGI`, `ADMIN_SGI`, `ADMIN_GENERAL` |
| 5 | **Journal d'audit** | `GET /api/audit/journal/` | filtres : `action`, `email`, `entite_concernee`, `date_debut`, `date_fin` | traces immuables, avant/après |
| 6 | **Export CSV** | `GET /api/audit/journal/export/` | mêmes filtres | un fichier CSV téléchargé |

> **Garde-fous à vérifier** : l'Admin Général ne peut pas se désactiver
> lui-même ni changer son propre rôle (`400`) ; le **dernier admin
> général actif** est protégé ; l'espace n'accepte pas la création de
> comptes INVESTISSEUR (inscription publique uniquement).

---

## 6. Scénarios « échec » à tester (la sécurité)

| Test | Appel | Résultat attendu |
|---|---|---|
| Sans jeton | n'importe quel endpoint | `401` |
| Investisseur sur espace SGI | `GET /api/dossiers/admin/etapes-kyc/` en investisseur | `403` |
| Mauvais mot de passe | `POST /api/comptes/login/` | `401` + **trace dans le journal** (fraude visible) |
| Exécutable déguisé en PDF | upload `fichier_pdf` / `fichier` avec un `.exe` renommé | `400` « magic bytes » |
| Dossier d'une autre SGI | agent SGI sur un dossier étranger | `403` / `404` |
| Champ déjà rempli | double `POST` valeur | `409` |
| Soumettre sans signature | `POST soumettre/` avant l'étape requise | `409` |

---

## 7. Dépannage rapide

| Symptôme | Cause / solution |
|---|---|
| `401` sur tout | token expiré (15 min) → re-login ou `login/refresh/` ; oubli du préfixe `Bearer ` dans Authorize |
| `404` sur les ressources | UUID faux ou ressource d'un autre **tenant** (cloisonnement) |
| `409` à la soumission | convention non acceptée / non publiée, ou état du dossier incompatible — relire le message d'erreur |
| `500` au boot | variables d'env absentes → copier `.env.example` vers `.env` et renseigner `POSTGRES_*` et `DJANGO_SECRET_KEY` |
| Fichiers refusés | format exigé (PDF), taille max du champ (`taille_max_mo`), ou police « magic bytes » |
| Reset complet | `manage.py flush` puis `manage.py seed_demo` (ou recréer la base) |
| MinIO | laisser `MINIO_ENDPOINT_URL` vide en local (mode `media/` local) ; renseigner pour le mode S3 (Docker) |

---

## 8. Ordre de démonstration conseillé (5 min chrono)

1. **Login investisseur** + Authorize.
2. `GET /api/sgi/` → `GET /api/dossiers/etapes-kyc/?sgi={id}` (montrer le KYC).
3. `POST /api/dossiers/dossiers/` → `accepter-convention/` → renseigner
   une valeur → soumettre.
4. **Login agent** → `prendre-en-charge/` → `commenter/` → `valider/`.
5. **Login admin SGI** → publier une convention PDF (montrer le rejet
   d'un fichier `.exe`).
6. **Login admin général** → dashboard → `GET /api/audit/journal/`
   (montrer que tout ce qui précède y est tracé).
7. Finir sur `/api/redoc/` (doc ReDoc) et `/healthz/`.