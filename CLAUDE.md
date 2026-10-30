# CLAUDE.md

Project rules for Claude Code. Personal, machine-specific notes go in
`CLAUDE.local.md` (gitignored).

## Git workflow

- **Never commit or push directly to `main`.** `main` is protected and only
  changes through reviewed pull requests.
- For every change:
  1. Branch from an up-to-date `main`: `git switch main && git pull && git switch -c <type>/<short-description>`
     (e.g. `feat/fetch-issues`, `fix/rate-limit-retry`, `docs/readme-setup`).
  2. Make small, focused commits on the branch.
  3. Run the local checks (see below) before pushing.
  4. Push the branch and open a PR against `main` with `gh pr create`.
     The PR description says what changed, why, and how it was tested.
  5. **Do not merge PRs.** The human reviews and merges after CI passes.
- One logical change per PR. Keep PRs small enough to review in one sitting.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):
`type(scope): short imperative summary`

- Types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `build`, `ci`
- Summary under ~72 characters; use the body to explain *why*.
- PR titles follow the same format.

## Checks

CI (`.github/workflows/ci.yml`) runs on every PR and must pass before merge:
secret scanning, Ruff lint + format check, and — once `pyproject.toml`
exists — mypy and pytest. Run the same checks locally before pushing.
