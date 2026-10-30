"""Help output in the style of git, gh (Cobra) and clap-based tools.

argparse's default help puts usage first, lowercases section titles and
nests subcommands under a placeholder. This module reorders it to the
convention most CLIs share:

    <description>

    Usage:
      triage [options] <command> [<args>]

    Commands:
      add          Track a repository and fetch its open issues

    Options:
      -h, --help   Show this help and exit
          --flag   Long-only flags line up under long names

    <epilog: examples and a "Use ... --help" footer>
"""

import argparse
from typing import Any
from typing import NoReturn

# Space before a long-only flag so it lines up with "--long" in "-s, --long".
_LONG_ONLY_INDENT = "    "


class HelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Lists commands flat and writes each flag's value placeholder once."""

    def __init__(self, prog: str) -> None:
        """Initializes the formatter with a wider name column.

        Args:
            prog: The program name argparse shows in usage.
        """
        super().__init__(prog, max_help_position=32, width=80)

    def _format_action(self, action: argparse.Action) -> str:
        # Commands sit directly under "Commands:", not under a placeholder.
        if isinstance(action, argparse._SubParsersAction):
            return "".join(
                self._format_action(choice)
                for choice in action._get_subactions()
            )
        return super()._format_action(action)

    def _format_action_invocation(self, action: argparse.Action) -> str:
        if not action.option_strings:
            return super()._format_action_invocation(action)
        # "-l, --limit <n>" rather than argparse's "-l N, --limit N".
        invocation = ", ".join(action.option_strings)
        if action.nargs != 0:
            default_metavar = self._get_default_metavar_for_optional(action)
            invocation += " " + self._format_args(action, default_metavar)
        has_short_flag = any(
            not option.startswith("--") for option in action.option_strings
        )
        return invocation if has_short_flag else _LONG_ONLY_INDENT + invocation


class Parser(argparse.ArgumentParser):
    """An ArgumentParser with Git/Cobra-style help and error output."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initializes the parser with capitalized sections and -h/--help.

        Args:
            *args: Passed to argparse.ArgumentParser.
            **kwargs: Passed to argparse.ArgumentParser; ``formatter_class``
                and ``add_help`` default to this module's style.
        """
        kwargs.setdefault("formatter_class", HelpFormatter)
        kwargs["add_help"] = False
        super().__init__(*args, **kwargs)
        self._positionals.title = "Arguments"
        self._optionals.title = "Options"
        self.add_argument(
            "-h",
            "--help",
            action="help",
            default=argparse.SUPPRESS,
            help="Show this help and exit",
        )

    def format_usage(self) -> str:
        """Returns the one-line usage shown with errors.

        Returns:
            ``Usage: <usage>`` with a trailing newline.
        """
        formatter = self._get_formatter()
        formatter.add_usage(
            self.usage,
            self._actions,
            self._mutually_exclusive_groups,
            prefix="Usage: ",
        )
        return formatter.format_help()

    def format_help(self) -> str:
        """Returns the full help: description, usage, sections, epilog.

        Returns:
            The help text.
        """
        formatter = self._get_formatter()
        formatter.add_text(self.description)
        formatter.start_section("Usage")
        formatter.add_usage(
            self.usage,
            self._actions,
            self._mutually_exclusive_groups,
            prefix="  ",
        )
        formatter.end_section()
        # Commands come before options, as in git and Cobra.
        groups = sorted(
            self._action_groups,
            key=lambda group: (
                not any(
                    isinstance(action, argparse._SubParsersAction)
                    for action in group._group_actions
                )
            ),
        )
        for group in groups:
            formatter.start_section(group.title)
            formatter.add_text(group.description)
            formatter.add_arguments(group._group_actions)
            formatter.end_section()
        formatter.add_text(self.epilog)
        return formatter.format_help()

    def error(self, message: str) -> NoReturn:
        """Prints the error, the usage line and where to get help.

        Args:
            message: argparse's description of what was wrong.
        """
        self.exit(
            2,
            f"Error: {message}\n{self.format_usage()}"
            f"Run '{self.prog} --help' for more information.\n",
        )
