# CLAUDE.md

Project rules for Claude Code. Personal, machine-specific notes go in
`CLAUDE.local.md` (gitignored).

## Code style

House style: **Python 3.10+** and the
[Google Python Style Guide](https://google.github.io/styleguide/pyguide.html).

- **Target Python 3.10.** Use 3.10 features freely (`X | None` unions,
  built-in generics like `list[str]`, `match`). Do not use 3.11+ only APIs
  (`tomllib`, `typing.Self`, `StrEnum`, `ExceptionGroup`) without a fallback.
- **Docstrings:** Google style, with `Args:`, `Returns:`, `Raises:`
  sections, on every public module, class, and function.
- **Type annotations** on all public functions and methods.
- **Line length:** 80 characters.
- **Imports:** import packages and modules, not individual classes or
  functions (`from typing` is the usual exception). One import per line,
  grouped stdlib / third-party / local.
- **Naming:** `snake_case` functions and variables, `PascalCase` classes,
  `UPPER_SNAKE_CASE` constants, `_leading_underscore` for internals.
- **No** mutable default arguments, bare `except:`, or wildcard imports.
- **Readable code over comments.** Prefer clear, descriptive names and
  straightforward logic so the code explains itself. Don't comment what the
  code already says. Add comments only to explain *why* (a non-obvious
  decision, constraint, or workaround) or to walk through genuinely complex
  logic. Docstrings are still required as above.
- Ruff (with the `google` pydocstyle convention) and mypy enforce this once
  `pyproject.toml` exists; when the guide and the tools disagree, follow
  the guide and adjust the tool config.

## Logging

- Use the standard `logging` module with one logger per module:
  `logger = logging.getLogger(__name__)`. No `print()` for diagnostics.
- Per the Google guide, pass a **string literal with `%` placeholders** and
  the values as arguments, never an f-string:
  `logger.info("Fetched %d issues from %s", count, repo)`.
- **Never log secrets**: no API keys, tokens, or auth headers, at any level.
- Logs go to **stderr**; the tool's report goes to **stdout**.
- Inside `except` blocks, use `logger.exception(...)` to keep the traceback.
  Fail loudly; don't swallow errors.

## Git workflow

- **Never commit or push directly to `main`.** `main` is protected and only
  changes through reviewed pull requests.
- For every change:
  1. Branch from an up-to-date `main`: `git switch main && git pull && git switch -c <type>/<short-description>`
     (e.g. `feat/fetch-issues`, `fix/rate-limit-retry`, `docs/readme-setup`).
  2. Make small, focused commits on the branch.
  3. Run the local checks (see below) before pushing.
  4. Push the branch and open a PR against `main`. The PR body **must**
     follow `.github/pull_request_template.md`: copy it, fill in every
     section (delete options that don't apply), and pass it with
     `gh pr create --base main --title "<conventional title>" --body-file <file>`.
     `--fill` skips the template, so don't use it. In "AI assistance", list
     what you did and leave "Changed or overrode" and "Verified by hand"
     for the human to fill in unless they've told you.
  5. **Do not merge PRs.** The human reviews and merges after CI passes.
  6. After the human merges, clean up locally. GitHub deletes the remote
     branch automatically ("Automatically delete head branches" is on).
     Confirm the PR is merged with `gh pr view <branch> --json state`, then:
     `git switch main && git pull --prune && git branch -D <branch>`.
     (`-D` is needed because squash merges rewrite commits; only use it
     after confirming the PR state is `MERGED`.)
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

## Tests

- Tests live in `test/`, named `test_*.py`, and run with pytest.
- `test/test_repo_guardrails.py` checks the repo's own guardrails
  (ignored secrets, Claude deny rules, CLAUDE.md sections, CI triggers).
  Update it when you intentionally change those files; never weaken a test
  just to make it pass.
- New code ships with tests in the same PR. Test behavior, not
  implementation details, and don't mock the thing under test.
- Run before pushing: `uvx --with pyyaml pytest test`
  (becomes `uv run pytest` once `pyproject.toml` exists).
