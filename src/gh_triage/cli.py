"""Command-line entry point for the ``triage`` commands."""

import argparse
import logging
import pathlib
import sqlite3
import sys
from collections.abc import Callable
from collections.abc import Sequence

from gh_triage import completion
from gh_triage import github_api
from gh_triage import help_format
from gh_triage import repo_ref
from gh_triage import store

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = pathlib.Path(".cache")
DEFAULT_LIMIT = 10
DEFAULT_COMMENTS = 3

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


_COMMAND_ERRORS = (
    *_USER_ERRORS,
    AlreadyTrackedError,
    NotTrackedError,
    ConfirmationRequiredError,
)


_MAIN_EPILOG = """\
Examples:
  triage add https://github.com/vorssaint/vorssaint-utils
  triage add vorssaint/vorssaint-utils --limit 25
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
  Add one line to your shell's startup file, then open a new terminal.

  zsh (~/.zshrc):
    eval "$(triage completion zsh)"

  bash (~/.bashrc):
    eval "$(triage completion bash)"

Press Tab after "triage " to complete a command, or after "triage remove "
to complete a repository tracked in the current folder.
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
        summary="Print a shell completion script",
        usage="%(prog)s [options] <shell>",
        description="Print a script that adds Tab completion to your shell.",
        epilog=_COMPLETION_EPILOG,
    )
    completion_command.add_argument(
        "shell",
        metavar="<shell>",
        choices=sorted(completion.SCRIPTS),
        help="bash or zsh",
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

    rows = [("REPO", "STORED", "OPEN ON GITHUB", "LAST SYNCED (UTC)")]
    for path in paths:
        summary = store.summarize(path)
        rows.append(
            (
                summary.name_with_owner,
                str(summary.open_issues),
                str(summary.total_open),
                summary.last_synced_at.replace("+00:00", ""),
            )
        )
    widths = [max(len(row[column]) for row in rows) for column in range(4)]
    for row in rows:
        print(
            "  ".join(
                cell.ljust(width)
                for cell, width in zip(row, widths, strict=True)
            )
        )


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
    """Prints the completion script for the requested shell."""
    print(completion.SCRIPTS[args.shell], end="")
