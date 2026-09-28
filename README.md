# Planning → Agenda

Importe le PDF de planning d'une personne vers un agenda Google partagé. Elle
se connecte avec Google, dépose son PDF, vérifie les créneaux détectés dans
une prévisualisation, valide.

## Développement local

```bash
uv sync
cp .env.example .env   # puis remplir les valeurs (jamais commité)
uv run uvicorn app.main:app --reload
```

Avant chaque commit :

```bash
uv run ruff format . && uv run ruff check . && uv run pytest -q
```

Aucun appel réseau dans les tests : Google et le modèle sont remplacés par
des doubles ou simplement pas exercés (`tests/conftest.py` fixe des
variables d'environnement factices).

## Déploiement

Une seule autorité de déploiement : **Railway, branché sur le repo GitHub,
déploie chaque commit qui arrive sur `main`.** GitHub Actions ne déploie
rien, il teste (job `test` : lint + tests ; job `docker` : build de l'image
+ smoke test `/health`).

## Étapes manuelles (à faire une fois, par un humain)

Ces étapes ne peuvent pas être faites par l'agent (accès Railway, Google
Cloud, Google Agenda requis). Tant qu'elles ne sont pas faites, l'app n'est
ni protégée contre les push directs, ni déployée, ni utilisable.

### GitHub

1. Settings → Rules → Rulesets sur `main` : exiger une PR, exiger les status
   checks `test` et `docker`, interdire les push directs (vous y compris).

### Railway

2. New Project → Deploy from GitHub repo → ce repo, branche `main`. Le
   premier déploiement échoue faute de variables : normal.
3. Variables du service (voir `.env.example`), plus `DATA_DIR=/data`.
4. Volume : Attach Volume, point de montage `/data`.
5. Settings → Networking → Generate Domain. Noter l'URL.
6. Settings → Deploy → Healthcheck Path `/health`, Restart Policy
   « On failure ».
7. Settings → Source → **Wait for CI** : activer (le toggle n'apparaît que
   si `.github/workflows/ci.yml` avec `push: branches: [main]` existe déjà).
8. `Cmd+K → Deploy latest commit`. Ouvrir `https://<domaine>/health`.

### Google Cloud / Google Agenda

9. Créer l'agenda partagé depuis votre compte Gmail, dans Google Agenda.
10. Sur console.cloud.google.com : créer un projet, activer l'API Google
    Calendar.
11. Créer un compte de service, générer une clé JSON
    (→ `GOOGLE_SERVICE_ACCOUNT_JSON`).
12. Partager l'agenda avec l'adresse du compte de service, permission
    « Apporter des modifications aux événements ».
13. Récupérer l'ID de l'agenda dans Paramètres → Intégrer l'agenda
    (→ `CALENDAR_ID`).
14. Créer des identifiants OAuth 2.0 « application web »
    (→ `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`), avec comme URI de
    redirection `https://<domaine>/auth/callback` (domaine noté à l'étape 5).
15. Écran de consentement OAuth : type « Externe », mode « Test », ajouter
    les emails des amis comme utilisateurs test. Sans ça, Google affiche
    « Accès bloqué » à leur première connexion.
16. Partager l'agenda avec les amis depuis Google Agenda.

Ensuite, chaque merge sur `main` est un déploiement, sans rien toucher.
Une variable à changer se modifie dans Railway, qui redéploie.

## Structure

- `app/db.py` : connexion SQLite (schéma, tables) partagée entre `log.py` et `auth.py`
- `app/upload.py` : routes de dépôt du PDF, orchestration `locate` → `crop`,
  écrans de secours (nom à préciser, recadrage manuel)
