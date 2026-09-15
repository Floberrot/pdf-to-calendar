# CLAUDE.md

## Contexte

Application d'import d'un planning PDF vers un agenda Google partagé — description fonctionnelle dans README.md. Les 6 phases initiales du développement sont terminées ; le travail se fait maintenant au fil des retours d'usage.

## Façon de travailler

- Jamais de commit direct sur `main`. Une branche par lot de travail, une PR (`gh pr create`). Merge avec `gh pr merge --squash --delete-branch` seulement quand `gh pr checks` est vert.
- Nom de branche en lien avec la feature/issue/bugfix, conventions GitHub : `feature/...`, `fix/...`, `chore/...`, `docs/...` (ex. `feature/phase1-socle`, `fix/pytest-pythonpath`).
- Commits au format [Conventional Commits](https://www.conventionalcommits.org/) : `feat: ...`, `fix: ...`, `chore: ...`, `docs: ...`, `refactor: ...`, `test: ...`, `ci: ...`. Un commit = un changement cohérent.
- Avant chaque commit : `uv run ruff format . && uv run ruff check . && uv run pytest -q`.
- Petites PR, un sujet à la fois. Dans la description : ce qui est testé, ce qui ne l'est pas, ce que l'humain doit faire ensuite.
- Si une consigne (CLAUDE.md, retour utilisateur) est impossible ou mauvaise, ne pas contourner en silence : l'écrire dans la PR et proposer une alternative.

## Interdits

- Aucun secret dans le repo, les tests, les logs ou les messages de commit. `.env` est ignoré par git. Les valeurs utilisées en test sont factices.
- Aucun vrai PDF de planning dans le repo. `tests/fixtures/private/` est ignoré par git ; les tests qui en dépendent sont `skip` si le dossier est vide. Ne pas lire ces fichiers sans demande explicite : ils contiennent des données de tiers.
- Aucun appel réseau dans les tests : Google et le modèle sont remplacés par des doubles.
- Pas de `railway.toml` ni `railway.json`. Pas de `concurrency: cancel-in-progress` dans le workflow CI.
- Ne jamais supprimer un événement Google Agenda qui ne porte pas le tag `app`.

## Stack

Python 3.12, uv, FastAPI, Jinja2 + htmx, SQLite (arborescence résumée dans le README, section Structure). Interface en français. Commentaires et identifiants : français ou anglais, mais cohérents dans un même fichier.

## Ce que tu ne peux pas faire

Railway, Google Cloud, Google Agenda : étapes manuelles (détail dans le README, section « Étapes manuelles »). Quand tu en as besoin (URL de l'app, variables, PDF de test, résultat d'un test de santé), arrête-toi et demande. Ne devine pas.
