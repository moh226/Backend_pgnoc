# PGNOC-TI — Référence des endpoints (explication pour la soutenance)

> Chaque endpoint est expliqué de façon **métier d'abord** (à quoi il
> sert dans le processus réglementaire), puis **technique** (méthode,
> corps, accès, garde-fous). Les numéros « UC » renvoient aux cas
> d'utilisation de la conception (UEMOA / CREPMF).

---

## 0. Règles transverses

| Règle | Détail |
|---|---|
| **Authentification** | JWT (SimpleJWT) : `access` 15 min + `refresh` 7 jours avec rotation. Toutes les routes sont protégées sauf `register/` et `login/`. |
| **RBAC** | 4 rôles isolés : Investisseur, Agent SGI, Admin SGI, Admin Général. Chaque espace a sa classe de permission ; une action « étrangère » → `403`. |
| **Multi-tenant** | Toutes les données métier sont **cloisonnées par SGI** : jamais un identifiant de SGI servi par le client pour l'écriture (toujours déduit du compte connecté). |
| **Journal d'audit** | Chaque action significative est tracée (avant/après, IP, User-Agent) dans un journal **append-only** verrouillé au niveau PostgreSQL. |
| **Pagination** | Listes paginées (25 par page, `?page=2`). |
| **Limites de débit** | `login/` : 5/min, `register/` : 10/h (anti brute-force). |
| **Erreurs** | `400` validation, `401` non authentifié, `403` rôle insuffisant, `404` inexistant ou autre SGI, `409` état métier incompatible. |

---

## 1. Espace `comptes` — `/api/comptes/`

