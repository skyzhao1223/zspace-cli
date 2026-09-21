<!-- Thanks for contributing! Please fill in what applies. -->

## What does this PR do?

<!-- One or two lines: the problem or idea, and your approach. Link the issue it fixes. -->

Fixes #

## Checklist (from CONTRIBUTING.md)

- [ ] `ruff check src tests skills` clean (ruff ≥ 0.11)
- [ ] `pytest -q` green (coverage ≥ 75%; new behavior has new tests)
- [ ] `python -m pyright` clean (if `src/` changed)
- [ ] `skills/*/tests/smoke.sh` pass (if skills changed)
- [ ] `skills/` ↔ `src/zspace_cli/skills/` dual copies in sync (if skills changed)
- [ ] Docs updated if user-facing: `README.md` + `docs/README.zh.md` (+ `skills/README.md`)
- [ ] Tested against a real NAS if the change touches network behavior (describe below)

## Notes for reviewers

<!-- Anything tricky, NAS/client versions involved, screenshots for CLI output changes. -->
