"""Command-line entry point for the ``triage`` commands."""

import argparse
import datetime
import logging
import os
import pathlib
import shutil
import sqlite3
import sys
import textwrap
from collections.abc import Callable
from collections.abc import Sequence

from issue_store import github_api
from issue_store import repo_ref
from issue_store import store
from triage_cli import completion
from triage_cli import help_format
from triage_cli import timefmt
from triager import facts
from triager import loader
from triager import taxonomy

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = pathlib.Path(".cache")
DEFAULT_LIMIT = 10
DEFAULT_COMMENTS = 3
# Narrowest a wrapped column gets, however many columns come before it.
MIN_WRAP_WIDTH = 20

# Errors a user can fix; they get a one-line message instead of a traceback.
_USER_ERRORS = (
    repo_ref.InvalidRepoRefError,
    github_api.GitHubError,
    store.StoreError,
    sqlite3.DatabaseError,
)


class AlreadyTrackedError(RuntimeError):
    """Raised when ``add`` is given a repository that is already tracked."""


class NotTrackedError(RuntimeError):
    """Raised when a command needs a repository that isn't tracked."""


class ConfirmationRequiredError(RuntimeError):
    """Raised when a destructive command can't ask for confirmation."""


class SetupError(RuntimeError):
    """Raised when setup can't finish or a setup command is misused."""


class SyncError(RuntimeError):
    """Raised when ``sync`` couldn't update every repository it tried."""


_COMMAND_ERRORS = (
    *_USER_ERRORS,
    AlreadyTrackedError,
    NotTrackedError,
    ConfirmationRequiredError,
    SetupError,
    SyncError,
)


_MAIN_EPILOG = """\
New here? Run "triage setup" first.

Examples:
  triage add https://github.com/vorssaint/vorssaint-utils
  triage add vorssaint/vorssaint-utils --limit 25
  triage sync
  triage run vorssaint/vorssaint-utils
  triage list
  triage remove vorssaint/vorssaint-utils

Run triage from the project folder: it reads .env and .cache/ there.
Set up Tab completion with "triage completion --help".

Use "triage <command> --help" for more information about a command.
"""

_ADD_EPILOG = """\
Repository formats:
  https://github.com/vorssaint/vorssaint-utils
  https://github.com/vorssaint/vorssaint-utils/issues
  git@github.com:vorssaint/vorssaint-utils.git
  vorssaint/vorssaint-utils

Examples:
  triage add https://github.com/vorssaint/vorssaint-utils
  triage add vorssaint/vorssaint-utils --limit 25 --comments 5
"""

_SYNC_EPILOG = """\
What it fetches:
  Issues created, edited, commented on, closed or reopened since the newest
  change already stored. Run it as often as you like: nothing is fetched
  twice, and a run with no changes costs one request.

Examples:
  triage sync                           # every tracked repository
  triage sync vorssaint/vorssaint-utils
  triage sync --comments 5
"""

_HOT_RULE = (
    f"top {facts.HOT_FRACTION:.0%} of open issues by reactions plus comments, "
    f"minimum {facts.HOT_MIN_ENGAGEMENT}"
)

_RUN_EPILOG = f"""\
Flags:
  stale  no new issue or comment from a person in --stale-days days; bot
         comments and label edits don't count
  hot    {_HOT_RULE}

Tags from the model (bug, enhancement, ...) come in a later version.

Titles wrap to the terminal's width. Output piped to another program keeps
one line per issue.

Examples:
  triage run vorssaint/vorssaint-utils
  triage run vorssaint/vorssaint-utils --stale-days 90
  triage run vorssaint/vorssaint-utils --limit 20
"""

_LIST_EPILOG = """\
Examples:
  triage list
  triage list --names    # one repository per line, for scripts
"""

_REMOVE_EPILOG = """\
Examples:
  triage remove vorssaint/vorssaint-utils
  triage remove https://github.com/vorssaint/vorssaint-utils --yes
"""

_COMPLETION_EPILOG = """\
Setup:
  triage completion install adds these lines to ~/.zshrc (or ~/.bashrc),
  after asking:

    # triage: Tab completion
    eval "$(triage completion zsh)"

  Or add them yourself. Open a new terminal afterwards.

Press Tab after "triage " to complete a command, or after "triage remove ",
"triage sync " or "triage run " to complete a repository tracked in the
current folder.

Examples:
  triage completion install
  triage completion install bash --yes
  triage completion zsh    # print the script
"""

