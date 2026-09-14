# CLAUDE.md

## Contexte

Application décrite dans `docs/plan-v4.md`. Le plan fait autorité : en cas de doute, le relire plutôt qu'inventer. Suivre l'ordre des phases (section 11). La Phase 1 doit être mergée et déployée avant d'attaquer le reste.

## Façon de travailler

- Jamais de commit direct sur `main`. Une branche par lot de travail, une PR (`gh pr create`). Merge avec `gh pr merge --squash --delete-branch` seulement quand `gh pr checks` est vert.
- Avant chaque commit : `uv run ruff format . && uv run ruff check . && uv run pytest -q`.
- Petites PR, une par phase au plus. Dans la description : ce qui est testé, ce qui ne l'est pas, ce que l'humain doit faire ensuite.
- Si une décision du plan est impossible ou mauvaise, ne pas contourner en silence : l'écrire dans la PR et proposer une alternative.

## Interdits

- Aucun secret dans le repo, les tests, les logs ou les messages de commit. `.env` est ignoré par git. Les valeurs utilisées en test sont factices.
- Aucun vrai PDF de planning dans le repo. `tests/fixtures/private/` est ignoré par git ; les tests qui en dépendent sont `skip` si le dossier est vide. Ne pas lire ces fichiers sans demande explicite : ils contiennent des données de tiers.
- Aucun appel réseau dans les tests : Google et le modèle sont remplacés par des doubles.
- Pas de `railway.toml` ni `railway.json`. Pas de `concurrency: cancel-in-progress` dans le workflow CI.
- Ne jamais supprimer un événement Google Agenda qui ne porte pas le tag `app` (plan, section 6).

## Stack

Python 3.12, uv, FastAPI, Jinja2 + htmx, SQLite. Arborescence : section 9 du plan. Interface en français. Commentaires et identifiants : français ou anglais, mais cohérents dans un même fichier.

## Ce que tu ne peux pas faire

Railway, Google Cloud, Google Agenda : étapes manuelles de la section 13 du plan. Quand tu en as besoin (URL de l'app, variables, PDF de test, résultat d'un test de santé), arrête-toi et demande. Ne devine pas.
