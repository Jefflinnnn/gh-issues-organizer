"""Command-line entry point: ``triage add`` and ``triage list``."""

import argparse
import logging
import pathlib
import sqlite3
import sys
from collections.abc import Sequence
from typing import NoReturn

from gh_triage import github_api
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


class _Parser(argparse.ArgumentParser):
    """An ArgumentParser whose usage errors point to the examples."""

    def error(self, message: str) -> NoReturn:
        """Prints the usage line, the error and where to find examples.

        Args:
            message: argparse's description of what was wrong.
        """
        self.print_usage(sys.stderr)
        self.exit(
            2,
            f"{self.prog}: error: {message}\n"
            f"Run '{self.prog} -h' for examples.\n",
        )


class AlreadyTrackedError(RuntimeError):
    """Raised when ``add`` is given a repository that is already tracked."""


_MAIN_EXAMPLES = """\
examples:
  triage add https://github.com/vorssaint/vorssaint-utils
  triage add vorssaint/vorssaint-utils --limit 25
  triage list
  triage add -v vorssaint/vorssaint-utils    (show debug logs)

Run triage from the project folder: it reads .env and .cache/ there.
Run 'triage COMMAND -h' for a command's options.
"""

_ADD_EXAMPLES = """\
REPO can be any of:
  https://github.com/vorssaint/vorssaint-utils
  https://github.com/vorssaint/vorssaint-utils/issues
  git@github.com:vorssaint/vorssaint-utils.git
  vorssaint/vorssaint-utils

examples:
  triage add https://github.com/vorssaint/vorssaint-utils
  triage add vorssaint/vorssaint-utils --limit 25 --comments 5
"""

_LIST_EXAMPLES = """\
example:
  triage list
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
    except (*_USER_ERRORS, AlreadyTrackedError) as error:
        logger.error("%s", error)
        logger.debug("Details:", exc_info=True)
        return 1
    return 0


def _shared_options(*, suppress_defaults: bool) -> _Parser:
    """Returns a parent parser with the options every command accepts.

    The options are added both before and after the command name, so
    ``triage -v add ...`` and ``triage add -v ...`` both work.

    Args:
        suppress_defaults: True for the copy attached to each command, so
            an option left out after the command doesn't overwrite the
            value given before it.

    Returns:
        A parser to pass as ``parents=[...]``.
    """
    shared = _Parser(add_help=False)
    group = shared.add_argument_group("shared options")
    group.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS if suppress_defaults else False,
        help="show debug logs on stderr",
    )
    group.add_argument(
        "--data-dir",
        type=pathlib.Path,
        metavar="PATH",
        default=argparse.SUPPRESS if suppress_defaults else DEFAULT_DATA_DIR,
        help="folder with one cache file per repo "
        f"(default: {DEFAULT_DATA_DIR})",
    )
    return shared


def _build_parser() -> argparse.ArgumentParser:
    """Defines the commands and their flags."""
    formatter = argparse.RawDescriptionHelpFormatter
    command_options = _shared_options(suppress_defaults=True)
    parser = _Parser(
        prog="triage",
        description="Track GitHub repositories and store their open issues "
        "locally for triage.",
        epilog=_MAIN_EXAMPLES,
        formatter_class=formatter,
        parents=[_shared_options(suppress_defaults=False)],
    )
    commands = parser.add_subparsers(
        title="commands", dest="command", metavar="COMMAND"
    )

    add = commands.add_parser(
        "add",
        help="track a repository and fetch its open issues",
        description="Track a repository: fetch its most recently updated "
        "open issues into .cache/<owner>__<name>.db.",
        epilog=_ADD_EXAMPLES,
        formatter_class=formatter,
        parents=[command_options],
    )
    add.add_argument(
        "repo",
        metavar="REPO",
        help="GitHub link or owner/name (forms listed below)",
    )
    add.add_argument(
        "--limit",
        type=int,
        metavar="N",
        default=DEFAULT_LIMIT,
        help="open issues to fetch, 1-100 (default: %(default)s)",
    )
    add.add_argument(
        "--comments",
        type=int,
        metavar="N",
        default=DEFAULT_COMMENTS,
        help="latest comments to keep per issue (default: %(default)s)",
    )
    add.set_defaults(handler=_run_add)

    list_command = commands.add_parser(
        "list",
        help="show tracked repositories",
        description="Show each tracked repository, how many issues are "
        "stored, and when it was last synced.",
        epilog=_LIST_EXAMPLES,
        formatter_class=formatter,
        parents=[command_options],
    )
    list_command.set_defaults(handler=_run_list)
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