### 1.1 `POST /api/comptes/register/`
**Inscription publique d'un investisseur.**
- **Corps** : `email`, `prenom`, `nom`, `password`, `password_confirmation`.
- **Règles** : le rôle est **forcé à INVESTISSEUR côté serveur** (impossible de s'auto-attribuer un rôle privilégié) ; mot de passe fort validé ; double saisie vérifiée ; débit limité à 10/h.
- **Sécurité** : validation d'unicité explicite → `400` propre au lieu d'une erreur 500 en course.

### 1.2 `POST /api/comptes/login/`
**Connexion — obtention des jetons JWT.**
- **Corps** : `email`, `password`.
- **Réponse** : `access`, `refresh`, `utilisateur` (profil complet, dont rôle).
- **Sécurité** : 5 tentatives/min ; **les échecs de connexion sont journalisés avec l'email tenté** (détection brute-force dans le journal).

### 1.3 `POST /api/comptes/login/refresh/`
**Renouvelle le jeton d'accès.** Corps : `refresh`. Rotation active : le refresh consommé est invalidé et un nouveau est émis.

### 1.4 `GET /api/comptes/agents/` · `POST /api/comptes/agents/`
**Gestion des agents de la SGI, par l'Admin SGI (UC18).**
- **GET** : liste des agents de **sa** SGI uniquement.
- **POST** : création `{email, prenom, nom, matricule?, mot_de_passe?}` — la SGI est **déduite du compte connecté** (jamais fournie). Si `mot_de_passe` est absent, un mot de passe aléatoire est **généré et renvoyé une seule fois** (`mot_de_passe_initial`).
- **Atomicité** : création utilisateur + profil dans une même transaction (signal `post_save`).

### 1.5 `GET /api/comptes/agents/<uuid>/` · `PATCH /api/comptes/agents/<uuid>/`
**Détail / modification d'un agent.** Pas de suppression destructive : suspension via `is_active=false`. L'email n'est pas modifiable en PATCH ; le rôle est verrouillé.

### 1.6 `GET /api/comptes/oauth/google/login/`
**Étape 1 du « Connexion avec Google »** (investisseurs uniquement). Redirige le navigateur (302) vers l'écran Google avec un jeton `state` aléatoire conservé en session (anti login-CSRF).

### 1.7 `GET /api/comptes/oauth/google/callback/`
**Étape 2 — rappelé par Google** avec `code` + `state`.
- Vérifie le `state` (anti-falsification), échange le code, vérifie **`email_verified`** (refus d'un email non confirmé par son propriétaire : anti prise de contrôle de compte).
- Relie ou crée le compte ; **refuse si l'email appartient déjà à un rôle privilégié** (un compte AGENT/ADMIN ne doit jamais passer par Google).
- Redirige le front avec les tokens dans le **fragment d'URL** (jamais loggés, jamais envoyés au serveur).

---

## 2. Espace `sgi` — `/api/sgi/`

### 2.1 `GET /api/sgi/`
**Catalogue des SGI partenaires **actives**, pour le choix de l'investisseur** (UC03). Réponse légère : `id`, `nom`, `code_sgi`, `logo`. Une SGI suspendue disparaît du catalogue.

### 2.2 `GET /api/sgi/<uuid>/`
**Fiche d'adhésion de la SGI (UC01)** : présentation marketing + état de la **convention tarifaire** (`signe_requis` = un PDF est publié). C'est ce que l'investisseur consulte avant d'ouvrir son dossier, et **avant d'accepter la convention** (UC16).

### 2.3 `GET /api/sgi/admin/convention/` · `PUT /api/sgi/admin/convention/`
**Publication / mise à jour de la convention tarifaire (UC16), par l'Admin SGI** — cloisonnée à sa propre SGI.
- **GET** : état actuel (titre, nom de fichier, URL signée, dates).
- **PUT** : `multipart/form-data` — `titre` + `fichier_pdf` (vrai fichier PDF).
- **Garde-fous** : extension PDF **et magic bytes `%PDF`** (un exécutable déguisé est rejeté) ; **taille max 10 Mo** ; nom de fichier généré côté serveur (UUID) ; l'ancien PDF est purgé automatiquement après remplacement.
- **Impact métier** : tant que ce PDF n'existe pas, la **soumission des dossiers de la SGI est bloquée** (409).

### 2.4 `GET /api/sgi/admin/presentation/` · `PUT /api/sgi/admin/presentation/`
**Contenu marketing de la SGI (UC16).** `PUT` avec `{"contenu": "..."}`. Simple, mais son absence gâche la fiche UC01 — c'est l'argument commercial de la SGI.

---

## 3. Espace `dossiers` — `/api/dossiers/`

### 3.1 `GET /api/dossiers/etapes-kyc/?sgi=<uuid>`
**Découverte du parcours KYC public d'une SGI (UC03/UC04).**
- Le paramètre `sgi` est **obligatoire** : sans lui, la vue renverrait les formulaires de toutes les SGI à tout utilisateur authentifié (fuite inter-tenant).
- Réponse : étapes avec leurs champs (type, obligatoire, options, conditionnement). L'investisseur sait **exactement** ce qu'il devra produire.

### 3.2 `GET /api/dossiers/dossiers/` · `POST /api/dossiers/dossiers/`
**Liste / création des dossiers.**
- **GET** : liste **selon le rôle** — l'investisseur voit ses dossiers ; le personnel SGI voit la **file d'attente de sa SGI** (avec email de l'investisseur, statut, progression).
- **POST** : `{"sgi": "<uuid>", "etape_courante": "<uuid>"?}` — `utilisateur` est **forcé au compte connecté** (jamais accepté du client) ; la SGI doit être active (une SGI suspendue n'ouvre plus de dossier).

### 3.3 `GET /api/dossiers/dossiers/<uuid>/`
**Détail complet d'un dossier** : statut, référence, progression %, étapes/valeurs, **signature électronique** (si posée). C'est l'écran pivot de l'instruction.

### 3.4 `POST /api/dossiers/dossiers/<uuid>/soumettre/`
**Soumission du dossier pour instruction (UC10). Corps volontairement VIDE.**
- Exige (409 sinon) : **signature OTP posée**, **convention acceptée**, champs obligatoires remplis, SGI active.
- Transition : BROUILLON/REJETE → **SOUMIS**, journalisée + notification à la SGI.
- Le motif de rejet éventuel est purgé à la re-soumission.

### 3.5 `POST /api/dossiers/dossiers/<uuid>/accepter-convention/`
**Accord irréversible de l'investisseur sur la convention (UC16).**
- Exige une **convention publiée** par la SGI (409 sinon) et un dossier en BROUILLON/REJETE.
- **Idempotent** : une seconde acceptation ne crée pas d'erreur, elle renvoie simplement l'état actuel.
- Journalisée (`ACCEPTATION_CONVENTION`).

### 3.6 `POST /api/dossiers/dossiers/<uuid>/generer-otp/`
**Génère le code OTP de signature (preuve serveur).**
- Code de 6 chiffres valable 5 minutes, **jamais stocké en clair** : seul son hash PBKDF2 est conservé.
- L'API le renvoie en clair uniquement pour le développement ; en production, il partirait par SMS/email (canal hors-bande).

### 3.7 `POST /api/dossiers/dossiers/<uuid>/signer/`
**Pose la signature électronique.** Corps : `{"otp_code": "123456"}`.
- Vérifie le code (valide, non expiré, **à usage unique**), puis pose une **preuve chaînée** : hash de la référence du dossier, de l'utilisateur, de la SGI, de l'horodatage, de l'IP et du code (PBKDF2 + SHA-256).
- Un dossier n'est **soumissible et validable qu'une fois signé** — le contrôle devient une condition du workflow, plus un texte libre.

### 3.8 `POST /api/dossiers/dossiers/<uuid>/prendre-en-charge/`
**L'agent SGI prend le dossier en instruction (UC13).**
- Uniquement depuis **SOUMIS** (409 sinon). Transition → **EN_INSTRUCTION**, journalisée, l'investisseur est notifié.

### 3.9 `POST /api/dossiers/dossiers/<uuid>/commenter/`
**L'agent demande une correction sur une valeur (UC09/UC14).**
- Corps : `{"valeur": "<uuid>", "commentaire": "Pièce illisible"}`.
- Effet : le champ repasse à corriger (`est_corrige=false`) ; l'investisseur le voit et le **re-saissit** (le repassage à `true` est automatique à sa prochaine saisie). Journalisé + notification.

### 3.10 `POST /api/dossiers/dossiers/<uuid>/valider/`
**Décision de validation (UC14) — EN_INSTRUCTION → VALIDE.**
- Réservée à l'**agent instructeur** du dossier (l'Admin SGI reste en supervision, mais c'est l'agent qui décide — traçabilité individuelle).
- Exige un dossier signé (la signature posée par l'investisseur est vérifiée).

### 3.11 `POST /api/dossiers/dossiers/<uuid>/rejeter/`
**Décision de rejet (UC14).** Corps : `{"motif_rejet": "..."}`.
- EN_INSTRUCTION → **REJETE** avec motif obligatoire. L'investisseur corrige, re-signature si nécessaire, re-soumet (le motif est alors purgé).

### 3.12 `GET /api/dossiers/dossiers/<uuid>/valeurs/` · `POST /api/dossiers/dossiers/<uuid>/valeurs/`
**Lecture / écriture des valeurs KYC (UC04).**
- **POST** : `{"champ": "<uuid>", "valeur": "..."}` — le champ doit appartenir à la SGI du dossier.
- **Typage strict** : nombre, date ISO, booléen, choix limités aux options définies — un champ « non » ou vide **ne remplit plus** le dossier (le calcul de progression est honnête).
- Un champ FICHIER se remplit **exclusivement** par l'endpoint d'upload dédié (une référence MinIO n'est jamais acceptée du client : anti fuite entre dossiers).
- Un POST sur un champ déjà rempli est refusé (`409`) — le cycle de correction passe par l'agent.

### 3.13 `POST /api/dossiers/dossiers/<uuid>/valeurs/fichier/`
**Téléversement d'un justificatif (UC05).** `multipart/form-data` : `champ` + `fichier`.
- **Magic bytes vérifiés** : l'extension et le Content-Type envoyés par le client sont falsifiables, le contenu ne ment pas.
- Taille plafonnée (**`taille_max_mo` défini par la SGI**, obligatoire pour tout champ FICHIER — un upload ne peut jamais être illimité).
- Nom stocké généré côté serveur (UUID), jamais le nom du client ; le fichier remplace l'ancien et `est_corrige` repasse à `true`.

### 3.14 `GET /api/dossiers/dossiers/<uuid>/valeurs/<valeur_id>/url/`
**URL signée temporaire du justificatif (UC08).**
- 10 minutes de validité (MinIO) ou URL `/media/` en mode local.
- Accès **double** : l'investisseur propriétaire et le personnel de la SGI concernée — et personne d'autre (les justificatifs d'un autre tenant ne sont jamais résolvables).

### 3.15 `GET/POST /api/dossiers/admin/etapes-kyc/` · `GET/PATCH/DELETE /api/dossiers/admin/etapes-kyc/<uuid>/`
**Paramétrage du parcours KYC par l'Admin SGI (UC15).**
- POST : `{"nom": "Identité", "ordre": 1, "actif": true}` — la SGI est forcée au compte connecté (cloisonnement).
- Un seul ordre par numéro d'étape (collision → `400` propre).

### 3.16 `GET/POST /api/dossiers/admin/champs-kyc/` · `GET/PATCH/DELETE /api/dossiers/admin/champs-kyc/<uuid>/`
**Paramétrage des champs KYC (UC15).**
- POST : `{"etape": "<uuid>", "code", "nom", "type", "obligatoire", "ordre", "justification"?, "options_choix"?, "champ_parent"?, "valeur_declencheur"?, "formats_acceptes"?, "taille_max_mo"?}`.
- `type` : TEXTE_COURT, TEXTE_LONG, NOMBRE, DATE, CHOIX_UNIQUE, CHOIX_MULTIPLE, FICHIER.
- Conditions : un champ CHOIX_* exige `options_choix` ; un champ **FICHIER exige `taille_max_mo`** (refus sinon) ; un field conditionnel exige `champ_parent` + `valeur_declencheur` dans la **même étape**.
- `code` unique par SGI ; une étape/champ d'une autre SGI n'est jamais accessible (`403`/`404`).

---

## 4. Espace `audit` — `/api/audit/journal/`

### 4.1 `GET /api/audit/journal/`
**Consultation du journal d'audit (Admin Général uniquement).**
- Filtres optionnels : `action`, `email`, `entite_concernee`, `entite_id`, `date_debut`, `date_fin`.
- Chaque trace : qui (agent), quoi (action), quand (horodatage), **avant/après** en JSONB, IP + User-Agent.
- **Append-only** : un trigger PostgreSQL **interdit UPDATE et DELETE** sur la table, y compris en écriture directe — c'est la preuve de non-altération exigée par le contrôle.

### 4.2 `GET /api/audit/journal/export/`
**Export CSV** du journal (mêmes filtres), fichier téléchargé nommé `journal-audit-<date>.csv`. C'est la matière première des **contrôles CREPMF**.

---

## 5. Espace `notifications` — `/api/notifications/`

### 5.1 `GET /api/notifications/`
**Mes notifications** (celles de l'utilisateur connecté, jamais celles d'autrui). Filtre `?non_lues=true`.

### 5.2 `GET /api/notifications/non-lues/`
**Compteur de non-lues** — le « badge » du frontend.

### 5.3 `POST /api/notifications/<uuid>/marquer-lue/`
**Marque une notification comme lue** — destinataire uniquement (403 sinon).

Les notifications sont émises par le **moteur du workflow** à chaque transition (soumission → SGI, prise en charge/commentaire/décision → investisseur) ; l'envoi est **best-effort** : un échec ne casse jamais la transition, il est journalisé.

---

## 6. Espace Admin Général — `/api/admin-general/`

### 6.1 `GET /api/admin-general/dashboard/`
**Tableau de bord global (UC22)** : volume de dossiers par statut, soumissions du jour, SGI actives et **SGI sans convention publiée** (signal d'alerte réglementaire), répartition des comptes par rôle, et **8 dernières traces d'audit** (flux d'activité temps réel du journal).

### 6.2 `GET/POST /api/admin-general/sgi/`
**Catalogue des SGI partenaires (UC20).**
- POST : `{"nom": "...", "code_sgi": "XXX", "est_active": true}` (+ `logo` en multipart).
- Chaque SGI apparaît avec ses **compteurs** (utilisateurs, dossiers).

### 6.3 `GET/PATCH /api/admin-general/sgi/<uuid>/`
**Détail / suspension d'une SGI (UC20).** Pas de DELETE : `est_active=false` bloque l'ouverture de nouveaux dossiers (contrôle du workflow). Chaque modification est journalisée (`MODIFICATION_SGI`).

### 6.4 `GET/POST /api/admin-general/utilisateurs/`
**Comptes internes (UC21) — création d'agents, d'admins SGI et d'admins généraux.**
- Corps : `{"email", "prenom", "nom", "role": "AGENT_SGI"|"ADMIN_SGI"|"ADMIN_GENERAL", "sgi"?, "mot_de_passe"}`.
- **Les comptes INVESTISSEUR sont refusés ici** : ils passent par l'inscription publique (un seul canal, pas de contournement).
- Le mot de passe initial est **fourni par l'Admin Général** (jamais généré côté serveur : remise par canal sûr).
- Cohérence rôle/SGI validée applicativement **et verrouillée au niveau base** (trigger PostgreSQL).

### 6.5 `GET/PATCH /api/admin-general/utilisateurs/<uuid>/`
**Modification d'un compte interne (UC21).**
- Garde-fous : un admin ne peut **ni se désactiver ni changer son propre rôle** ; le **dernier admin général actif** est protégé (impossible de verrouiller la plateforme hors de portée).

---

## 7. Infrastructure — `/api/...`

| Endpoint | Méthode | Rôle | Explication |
|---|---|---|---|
| `/healthz/` | GET | public | Sonde de santé pour Docker/orchestrateur : 200 si la base répond, 503 sinon. |
| `/api/schema/` | GET | public | Schéma OpenAPI 3.0.3 brut (JSON) de toute l'API. |
| `/api/docs/` | GET | public | Documentation interactive **Swagger UI**. |
| `/api/redoc/` | GET | public | Documentation **ReDoc** (lecture). |
| `/admin/` | — | staff | Interface d'administration Django (gestion technique). |

---

## 8. Les 5 arguments sécurité à mettre en avant à l'oral

1. **Journal d'audit immuable** (append-only au niveau base) + état avant/après + IP — la conformité CREPMF est **prouvable**, pas déclarative.
2. **Signature électronique OTP serveur** : le code n'est jamais stocké en clair, la preuve est chaînée et horodatée — le « contrôle sans preuve » est devenu structurellement impossible.
3. **Uploads défendus en profondeur** : magic bytes (fichier réel, pas d'extension fiée), tailles plafonnées, noms générés côté serveur, URLs signées à durée courte.
4. **Verrouillage base sur le RBAC** : même une écriture SQL directe qui violerait la cohérence rôle/SGI est refusée par le trigger PostgreSQL.
5. **Zero-trust sur les identifiants** : le rôle, l'utilisateur et la SGI ne sont jamais pris d'un corps de requête — ils sont déduits du compte authentifié.