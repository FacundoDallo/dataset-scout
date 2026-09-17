"""Allows `python -m dataset_scout ...` as an alternative to the `scout` command."""

from .cli import main

raise SystemExit(main())
