# Import de planning PDF vers Google Agenda — Plan v4 (simplifié)

## 0. Ce qui change par rapport à la v3

Le point de départ a changé : **chaque personne a un PDF différent** (employeur différent, mise en page différente). La v3 misait 70 % de l'effort sur la détection automatique de la mise en page (colonnes, clustering). Face à des mises en page inconnues, c'est intenable.

La v4 ne modélise plus la mise en page. Elle cherche deux choses par leur **contenu** — le nom de la personne et les dates de l'en-tête — et s'appuie sur les traits du tableau pour délimiter la ligne. Quand l'une des trois recherches échoue, la personne encadre sa ligne à la main. C'est un secours, pas une étape.

Second changement : l'app est pour toi et quatre ou cinq amis. Tout ce qui servait à administrer « une équipe » depuis une interface (config chiffrée, trois portes d'authentification, journal filtrable) est remplacé par des variables d'environnement Railway et une page admin d'une vingtaine de lignes.

| Retiré | Remplacé par |
|---|---|
| Détection de colonnes, clustering de la mise en page | Localisation par le nom, les traits du tableau et les dates ; recadrage manuel en secours |
| Correspondance nom Google ↔ nom PDF à variantes multiples | Tentative auto, puis la personne tape son nom tel qu'il apparaît, une seule fois (`users.pdf_name`) |
| Neutralisation des codes d'absence par regex | La découpe exclut les collègues ; ce qui reste appartient à la personne |
| `ADMIN_PASSWORD`, `APP_PASSWORD`, config chiffrée Fernet, `SECRET_KEY` | Sign in with Google pour tout le monde, `ADMIN_EMAILS` en variable d'environnement |
| Écrans Configuration, Utilisateurs, Accès | Variables d'environnement Railway |
| Journal filtrable, paginé, export CSV, purge paramétrable | Une table `imports`, une page qui liste les 200 dernières lignes |
| Phase 0 « PDF scanné = bloquant » | Un scan passe par le recadrage manuel : dégradé, pas bloquant |

Estimation : **4 jours** au lieu de 6, dont une demi-journée de CI et déploiement, absents de la v3.

---

## 1. Objectif

Une personne se connecte avec Google et dépose le PDF de son planning. L'app trouve sa ligne, la découpe, en extrait les créneaux et les lui montre à côté de l'image. Elle valide, ses créneaux apparaissent sur l'agenda Google partagé. La première fois, elle indique éventuellement son nom tel qu'il est écrit dans le PDF. Toi seul vois la page admin.

## 2. Principe directeur

**Seules l'en-tête et la ligne de la personne partent vers le modèle**, et elle voit cette image avant de valider. Les collègues ne sont jamais dans le cadre. Le modèle rend du JSON et ne touche jamais à l'agenda : toute l'écriture est dans ton code.

```
PDF ─▶ [LOCAL] nom      ─▶ position de la ligne
               traits   ─▶ bornes de la ligne
               dates    ─▶ bande d'en-tête
                 │
                 ├─▶ échec ─▶ la personne encadre à la main
                 ▼
       image en-tête + ligne ─▶ modèle vision ─▶ JSON
                                                  │
                                                  ▼
                          [LOCAL] validation ─▶ prévisualisation ─▶ Calendar API
```

---

## 3. Authentification

Une seule porte : **Sign in with Google**, scope `openid email profile`.

```
ALLOWED_EMAILS=ami1@gmail.com,ami2@gmail.com
ADMIN_EMAILS=toi@gmail.com
```

- Accès si l'email est dans l'une des deux listes ; page `/admin` si dans `ADMIN_EMAILS`.
- `email_verified` doit être vrai.
- Comparaison sur l'email exact en minuscules.
- **Les listes sont vérifiées à chaque requête**, pas seulement au login. Retirer un email et redéployer coupe l'accès immédiatement, sans gestion de session.
- Listes vides = personne ne passe. L'app le signale dans ses logs au démarrage.
- Pas de porte de secours : les identifiants OAuth sont en variables d'environnement, donc disponibles dès le premier déploiement. Le problème d'amorçage de la v3 n'existe plus.

