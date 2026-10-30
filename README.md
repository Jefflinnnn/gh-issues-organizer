# gh-issues-organizer

`triage` is a command-line tool for maintainers facing a long list of open
GitHub issues. This first version tracks a repository and stores its open
issues in a local SQLite file. Tagging and the triage report come later.

## Requirements

- Python 3.10 or newer
- [uv](https://docs.astral.sh/uv/)
- A GitHub account (for a read-only token)

## Setup

### 1. Install

```bash
git clone https://github.com/Jefflinnnn/gh-issues-organizer.git
cd gh-issues-organizer
uv tool install --editable .
```

This puts a `triage` command on your PATH. `--editable` runs the code in
this folder, so a `git pull` takes effect without reinstalling. If your
shell can't find `triage`, run `uv tool update-shell` and open a new
terminal.

### 2. Create a read-only GitHub token

The tool uses GitHub's GraphQL API, which needs a token. Give it the least
access possible: read-only access to public repositories, nothing else.

1. On github.com, open **Settings → Developer settings → Personal access
   tokens → Fine-grained tokens → Generate new token**.
2. Name it (for example `triage`) and pick a short expiration, such as 30
   days.
3. Under **Repository access**, choose **Public repositories (read-only)**.
4. Leave every permission off. Reading public issues needs none.
5. Click **Generate token** and copy it. GitHub shows it only once.

Avoid reusing `gh auth token`: it usually carries far more access than this
tool needs.

### 3. Save the token in `.env`

```bash
cp .env.example .env
```

Open `.env` in an editor and paste the token after `GITHUB_TOKEN=`. Use an
editor rather than `echo`, so the token doesn't land in your shell history.
Git ignores `.env`, so the token never gets committed. A `GITHUB_TOKEN`
environment variable, if set, takes priority over the file.

## Usage

Run `triage` from the project folder. It reads `.env` and keeps its data
in `.cache/` relative to the folder you run it in, so from another folder
it won't find your token or your tracked repositories.

Track a repository and fetch its 10 most recently updated open issues:

```bash
triage add https://github.com/vorssaint/vorssaint-utils
```

`add` accepts a GitHub link (with or without `/issues` or `.git`), an SSH
remote such as `git@github.com:owner/name.git`, or `owner/name`.

Show tracked repositories:

```bash
triage list
```

Run `triage -h`, `triage add -h` or `triage list -h` for options and
examples. Shared options such as `-v` work before or after the command
(`triage -v add ...` or `triage add -v ...`).

Options:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--limit N` (add) | 10 | Open issues to fetch, 1 to 100 |
| `--comments N` (add) | 3 | Latest comments kept per issue |
| `--data-dir PATH` | `.cache` | Folder holding the cache files |
| `-v` | off | Show debug logs |

Each tracked repository gets one SQLite file in `.cache/`, for example
`.cache/vorssaint__vorssaint-utils.db`. The tool creates the file and its
tables on first use; nothing needs setting up. Git ignores `.cache/`.

## Troubleshooting

| Message | Fix |
| --- | --- |
| `No GitHub token found` | Create `.env` as in setup step 3. |
| `GitHub rejected the token (HTTP 401)` | The token is mistyped or expired; generate a new one. |
| `Repository ... not found` | Check the spelling. Private repos need a token with access to them. |
| `... is already tracked` | The repository already has a cache file; see `triage list`. |

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy .
uv run pytest
```

Tests run against a GraphQL response recorded in
`test/fixtures/issues_response.json`, so they need no network or token.
