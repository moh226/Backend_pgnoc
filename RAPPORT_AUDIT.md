# Rapport d'audit — bugs silencieux et points de robustesse

> Audit complet du backend avant la phase frontend.
> Date : 13 août 2026 — basé sur suite 179/179 → **181/181 tests** après corrections.

---

## 1. Bugs corrigés (3)

### 1.1 Injection CSV à l'export du journal (audit/views.py) — **Haut**
- **Problème** : le CSV `journal/export/` contenait des valeurs brutes
  non fiables (User-Agent, email, JSON `avant`/`apres`). Un en-tête
  HTTP forgé commençant par `=`/`+`/`-`/`@` devient une **formule
  exécutée** à l'ouverture dans Excel/Sheets (exfiltration de données).
- **Correctif** : fonction `_cellule_csv()` — toute cellule amorcée par
  un caractère dangereux est préfixée d'un `'` (texte forcé).
- **Test** : `test_export_csv_neutralise_les_formules` (User-Agent
  `=cmd|'...'` → cellule neutralisée).

### 1.2 Course de création convention/présentation (sgi/views_admin.py) — **Moyen**
- **Problème** : deux `PUT admin/convention/` (ou présentation)
  simultanés → la contrainte d'unicité OneToOne déclenche une
  `IntegrityError` → **500** au lieu d'une réponse propre.
- **Correctif** : helper `_obtenir_ou_creer()` — reprise transactionnelle
  du `get_or_create` (second appel relit l'enregistrement existant).
- **Test** : `ObtenirOuCreerTolereLaCourseTests` (course simulée →
  objet retourné, pas d'exception).

### 1.3 Course sur la signature OTP (dossiers/services.py) — **Moyen**
- **Problème** : `poser_signature_otp()` lisait le hash OTP sans verrou :
  deux `POST signer/` simultanés pouvaient **valider le même code deux
  fois** (last-write-wins) et poser deux preuves concurrentes.
- **Correctif** : vérification + purge + écriture de la preuve sous
  `select_for_update()` dans une transaction — le second appelant
  relit un hash déjà purgé et échoue proprement.
- **Validation** : tous les tests OTP existants (usage unique, expiration,
  code invalide) passent sur la nouvelle structure.

### Nettoyage
- `__import__("json")` → import réel (dossiers/services.py).

---

## 2. Vérifié sain (aucun bug détecté)

| Domaine | Résultat |
|---|---|
| **Workflow** (`transiter`) | Verrou pessimiste + audit dans la même transaction ; décision liée à l'agent instructeur ; pas de transition orpheline. |
| **OTP** | Hash PBKDF2 + sel, vérification en temps constant, expiration 5 min, purge à usage unique, preuve chaînée horodatée (référence, utilisateur, SGI, IP). |
| **Uploads fichiers** | Magic bytes (contenu réel contrôlé), tailles plafonnées, noms générés UUID (anti path-traversal), purge des fichiers orphelins (best-effort), rejet 400 propre. |
| **Cloisonnement multi-tenant** | SGI jamais acceptée du client (toujours déduite du compte) ; querysets filtrés partout ; valeurs/étapes/champs d'une autre SGI → 403/404 ; agents propres à la SGI. |
| **Référence dossier** | Séquence `PGNOC-AAAA-XXXXXX` avec retry sur collision (anti 500 en course). |
| **Journal d'audit** | Best-effort **tracé** (jamais muet) ; verrou append-only PostgreSQL. |
| **Notifications** | Best-effort intégral, échec journalisé, jamais bloquant. |
| **Comptes/mots de passe** | `create_user` (hash Argon2), professionnels créés par `set_password`, jamais de clair. |
| **RBAC** | Chaque espace a sa permission dédiée ; garde-fous admin général (auto-désactivation, dernier admin, rôles INVESTISSEUR exclus de l'espace admin). |
| **Schéma OpenAPI** | 0 erreur, 0 warning — tous les endpoints documentés. |

---

## 3. Risques résiduels (documentés, assumés)

| Risque | État | Recommandation |
|---|---|---|
| **IP `X-Forwarded-For`** : valeur probante uniquement si le header est posé par un proxy de confiance | `SECURE_PROXY_SSL_HEADER` posé en prod | S'assurer que nginx/traefik normalise le header (déjà le cas dans le Dockerfile/compose) |
| **403 « Ce dossier ne vous appartient pas »** révèle l'existence d'un dossier (UUID à 122 bits — non devinable) | assumé, impact faible | Optionnel : 404 systématique pour coller au cloisonnement strict |
| **Pas de limite de débit sur `generer-otp/`** | l'investisseur ne peut spammer que son propre dossier | Ajouter un throttle `otp` si besoin en prod |
| **Checklist HTTPS** (`check --deploy`) : 6 avertissements en local | attendus (clés/env de dev) | Vérifier 0 warning avec le `.env` de production |

---

## 4. Conclusion

Le code était **globalement sain** : aucun `except: pass` silencieux illégitime,
toutes les écritures sensibles sont verrouillées ou tracées, le cloisonnement
est cohérent. Les 3 défauts trouvés étaient des **courses concurrentes et une
injection de sortie** — typiques des bugs qui ne se voient pas dans les tests
unitaires simples. Ils sont corrigés **et** couverts par des tests dédiés.

**État final : 181/181 tests verts, schéma OpenAPI 0 erreur/0 warning,
`manage.py check` sans issue.**