## Résumé

<!-- 1-2 phrases sur le but du PR -->

## Release concernée

- **Milestone** : R<X> — <titre>
- **Spec** : `releases/r<X>-<slug>.md`
- **Tag cible** : `vX.Y.Z`

## Changements

- <item 1>
- <item 2>

## Tests effectués

- [ ] `python -m pytest -m "not integration"` OK
- [ ] `python -m ruff check src tests app.py` OK
- [ ] `python -m compileall -q src app.py` OK
- [ ] Tests manuels : <scénario>
- [ ] (si frontend) `npm run test` + `npm run test:e2e` OK

## Feature flag

- [ ] Pas de flag (feature complète)
- [ ] Caché derrière `FF_<NOM>` (désactivé par défaut)

## Screenshots (si UI)

<!-- drag & drop -->

## Checklist de merge

- [ ] CI verte
- [ ] Branche à jour avec `main` (rebase si nécessaire)
- [ ] Conventional Commits respectés
- [ ] Aucun `Co-Authored-By` dans les messages de commit