_SETUP_EPILOG = """\
Steps:
  1. GitHub token: checks .env has GITHUB_TOKEN and that GitHub accepts it.
  2. Tab completion: offers to add two lines to your shell's startup file.
     Say no and turn it on later with: triage completion install

Running setup again is safe: finished steps are skipped.

Examples:
  triage setup
  triage setup --yes    # turn on Tab completion without asking
"""


def main(argv: Sequence[str] | None = None) -> int:
    """Runs the CLI.

    Args:
        argv: Arguments without the program name; defaults to
            ``sys.argv[1:]``.

    Returns:
        The process exit code: 0 on success, 1 on a handled error, 2 when
        no command was given.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        args.handler(args)
    except _COMMAND_ERRORS as error:
        logger.error("%s", error)
        logger.debug("Details:", exc_info=True)
        return 1
    return 0


def _add_global_options(
    add_argument: Callable[..., argparse.Action], *, suppress_defaults: bool
) -> None:
    """Adds the options every command accepts.

    They're added to the top-level parser and again to each command, so
    ``triage -v add ...`` and ``triage add -v ...`` both work.

    Args:
        add_argument: The ``add_argument`` method of a parser or group.
        suppress_defaults: True for the copy on each command, so an option
            left out after the command doesn't overwrite the value given
            before it.
    """
    add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS if suppress_defaults else False,
        help="Show debug logs on stderr",
    )
    add_argument(
        "--data-dir",
        type=pathlib.Path,
        metavar="<path>",
        default=argparse.SUPPRESS if suppress_defaults else DEFAULT_DATA_DIR,
        help="Folder with one cache file per repo "
        f"(default: {DEFAULT_DATA_DIR})",
    )


def _add_command(
    commands: "argparse._SubParsersAction[help_format.Parser]",
    name: str,
    *,
    summary: str,
    usage: str,
    description: str,
    epilog: str,
) -> help_format.Parser:
    """Adds one command with the shared help layout and global options.

    Args:
        commands: The subparsers action of the top-level parser.
        name: The command name, e.g. ``"add"``.
        summary: One line shown in the top-level command list.
        usage: The usage line, e.g. ``"%(prog)s [options] <repo>"``.
        description: The paragraph at the top of the command's help.
        epilog: Text after the options, usually examples.

    Returns:
        The command's parser.
    """
    command = commands.add_parser(
        name,
        # Without prog, argparse prefixes the top-level usage line.
        prog=f"triage {name}",
        help=summary,
        usage=usage,
        description=description,
        epilog=epilog,
    )
    global_options = command.add_argument_group("Global Options")
    _add_global_options(global_options.add_argument, suppress_defaults=True)
    return command


def _add_startup_file_options(
    command: help_format.Parser, *, yes_help: str
) -> None:
    """Adds the options of commands that may edit a shell startup file.

    Args:
        command: The command's parser.
        yes_help: Help text for ``--yes`` on this command.
    """
    command.add_argument("-y", "--yes", action="store_true", help=yes_help)
    command.add_argument(
        "--startup-file",
        type=pathlib.Path,
        metavar="<path>",
        help="Startup file to edit (default: ~/.zshrc or ~/.bashrc)",
    )


def _build_parser() -> help_format.Parser:
    """Defines the commands and their flags."""
    parser = help_format.Parser(
        prog="triage",
        usage="%(prog)s [options] <command> [<args>]",
        description="Track GitHub repositories and store their open issues "
        "locally for triage.",
        epilog=_MAIN_EPILOG,
    )
    _add_global_options(parser.add_argument, suppress_defaults=False)
    commands = parser.add_subparsers(
        title="Commands", dest="command", metavar="<command>"
    )

    setup = _add_command(
        commands,
        "setup",
        summary="Check your GitHub token and turn on Tab completion",
        usage="%(prog)s [options]",
        description="Get ready to use triage: confirm your GitHub token "
        "works and offer Tab\ncompletion.",
        epilog=_SETUP_EPILOG,
    )
    _add_startup_file_options(
        setup, yes_help="Turn on Tab completion without asking"
    )
    setup.set_defaults(handler=_run_setup)

    add = _add_command(
        commands,
        "add",
        summary="Track a repository and fetch its open issues",
        usage="%(prog)s [options] <repo>",
        description="Track a repository: fetch its most recently updated "
        "open issues\ninto .cache/<owner>__<name>.db.",
        epilog=_ADD_EPILOG,
    )
    add.add_argument(
        "repo",
        metavar="<repo>",
        help="GitHub link or owner/name (formats below)",
    )
    add.add_argument(
        "--limit",
        type=int,
        metavar="<n>",
        default=DEFAULT_LIMIT,
        help="Open issues to fetch, 1-100 (default: %(default)s)",
    )
    add.add_argument(
        "--comments",
        type=int,
        metavar="<n>",
        default=DEFAULT_COMMENTS,
        help="Latest comments to keep per issue (default: %(default)s)",
    )
    add.set_defaults(handler=_run_add)

    sync = _add_command(
        commands,
        "sync",
        summary="Fetch issues changed since the last run",
        usage="%(prog)s [options] [<repo>]",
        description="Fetch the issues created, changed or closed since the "
        "last run and\nupdate the cache. Without <repo>, every tracked "
        "repository is synced.",
        epilog=_SYNC_EPILOG,
    )
    sync.add_argument(
        "repo",
        nargs="?",
        metavar="<repo>",
        help="GitHub link or owner/name (default: all tracked)",
    )
    sync.add_argument(
        "--comments",
        type=int,
        metavar="<n>",
        default=DEFAULT_COMMENTS,
        help="Latest comments to keep per changed issue (default: %(default)s)",
    )
    sync.set_defaults(handler=_run_sync)

    run = _add_command(
        commands,
        "run",
        summary="Flag a tracked repository's open issues",
        usage="%(prog)s [options] <repo>",
        description="Flag the stored open issues of a tracked repository "
        "and print them.\nRun triage sync first to work on fresh data.",
        epilog=_RUN_EPILOG,
    )
    run.add_argument("repo", metavar="<repo>", help="GitHub link or owner/name")
    run.add_argument(
        "--stale-days",
        type=_positive_int,
        metavar="<n>",
        default=facts.DEFAULT_STALE_DAYS,
        help="Days without human activity before an issue is stale "
        "(default: %(default)s)",
    )
    run.add_argument(
        "--limit",
        type=_positive_int,
        metavar="<n>",
        help="Show only the first <n> issues (default: all)",
    )
    run.set_defaults(handler=_run_triage)

    list_command = _add_command(
        commands,
        "list",
        summary="Show tracked repositories",
        usage="%(prog)s [options]",
        description="Show each tracked repository, how many issues are "
        "stored, and when it\nwas last synced.",
        epilog=_LIST_EPILOG,
    )
    list_command.add_argument(
        "--names",
        action="store_true",
        help="Print only repository names, one per line",
    )
    list_command.set_defaults(handler=_run_list)

    remove = _add_command(
        commands,
        "remove",
        summary="Stop tracking a repository and delete its data",
        usage="%(prog)s [options] <repo>",
        description="Stop tracking a repository and delete its cache file. "
        "To track it again,\nrun triage add.",
        epilog=_REMOVE_EPILOG,
    )
    remove.add_argument(
        "repo", metavar="<repo>", help="GitHub link or owner/name"
    )
    remove.add_argument(
        "-y", "--yes", action="store_true", help="Delete without asking"
    )
    remove.set_defaults(handler=_run_remove)

    completion_command = _add_command(
        commands,
        "completion",
        summary="Turn on Tab completion or print its script",
        usage="%(prog)s [options] <shell>\n"
        "  triage completion install [options] [<shell>]",
        description="Print the Tab completion script for your shell, or "
        "install it in your\nshell's startup file.",
        epilog=_COMPLETION_EPILOG,
    )
    completion_command.add_argument(
        "target",
        metavar="<shell>|install",
        choices=[*sorted(completion.SCRIPTS), "install"],
        help="bash or zsh to print the script; install to set it up",
    )
    completion_command.add_argument(
        "install_shell",
        nargs="?",
        choices=sorted(completion.SCRIPTS),
        help=argparse.SUPPRESS,
    )
    _add_startup_file_options(
        completion_command,
        yes_help="With install: add the lines without asking",
    )
    completion_command.set_defaults(handler=_run_completion)
    return parser


def _run_add(args: argparse.Namespace) -> None:
    """Tracks a new repository: fetches its issues into a new cache file."""
    requested = repo_ref.parse(args.repo)
    data_dir: pathlib.Path = args.data_dir
    _ensure_not_tracked(data_dir / requested.db_filename, requested.full_name)

    started_at = store.utc_now()
    token = github_api.load_token()
    fetch = github_api.fetch_open_issues(
        requested, limit=args.limit, comments=args.comments, token=token
    )

    # The API returns the canonical name, which differs from what the user
    # typed when the repository was renamed or transferred.
    canonical = repo_ref.parse(fetch.repository.name_with_owner)
    path = data_dir / canonical.db_filename
    _ensure_not_tracked(path, canonical.full_name)
    _write_new_cache(path, fetch, started_at)

    print(
        f"Tracking {fetch.repository.name_with_owner} · stored "
        f"{len(fetch.issues)} of {fetch.total_open} open issues · {path}"
    )


def _ensure_not_tracked(path: pathlib.Path, full_name: str) -> None:
    """Raises AlreadyTrackedError when the repo's cache file exists."""
    if path.exists():
        raise AlreadyTrackedError(f"{full_name} is already tracked ({path}).")


