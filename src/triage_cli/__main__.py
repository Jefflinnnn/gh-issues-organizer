"""Allows running the CLI with ``python -m triage_cli``."""

import sys

from triage_cli import cli

sys.exit(cli.main())
