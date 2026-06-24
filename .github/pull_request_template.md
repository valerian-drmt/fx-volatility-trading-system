## Summary

<!-- 1-2 sentences on the purpose of this PR -->

## Linked issue

Closes #<N>
<!-- REQUIRED: closes the issue + moves the board card to ✅ Done on merge. -->

## Release

- **Milestone**: R<X>
- **Target tag**: `vX.Y.Z`

## Changes

- <item 1>
- <item 2>

## Tests performed

- [ ] `python -m pytest -m "not integration"` OK
- [ ] `python -m ruff check src tests` OK
- [ ] `python -m compileall -q src` OK
- [ ] `PYTHONPATH=src lint-imports` OK (architecture contracts)
- [ ] Manual test: <scenario>
- [ ] (if frontend) `npm run test` + `npm run test:e2e` OK

## Feature flag

- [ ] No flag (complete feature)
- [ ] Behind `FF_<NAME>` (disabled by default)

## Screenshots (if UI)

<!-- drag & drop -->

## Merge checklist

- [ ] `Closes #N` present (closes the issue + card → ✅ Done)
- [ ] CI green
- [ ] Branch up to date with `main` (rebase if needed)
- [ ] Conventional Commits respected
- [ ] No `Co-Authored-By` / bot name in commit messages
- [ ] No secret exposed (issue, PR, commit, log)