def _write_new_cache(
    path: pathlib.Path, fetch: github_api.FetchResult, started_at: str
) -> None:
    """Writes a fetch to a new cache file, leaving no file if it fails.

    The data goes to a temporary file that is renamed into place only after
    every row is written, so an interrupted add can't leave a half-filled
    cache that later looks tracked.
    """
    temporary = path.with_name(path.name + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        connection = store.connect(temporary)
        try:
            store.save_fetch(connection, fetch, "add", started_at)
        finally:
            connection.close()
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _run_sync(args: argparse.Namespace) -> None:
    """Updates one tracked repository, or all of them, with new changes."""
    data_dir: pathlib.Path = args.data_dir
    if args.repo is None:
        paths = sorted(data_dir.glob("*.db")) if data_dir.is_dir() else []
        if not paths:
            print("No tracked repositories. Add one with: triage add <link>")
            return
    else:
        requested = repo_ref.parse(args.repo)
        path = data_dir / requested.db_filename
        if not path.exists():
            raise NotTrackedError(
                f"{requested.full_name} is not tracked. Add it with: "
                f"triage add {requested.full_name}"
            )
        paths = [path]

    token = github_api.load_token()
    failed = 0
    for path in paths:
        try:
            print(_sync_one(path, args.comments, token))
        except _USER_ERRORS as error:
            # One broken repository shouldn't stop the others.
            logger.error("%s: %s", path.name, error)
            logger.debug("Details:", exc_info=True)
            failed += 1
    if failed:
        raise SyncError(f"{failed} of {len(paths)} repositories not synced.")


def _sync_one(path: pathlib.Path, comments: int, token: str) -> str:
    """Syncs one cache file and returns its one-line result."""
    started_at = store.utc_now()
    connection = store.connect(path)
    try:
        since = store.last_activity(connection)
        name = connection.execute(
            "SELECT name_with_owner FROM repository"
        ).fetchone()[0]
        fetch = github_api.fetch_changed_issues(
            repo_ref.parse(name), since, comments, token
        )
        changes = store.save_fetch(connection, fetch, "sync", started_at)
    finally:
        connection.close()
    return _describe_sync(fetch.repository.name_with_owner, changes, fetch)


def _describe_sync(
    name: str, changes: store.Changes, fetch: github_api.FetchResult
) -> str:
    """Formats a sync result, leaving out counts that are zero."""
    parts = [
        f"{count} {label}"
        for count, label in (
            (changes.created, "new"),
            (changes.closed, "closed"),
            (changes.reopened, "reopened"),
            (changes.updated, "updated"),
        )
        if count
    ]
    summary = " · ".join(parts) if parts else "no changes"
    return f"{name} · {summary} · {fetch.total_open} open on GitHub"


def _positive_int(text: str) -> int:
    """Parses a command-line number that must be 1 or more."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more, got {value}")
    return value


def _run_triage(args: argparse.Namespace) -> None:
    """Prints a tracked repository's open issues with their flags."""
    requested = repo_ref.parse(args.repo)
    data_dir: pathlib.Path = args.data_dir
    path = data_dir / requested.db_filename
    if not path.exists():
        raise NotTrackedError(
            f"{requested.full_name} is not tracked. Add it with: "
            f"triage add {requested.full_name}"
        )
    connection = store.connect(path)
    try:
        name = connection.execute(
            "SELECT name_with_owner FROM repository"
        ).fetchone()[0]
        issues = loader.load_open_issues(connection)
    finally:
        connection.close()

    if not issues:
        print(f"{name} · no open issues stored")
        return
    results = facts.compute(
        issues,
        now=datetime.datetime.now(datetime.timezone.utc),
        stale_days=args.stale_days,
    )
    counts = [
        f"{sum(flag in result.flags for result in results)} {flag.value}"
        for flag in (taxonomy.Flag.STALE, taxonomy.Flag.HOT)
    ]
    print(f"{name} · {len(results)} open issues · " + " · ".join(counts))
    shown = results[: args.limit]
    rows = [("ISSUE", "FLAGS", "IDLE", "ENGAGEMENT", "TITLE")]
    for result in shown:
        rows.append(
            (
                f"#{result.issue.number}",
                ", ".join(flag.value for flag in result.flags) or "-",
                f"{result.idle_days}d",
                str(result.engagement),
                result.issue.title,
            )
        )
    # Piped output stays one line per issue, so scripts can read it.
    width = shutil.get_terminal_size().columns if sys.stdout.isatty() else None
    _print_table(rows, wrap_width=width)
    if len(shown) < len(results):
        print(
            f"Showing {len(shown)} of {len(results)} issues; drop --limit "
            "to see them all."
        )


def _print_table(
    rows: Sequence[Sequence[str]], wrap_width: int | None = None
) -> None:
    """Prints rows as left-aligned columns, the first row as the header.

    Args:
        rows: The header row, then the data rows, all the same length.
        wrap_width: Line width to wrap the last column at, continuing it
            under itself; None prints each row on one line.
    """
    widths = [
        max(len(row[column]) for row in rows) for column in range(len(rows[0]))
    ]
    indent = sum(widths[:-1]) + 2 * (len(widths) - 1)
    for row in rows:
        prefix = "  ".join(
            cell.ljust(width)
            for cell, width in zip(row[:-1], widths[:-1], strict=True)
        )
        lines = [row[-1]]
        if wrap_width is not None:
            lines = textwrap.wrap(
                row[-1], max(wrap_width - indent, MIN_WRAP_WIDTH)
            ) or [""]
        print(f"{prefix}  {lines[0]}".rstrip())
        for line in lines[1:]:
            print(" " * indent + line)


def _run_list(args: argparse.Namespace) -> None:
    """Prints every tracked repository and when it was last synced."""
    data_dir: pathlib.Path = args.data_dir
    paths = sorted(data_dir.glob("*.db")) if data_dir.is_dir() else []
    if args.names:
        _print_names(paths)
        return
    if not paths:
        print("No tracked repositories. Add one with: triage add <github link>")
        return

    rows = [("REPO", "STORED", "OPEN ON GITHUB", "LAST SYNCED")]
    for path in paths:
        summary = store.summarize(path)
        rows.append(
            (
                summary.name_with_owner,
                str(summary.open_issues),
                str(summary.total_open),
                timefmt.local_display(summary.last_synced_at),
            )
        )
    _print_table(rows)


def _run_remove(args: argparse.Namespace) -> None:
    """Deletes a tracked repository's cache file after confirmation."""
    requested = repo_ref.parse(args.repo)
    data_dir: pathlib.Path = args.data_dir
    path = data_dir / requested.db_filename
    if not path.exists():
        raise NotTrackedError(
            f"{requested.full_name} is not tracked. See: triage list"
        )

    try:
        summary = store.summarize(path)
        name = summary.name_with_owner
        what = f"{name} and its {summary.open_issues} stored issues"
    except (store.StoreError, sqlite3.DatabaseError):
        # An unreadable file must still be removable.
        logger.debug("Couldn't read %s", path, exc_info=True)
        name = requested.full_name
        what = f"{name} (its cache file can't be read)"

    question = (
        f"Remove {what}? You'll need 'triage add' to track it again. [y/N] "
    )
    if not args.yes and not _confirm(question):
        print("Nothing removed.")
        return
    path.unlink()
    print(f"Removed {name} ({path})")


def _confirm(question: str) -> bool:
    """Asks a yes/no question on the terminal; anything but yes is no.

    Args:
        question: The prompt, shown on stderr so stdout stays the report.

    Returns:
        True if the user answered y or yes.

    Raises:
        ConfirmationRequiredError: stdin isn't a terminal, so nobody can
            answer.
    """
    if not sys.stdin.isatty():
        raise ConfirmationRequiredError(
            "Can't ask for confirmation without a terminal; rerun with --yes."
        )
    sys.stderr.write(question)
    sys.stderr.flush()
    return sys.stdin.readline().strip().lower() in ("y", "yes")


def _print_names(paths: list[pathlib.Path]) -> None:
    """Prints each tracked repository's name, skipping unreadable files.

    Shell completion calls this on every Tab, so one broken file shouldn't
    hide the other repositories.
    """
    for path in paths:
        try:
            print(store.summarize(path).name_with_owner)
        except (store.StoreError, sqlite3.DatabaseError):
            logger.warning("Skipping unreadable cache file %s", path)


def _run_completion(args: argparse.Namespace) -> None:
    """Prints a completion script, or installs one with ``install``."""
    if args.target != "install":
        if args.install_shell:
            raise SetupError(
                f"Unexpected argument {args.install_shell!r}. To set up "
                f"completion, run: triage completion install {args.target}"
            )
        print(completion.SCRIPTS[args.target], end="")
        return

    shell = args.install_shell or completion.detect_shell(os.environ)
    if shell is None:
        raise SetupError(_unknown_shell_message())
    _offer_completion(shell, _startup_file(args, shell), args.yes)


def _run_setup(args: argparse.Namespace) -> None:
    """Checks the GitHub token, then offers Tab completion."""
    token_ok = _check_token_step()

    shell = completion.detect_shell(os.environ)
    if shell is None:
        print(f"Tab completion: skipped. {_unknown_shell_message()}")
    else:
        try:
            _offer_completion(shell, _startup_file(args, shell), args.yes)
        except ConfirmationRequiredError:
            print(
                "Tab completion: skipped (no terminal to ask). Turn it on "
                "with: triage completion install --yes"
            )

    if not token_ok:
        raise SetupError(
            "Setup isn't finished: fix the GitHub token, then run "
            "triage setup again."
        )
    print("\nReady. Try: triage add https://github.com/<owner>/<repo>")


def _check_token_step() -> bool:
    """Prints whether a GitHub token is configured and accepted.

    Returns:
        True if GitHub accepted the token.
    """
    try:
        token = github_api.load_token()
    except github_api.GitHubError:
        print(
            "GitHub token: not found. Create a read-only token (README, "
            "setup step 2),\nsave it in .env as GITHUB_TOKEN=..., then run "
            "triage setup again."
        )
        return False
    try:
        status = github_api.check_token(token)
    except github_api.GitHubError as error:
        print(f"GitHub token: doesn't work. {error}")
        return False
    print(
        f"GitHub token: works for {status.login} ({status.remaining:,} of "
        f"{status.limit:,} requests left this hour)."
    )
    return True


def _offer_completion(shell: str, path: pathlib.Path, assume_yes: bool) -> None:
    """Adds the completion lines to a startup file after confirmation.

    Args:
        shell: ``"bash"`` or ``"zsh"``.
        path: The startup file to edit.
        assume_yes: Skip the question and add the lines.

    Raises:
        ConfirmationRequiredError: Confirmation is needed but there's no
            terminal to ask on.
    """
    shown = _display_path(path)
    if completion.is_installed(path, shell):
        print(f"Tab completion: already set up in {shown}.")
        return
    lines = "".join(
        f"  {line}\n" for line in completion.setup_lines(shell).splitlines()
    )
    question = (
        f"Turn on Tab completion for {shell}? This adds 2 lines to "
        f"{shown}:\n\n{lines}\n[y/N] "
    )
    if not assume_yes and not _confirm(question):
        print(
            "Tab completion: skipped. Turn it on any time with: "
            "triage completion install"
        )
        return
    completion.install(path, shell)
    print(f"Tab completion: added to {shown}. Open a new terminal to use it.")


def _unknown_shell_message() -> str:
    """Explains why the shell wasn't recognized and what to run instead."""
    name = pathlib.PurePath(os.environ.get("SHELL", "")).name
    reason = (
        f"Tab completion supports bash and zsh, not {name}."
        if name
        else "Couldn't tell which shell you use."
    )
    return f"{reason} For bash or zsh, run: triage completion install zsh"


def _startup_file(args: argparse.Namespace, shell: str) -> pathlib.Path:
    """Returns ``--startup-file`` or the shell's usual startup file."""
    if args.startup_file is not None:
        startup: pathlib.Path = args.startup_file
        return startup
    return completion.startup_file(shell, os.environ, pathlib.Path.home())


def _display_path(path: pathlib.Path) -> str:
    """Shows a path under the home folder as ``~/...``."""
    try:
        return "~/" + str(path.relative_to(pathlib.Path.home()))
    except ValueError:
        return str(path)