Une tentative refusée est journalisée avec l'email tenté. Pour autoriser quelqu'un : ajouter l'email dans Railway, redéployer. Trente secondes, cinq fois par an.

À la première connexion, l'app ne demande rien. Le nom dans le PDF n'est demandé que si la recherche automatique échoue (section 5B).

---

## 4. Configuration

Tout en variables d'environnement Railway :

```
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
SESSION_SECRET=              # signe le cookie de session
ALLOWED_EMAILS=
ADMIN_EMAILS=
CALENDAR_ID=                 # xxxx@group.calendar.google.com
GOOGLE_SERVICE_ACCOUNT_JSON= # contenu du fichier JSON, sur une ligne
LLM_API_KEY=
LLM_MODEL=
TZ=Europe/Paris
VALIDATION_WEEKS=8
DATA_DIR=/data                # point de montage du volume Railway
```

Railway chiffre ses variables et les masque dans l'interface. Pas de table `config`, pas de Fernet, rien à sauvegarder à part la base SQLite. `settings.py` échoue explicitement au démarrage si une variable manque.

**Page admin** (`/admin`) :

- *Tester l'agenda* : crée un événement bidon, le lit, le supprime.
- *Tester le modèle* : envoie une image de test, vérifie que le JSON revient.
- Les 200 derniers imports (section 7).

Les deux boutons restent : sans eux, la première erreur de config se découvre quand quelqu'un perd son planning.

---

## 5. Le pipeline

### A — Dépôt et rendu

Upload du PDF (limite 10 Mo, 10 pages). `pdfplumber` ouvre le fichier pour le texte et les traits ; `pypdfium2` rend les pages en PNG à `scale=3` (~216 dpi, nécessaire pour que le modèle lise des horaires en petite police). Les fichiers vivent dans `/tmp`, dans un dossier lié à la session, supprimés à la validation ou après 30 minutes.

Test de couche texte : `extract_words()` vide sur toutes les pages → c'est un scan → directement au recadrage manuel (B bis).

### B — Localisation automatique de la ligne

Trois recherches par contenu, aucune hypothèse sur la mise en page.

**1. Le nom.** Normalisation des deux côtés : majuscules, sans accents ni ponctuation. Candidats, dans l'ordre : `users.pdf_name` s'il existe ; sinon, à partir du compte Google, `NOM PRENOM`, `PRENOM NOM`, `NOM P`, `P NOM`, puis `NOM` seul. La recherche recolle les mots adjacents d'une même ligne, un nom composé pouvant faire deux ou trois mots.

- Exactement une occurrence → on continue. Si elle vient d'une variante Google, on l'enregistre dans `pdf_name`.
- Zéro → la personne tape son nom tel qu'il apparaît sur le PDF. Enregistré, plus jamais redemandé tant que ça marche.
- Plusieurs → homonyme ; on lui demande une chaîne plus précise (`DUPONT J.` plutôt que `DUPONT`).

