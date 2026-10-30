"""Allows running the CLI with ``python -m gh_triage``."""

import sys

from gh_triage import cli

sys.exit(cli.main())
