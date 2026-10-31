# gh-issues-organizer

`triage` is a command-line tool for maintainers facing a long list of open
GitHub issues. This first version tracks a repository and stores its open
issues in a local SQLite file, then flags the ones that are stale or getting
attention. Tags from a language model come later.

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

### 4. Run setup

```bash
triage setup
```

Setup checks that GitHub accepts your token, then asks whether to turn on
Tab completion (it shows the two lines it would add to `~/.zshrc` or
`~/.bashrc`). Say no if you'd rather not; you can turn it on later. Running
setup again is safe: finished steps are skipped.

```text
GitHub token: works for your-login (4,999 of 5,000 requests left this hour).
Turn on Tab completion for zsh? This adds 2 lines to ~/.zshrc:

  # triage: Tab completion
  eval "$(triage completion zsh)"

[y/N] y
Tab completion: added to ~/.zshrc. Open a new terminal to use it.

Ready. Try: triage add https://github.com/<owner>/<repo>
```

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

Fetch what changed since you last looked: issues that were created, edited,
commented on, closed or reopened. With no argument it syncs every tracked
repository; give one to sync only it:

```bash
triage sync
triage sync vorssaint/vorssaint-utils
```

```
vorssaint/vorssaint-utils · 2 new · 1 closed · 4 updated · 889 open on GitHub
```

`sync` picks up where the stored issues leave off, so it is safe to run as
often as you like, and a run with no changes costs one request. Issues that
were already open when you ran `add` but are older than its `--limit` are not
back-filled.

Flag a tracked repository's stored open issues:

```bash
triage run vorssaint/vorssaint-utils
```

```
vorssaint/vorssaint-utils · 3 open issues · 0 stale · 1 hot
ISSUE  FLAGS  IDLE  ENGAGEMENT  TITLE
#3267  hot    1d    5           Network module: add connectivity history and
                                interface status (like Stats)
#3668  -      1d    1           Dynamic Island Hermes Agent
#3678  -      1d    0           Add Cursor to AI agents view
```

`run` works on the cache, so run `triage sync` first for fresh data. It
computes two flags each time:

- **stale**: no new issue or comment from a person in 60 days. Bot comments
  and label edits don't reset the clock. Change the window with
  `--stale-days`:

  ```bash
  triage run vorssaint/vorssaint-utils --stale-days 90
  ```
- **hot**: in the top 10% of the repository's open issues by reactions plus
  comments, with at least 3.

Long titles wrap to your terminal's width; output piped to another program
keeps one line per issue. To see only the first few issues:

```bash
triage run vorssaint/vorssaint-utils --limit 20
```

`IDLE` is days since the last human activity; `ENGAGEMENT` is reactions plus
comments. The cache keeps only each issue's latest comments, so if those are
all from bots, the issue counts as idle since it was opened.

Show tracked repositories:

```bash
triage list
```

Times show in your computer's timezone (for example `2026-10-29 22:18 PDT`).
The cache stores them in UTC, like GitHub does; set `TZ` to view another
zone, e.g. `TZ=UTC triage list`.

Stop tracking a repository and delete its cache file. `triage` asks for
confirmation first; `--yes` skips the question. To track it again, run
`triage add`.

```bash
triage remove vorssaint/vorssaint-utils
```

Run `triage --help` for the command list, and `triage <command> --help`
(or `-h`) for a command's arguments, options and examples. Shared options
such as `-v` work before or after the command (`triage -v add ...` or
`triage add -v ...`).

Options:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--limit N` (add) | 10 | Open issues to fetch, 1 to 100 |
| `--comments N` (add, sync) | 3 | Latest comments kept per fetched issue |
| `--stale-days N` (run) | 60 | Days without human activity before an issue is stale |
| `--limit N` (run) | all | Issues to show in the report |
| `--yes` (remove) | off | Delete without asking |
| `--data-dir PATH` | `.cache` | Folder holding the cache files |
| `-v` | off | Show debug logs |

Each tracked repository gets one SQLite file in `.cache/`, for example
`.cache/vorssaint__vorssaint-utils.db`. The tool creates the file and its
tables on first use, and upgrades files written by an older version in place;
nothing needs setting up. Git ignores `.cache/`.

## Tab completion

`triage setup` offers Tab completion. If you skipped it, turn it on any
time; it asks before editing your startup file:

```bash
triage completion install
```

Or add the line to `~/.zshrc` (or `~/.bashrc` with `bash`) yourself:

```bash
eval "$(triage completion zsh)"
```

Completion works in zsh and bash; open a new terminal after setting it up.
Press Tab after `triage ` to complete a command, or after `triage remove `,
`triage sync ` or `triage run ` to complete a tracked repository's name.
Press Tab again to cycle through the matches; in bash, cycling needs
`bind 'TAB:menu-complete'`. Completion lists the repositories tracked in the
current folder, the same ones `triage list --names` prints.

## Troubleshooting

| Message | Fix |
| --- | --- |
| `No GitHub token found` | Create `.env` as in setup step 3, then run `triage setup`. |
| `GitHub rejected the token (HTTP 401)` | The token is mistyped or expired; generate a new one. |
| `Repository ... not found` | Check the spelling. Private repos need a token with access to them. |
| `... is already tracked` | The repository already has a cache file; see `triage list`. |
| `... was written by a newer version of triage` | Update the tool: run `git pull` in the project folder. |

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy .
uv run pytest
```

Tests run against a GraphQL response recorded in
`test/fixtures/issues_response.json`, so they need no network or token.