**2. Les bornes de la ligne.** Les traits horizontaux du tableau (`page.horizontal_edges`, filtrés sur une longueur d'au moins un tiers de la largeur de la page) : le plus proche au-dessus du haut du nom, le plus proche au-dessous de son bas. Ça gère une personne qui occupe deux hauteurs de ligne. Pas de trait de chaque côté → secours.

**3. L'en-tête.** Les mots qui correspondent à un motif de date (`Lun 15`, `15/09`, `lundi 15 sept`, `15 sept.`), situés au-dessus de la ligne et alignés à ±5 pt. On prend la bande verticale qui les contient, avec une marge, plus la ligne de texte immédiatement au-dessous si elle est bornée par le même trait (jours et dates sont souvent sur deux lignes). Moins de 3 dates alignées → secours.

**Composition.** En-tête empilée au-dessus de la ligne, sur toute la largeur du tableau, en une seule image PNG. La colonne du nom reste dans la découpe : c'est le nom de la personne elle-même, et le voir dans la prévisualisation est le moyen le plus simple de vérifier qu'on est sur la bonne ligne.

### B bis — Recadrage manuel (secours)

Déclenché quand l'une des trois recherches échoue, ou pour un scan. L'écran affiche la page ; la personne trace un rectangle qui contient l'en-tête et sa ligne (Cropper.js via CDN, découpe côté serveur avec Pillow). Le rectangle est mémorisé dans `users.last_crop` et pré-positionné la fois suivante, ce qui rend le secours presque aussi rapide que l'automatique pour un ami dont le PDF n'a pas de traits.

Le bouton *Recadrer à la main* est aussi disponible depuis la prévisualisation, si la découpe automatique est visiblement fausse.

### C — Appel au modèle

Image recadrée + prompt court. JSON strict, température 0. **La date du jour dans le prompt**, sinon l'année est inventée sur un en-tête `Lun 15`.

Réponse attendue :

```json
{
  "periode": {"debut": "2026-09-14", "fin": "2026-09-20"},
  "creneaux": [
    {"date": "2026-09-15", "debut": "09:00", "fin": "17:30", "lieu": "Site B"}
  ]
}
```

`periode` est la plage couverte par l'en-tête, pas par les créneaux. C'est elle qui délimite les suppressions (section 6) : un jour de repos en fin de semaine doit bien effacer un ancien créneau à cette date.

Choix du modèle : n'importe quel modèle vision correct lit un tableau d'horaires. Deux points d'attention :

- Sur le **tier gratuit** de l'API Gemini, Google peut utiliser les prompts et réponses pour améliorer ses produits, et les conditions comportent des clauses spécifiques à l'EEE, à lire. Pour cinq images par semaine, le tier payant coûte quelques centimes par mois : le prendre d'emblée.
- Garder le prompt et le schéma indépendants du fournisseur, derrière une fonction `extract(image_png) -> dict`, pour pouvoir changer.

### D — Validation locale

Rejet si :

- `periode` dure plus de 6 semaines (mauvaise découpe ou hallucination)
- une date de créneau est hors de `[periode.debut, periode.fin]`
- `periode` est hors de `[aujourd'hui − 2 semaines, aujourd'hui + VALIDATION_WEEKS]`
- la durée d'un créneau est hors de 1 h – 14 h, **après** traitement des nuits : si `fin < debut`, le créneau se termine le lendemain (`21:00–07:00` = 10 h). C'était un bug de la v3, qui rejetait toute nuit.

Pas de plafond de créneaux par semaine : les coupures (`9h-13h` + `15h-19h`) sont légitimes. La prévisualisation est le garde-fou, pas une règle arbitraire.

Un échec renvoie à la prévisualisation avec le motif. Jamais d'écriture partielle.

### E — Prévisualisation

Côte à côte : l'image découpée, les créneaux détectés en tableau, et les anciens créneaux qui seront remplacés (« 4 créneaux du 14 au 20 septembre »). Boutons *Valider*, *Recadrer à la main*, *Changer le nom recherché*.

C'est la protection principale contre le risque numéro un du projet : une ligne de décalage, et on importe les horaires du voisin. Le nom de la personne est visible dans l'image découpée ; la première fois elle vérifie, ensuite c'est un coup d'œil.

---

## 6. Écriture dans l'agenda

La section que la v3 avait laissée vide.

**Tags** sur chaque événement créé :

```python
"extendedProperties": {"private": {
    "app": "planning-import",
    "email": "ami1@gmail.com"
}}
```

**Séquence**, pour un import couvrant `[debut, fin]` :

1. Lister les événements de l'agenda entre `debut` et `fin + 1 jour` avec `privateExtendedProperty=app=planning-import` **et** `privateExtendedProperty=email=<email>`. C'est la liste « sera remplacé » de la prévisualisation.
2. Insérer les nouveaux créneaux. Noter les IDs créés.
3. Si toutes les insertions passent, supprimer les événements de l'étape 1.
4. Si une insertion échoue : supprimer les événements créés à l'étape 2 (meilleur effort), ne rien supprimer de l'étape 1, afficher l'erreur. L'agenda reste dans l'état d'avant.

Insérer avant de supprimer : une panne à mi-chemin laisse l'ancienne semaine en place plutôt qu'un trou.

**Règle absolue conservée** : ne jamais supprimer un événement sans le tag `app`, même s'il tombe sur le même créneau. Les réunions ajoutées à la main sont intouchables.

Détails :

- Titre : `Prénom Nom — 9h-17h30`, avec le `name` du compte Google.
- `colorId` dérivé d'un hash de l'email.
- `start` / `end` avec `timeZone: Europe/Paris` explicite, jamais d'UTC converti.
- `reminders: {"useDefault": true}`. Les rappels se règlent par chacun dans son Google Agenda, sur l'agenda partagé. À écrire sur la page d'accueil, sinon quelqu'un signalera que les notifications ne marchent pas.

---

## 7. Données

Deux tables.

```sql
CREATE TABLE users (
  email       TEXT PRIMARY KEY,
  pdf_name    TEXT,            -- nom tel qu'il apparaît dans le PDF, déduit ou tapé
  last_crop   TEXT,            -- JSON {page, x, y, w, h} du dernier recadrage manuel
  created_at  TEXT NOT NULL,
  last_seen   TEXT
);

CREATE TABLE imports (
  id          INTEGER PRIMARY KEY,
  ts          TEXT NOT NULL,
  request_id  TEXT NOT NULL,
  email       TEXT NOT NULL,
  step        TEXT NOT NULL,   -- login | upload | locate | crop | llm | validate | write
  status      TEXT NOT NULL,   -- ok | error | denied
  detail      TEXT             -- JSON : compteurs, IDs, motif. Jamais de contenu.
);
CREATE INDEX idx_imports_ts ON imports(ts DESC);
```

`imports` est en ajout seul. `request_id` est commun à toute la chaîne d'un import. La page admin liste les 200 dernières lignes, une ligne se déplie sur `detail`. Un filtre par email en query string si besoin, rien de plus.

Règles : jamais de contenu de PDF ni d'image, jamais de secret. `locate` journalise « trouvé par variante Google / par pdf_name / échec, motif », jamais les noms lus dans le PDF. Purge des lignes de plus de 90 jours au démarrage de l'app.

---

## 8. Configuration Google (une fois, manuellement)

Identique à la v3 :

1. Créer l'agenda partagé depuis ton compte Gmail, dans Google Agenda.
2. Sur console.cloud.google.com : projet, activer l'API Google Calendar.
3. Créer un compte de service, générer une clé JSON.
4. Partager l'agenda avec l'adresse du compte de service, permission « Apporter des modifications aux événements ».
5. Récupérer l'ID de l'agenda dans Paramètres → Intégrer l'agenda.
6. Créer des identifiants OAuth 2.0 « application web », avec l'URI de redirection de ton app Railway.
7. Écran de consentement OAuth : type « Externe », laisser en mode « Test » et **ajouter les emails des amis comme utilisateurs test**. Sans ça, Google affiche « Accès bloqué » à leur première connexion. Les scopes `openid email profile` ne demandent aucune vérification.
8. Partager l'agenda avec les amis depuis Google Agenda.

---

## 9. Stack et structure

| Brique | Choix |
|---|---|
| Backend | Python + FastAPI |
| Texte et traits du PDF | `pdfplumber` |
| Rendu PDF → PNG | `pypdfium2` |
| Découpe | `Pillow` ; Cropper.js côté navigateur pour le secours |
| Agenda | `google-api-python-client` + compte de service |
| OAuth | `authlib` |
| Modèle vision | SDK du fournisseur choisi, derrière `extract()` |
| Frontend | Jinja2 + htmx |
| Base | SQLite sur volume Railway |

```
.github/workflows/ci.yml   # section 13
Dockerfile
pyproject.toml
uv.lock
tests/
app/
  main.py             # + /health, sans dépendance externe
  settings.py         # variables d'environnement, échec explicite si une manque
  auth.py             # OAuth Google + ALLOWED_EMAILS + ADMIN_EMAILS
  pdf/
    render.py         # pypdfium2, PNG par page
    locate.py         # nom, traits, dates → rectangle ; ou None avec le motif
    crop.py           # découpe + empilement en-tête / ligne
  llm.py              # extract(image) -> dict
  validate.py
  calendar_sync.py    # lister / insérer / supprimer par tags
  log.py              # log(request_id, email, step, status, detail)
  admin.py            # /admin : tests de santé + journal
  templates/
```

Plus de `cryptography`, plus de `tools/diagnose_pdf.py` : `locate.py` est une fonction pure, testable en local sur un PDF avec deux lignes de Python.

---

## 10. Risques

| Risque | Gravité | Parade |
|---|---|---|
| Décalage d'une ligne : on importe les horaires du voisin | **Élevée**, silencieuse | Prévisualisation avec l'image découpée, nom de la personne visible dedans |
| Le modèle lit mal une heure | Élevée | Prévisualisation image + tableau côte à côte |
| Suppression d'événements non créés par l'app | Élevée | Filtre strict sur le tag `app` |
| Sign-In sans restriction | Élevée | `ALLOWED_EMAILS`, vérifié à chaque requête |
| Hallucination d'année | Moyenne | Date du jour dans le prompt + fenêtre de validation |
| Panne pendant l'écriture | Moyenne | Insertion avant suppression |
| Nom introuvable, homonyme, tableau sans traits, dates non reconnues | Faible | Champ texte, puis recadrage manuel mémorisé |
| PDF scanné | Faible | Recadrage manuel |
| Mise en page qui change | Faible | La localisation ne dépend pas de la mise en page ; elle se refait à chaque import |

---

## 11. Ordre de travail

**Phase 1 — Socle (1 j).** Repo, `ci.yml`, `Dockerfile`, `/health`, premier déploiement Railway avec une app vide mais en ligne (section 13). Puis FastAPI, `settings.py`, Sign in with Google, listes d'emails, tables, `log()`. Déployer en premier : tout ce qui suit part en prod à chaque merge.

**Phase 2 — Localisation et découpe (1,5 j).** `locate.py` d'abord, testé en local sur les vrais PDF de deux ou trois amis avant d'écrire le moindre écran : c'est le seul point d'incertitude du projet. Puis upload, rendu, écran de recadrage de secours.

**Phase 3 — Modèle + validation + prévisualisation (½ j).**

**Phase 4 — Écriture agenda (½ j).** Sur un agenda jetable d'abord.

**Phase 5 — Admin (½ j).** Tests de santé, journal.

**≈ 4 jours.**

---

## 12. Améliorations rapides, une fois que ça tourne

- **Double vérification par le modèle** : lui demander aussi le nom lu dans la ligne et le nombre de lignes de personnes visibles. Rejeter si ce n'est pas exactement une ligne ou si le nom ne correspond pas à `pdf_name`. Cinq lignes de code contre le risque numéro un.
- **Importer pour quelqu'un d'autre** depuis `/admin`, si un ami te transfère son PDF au lieu de se connecter. Le tag `email` porte alors le sien.

## 13. Repo, CI et déploiement

### Principe

Une seule autorité de déploiement : **Railway, branché sur le repo GitHub, déploie chaque commit qui arrive sur `main`.** GitHub Actions ne déploie rien ; il teste. Deux verrous empêchent qu'un commit cassé arrive en prod :

1. **Protection de branche sur `main`** : pas de push direct, une PR obligatoire, les checks `test` et `docker` verts avant de merger. C'est le vrai verrou.
2. **« Wait for CI » côté Railway** : le déploiement d'un commit attend que tous les workflows de ce commit soient terminés ; si l'un échoue, le déploiement est sauté. Ceinture en plus des bretelles, pour le cas où quelqu'un contourne la PR.

Pas de token Railway dans GitHub, pas de job de déploiement à maintenir. Si un jour un déploiement reste bloqué en WAITING alors que le CI est vert (ça arrive, d'après les forums Railway), `Cmd+K → Deploy latest commit` dans Railway, ou désactiver le toggle : la protection de branche suffit.

**Ne pas créer de `railway.toml` ni `railway.json`** : déprécié, plus disponible pour les nouveaux services. Le `Dockerfile` à la racine est ce que Railway construit ; le reste (healthcheck, volume, variables) se règle dans le dashboard.

### Fichiers à créer par l'agent

```
.github/workflows/ci.yml
Dockerfile
.dockerignore
pyproject.toml            # dépendances + config ruff / pytest
uv.lock
.env.example
.gitignore                # .env, *.db, data/, tests/fixtures/private/
README.md                 # reprend les étapes manuelles ci-dessous, pour ne pas les perdre
app/                      # section 9
tests/
```

### `.github/workflows/ci.yml`

```yaml
name: CI

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]      # requis pour que « Wait for CI » apparaisse côté Railway

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          enable-cache: true
      - run: uv python install 3.12
      - run: uv sync --frozen
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run pytest -q

  docker:
    runs-on: ubuntu-latest
    needs: test
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t planning:ci .
      - name: Smoke test
        run: |
          docker run -d --name app -p 8000:8000 \
            -e PORT=8000 -e DATA_DIR=/tmp \
            -e GOOGLE_CLIENT_ID=x -e GOOGLE_CLIENT_SECRET=x -e SESSION_SECRET=x \
            -e ALLOWED_EMAILS=a@b.c -e ADMIN_EMAILS=a@b.c -e CALENDAR_ID=x \
            -e GOOGLE_SERVICE_ACCOUNT_JSON='{}' -e LLM_API_KEY=x -e LLM_MODEL=x \
            -e TZ=Europe/Paris -e VALIDATION_WEEKS=8 \
            planning:ci
          for i in $(seq 1 20); do
            curl -fsS http://localhost:8000/health && exit 0
            sleep 1
          done
          docker logs app
          exit 1
```

Le job `docker` existe pour une raison précise : « les tests passent mais l'image ne démarre pas » est la panne classique d'un déploiement automatique. Il construit la même image que Railway et vérifie qu'elle répond.

Pas de `concurrency: cancel-in-progress` sur ce workflow : un run annulé sur `main` est vu comme un échec par Railway. Versions des actions à vérifier au moment de l'écriture.

### `Dockerfile`

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv   # épingler une version

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app

# Pas d'utilisateur non-root : le volume Railway est monté root, et l'app est mono-tenant.
CMD ["sh", "-c", "/app/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
```

`pypdfium2` et `Pillow` livrent leurs binaires dans leurs wheels : aucun paquet système à installer.

### `pyproject.toml` (extrait)

```toml
[project]
name = "planning-agenda"
requires-python = ">=3.12"
dependencies = [
  "fastapi", "uvicorn[standard]", "jinja2", "python-multipart", "itsdangerous",
  "authlib", "httpx",
  "pdfplumber", "pypdfium2", "pillow",
  "google-api-python-client", "google-auth",
  # + le SDK du fournisseur de modèle choisi
]

[dependency-groups]
dev = ["pytest", "ruff", "reportlab"]

[tool.ruff]
line-length = 100
```

### Tests : ce que le CI vérifie

| Module | Ce qui est testé |
|---|---|
| `pdf/locate.py` | PDF **synthétiques générés par le test** avec `reportlab` : un tableau à 5 noms et en-tête de dates, une personne sur deux hauteurs, deux homonymes, un tableau sans traits. Vérifie la ligne trouvée, ses bornes, l'en-tête, et que chaque cas d'échec renvoie le bon motif. |
| `validate.py` | Nuits (`21:00–07:00` = 10 h), coupures, fenêtre, `periode` > 6 semaines, date hors `periode`. |
| `calendar_sync.py` | Faux client en mémoire : insertion avant suppression, filtre sur les tags, retour à l'état d'avant si une insertion échoue, jamais de suppression sans tag `app`. |
| `auth.py` | Listes d'emails, `email_verified` faux, casse, listes vides. |
| `llm.py` | `extract()` avec un fournisseur factice qui renvoie un JSON fixe ; JSON malformé → erreur propre, jamais d'exception brute. |
| `main.py` | `TestClient` : `/health` → 200 ; `/` anonyme → redirection login ; `/admin` non-admin → 403. |

Les vrais PDF de tes amis vont dans `tests/fixtures/private/`, ignoré par git ; les tests qui les utilisent sont marqués `skip` si le dossier est vide. Ils tournent chez toi, jamais dans GitHub.

**Zéro appel réseau dans le CI** : ni Google, ni le modèle. Les deux boutons de test de santé de `/admin` font ce travail en prod.

### Contraintes sur l'app pour que tout ça tienne

- `/health` répond `200 {"status": "ok"}` sans toucher ni à Google, ni au modèle, ni au disque. C'est ce que Railway interroge avant de basculer le trafic et ce que le smoke test vérifie.
- `settings.py` vérifie la **présence** des variables, pas leur validité. Les clients Google et LLM sont construits à la première utilisation, pas au démarrage. Sinon le smoke test et le premier déploiement échouent sur des valeurs factices.
- La base est `DATA_DIR/app.db`. En local `DATA_DIR=./data`, sur Railway `/data`, le point de montage du volume. Le dossier est créé au démarrage s'il manque, les tables aussi (`CREATE TABLE IF NOT EXISTS`).
- L'app écoute sur `0.0.0.0:$PORT`. Railway injecte `PORT`.
- Un seul replica : un volume ne se partage pas. Un déploiement avec volume coupe le service quelques secondes, sans importance ici.

### Mise en place manuelle (une fois, ~20 minutes)

**GitHub**

1. Créer le repo vide. L'agent pousse le squelette : `ci.yml`, `Dockerfile`, `pyproject.toml`, `/health`, un premier test. Le CI doit être vert avant d'aller plus loin.
2. Settings → Rules → Rulesets, sur `main` : exiger une PR, exiger les status checks `test` et `docker`, interdire les pushs directs, toi inclus. L'agent travaille sur des branches et ouvre des PR.

**Railway**

3. New Project → Deploy from GitHub repo → choisir le repo, branche `main`. Le premier déploiement échoue faute de variables : normal.
4. Variables : celles de la section 4, plus `DATA_DIR=/data`.
5. Volume : sur le service, Attach Volume, point de montage `/data`.
6. Settings → Networking → Generate Domain. Noter l'URL.
7. Settings → Deploy → Healthcheck Path `/health`, Restart Policy « On failure ».
8. Settings → Source → **Wait for CI** : activer. Le toggle n'apparaît que si le workflow avec `push: branches: [main]` existe déjà sur le repo.
9. `Cmd+K → Deploy latest commit`. Ouvrir `https://<domaine>/health`.

**Google Cloud**

10. Ajouter `https://<domaine>/auth/callback` aux URI de redirection du client OAuth (section 8, étape 6).

Ensuite, chaque merge sur `main` est un déploiement, sans rien toucher. Une variable à changer se modifie dans Railway, qui redéploie.

### Ce qu'on ne fait pas

- **Environnements de PR Railway** : le volume SQLite et les URI de redirection OAuth ne s'y prêtent pas. Une seule prod.
- **Déploiement depuis GitHub Actions** (`railway up` + token) : possible, à garder en réserve si « Wait for CI » pose problème. Dans ce cas, déconnecter le repo côté Railway pour n'avoir qu'une seule autorité.
- **Migrations de schéma** : `CREATE TABLE IF NOT EXISTS` au démarrage suffit pour deux tables. Si le schéma évolue, une table `schema_version` et des migrations appliquées au démarrage, pas de pre-deploy command.

---

## 14. Hors périmètre

- Plusieurs agendas de destination
- Déclenchement par email entrant
- Notifications externes (WhatsApp, Slack, SMS)
- Alerte admin en cas d'import raté (le journal suffit)
