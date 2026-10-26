"""Apply configured runtime environment before importing compiled dependencies.

Example: python -m decompcond.launch experiment --config configs/defaults.yaml
Network proxy and credential environment variables are inherited, never logged.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from .config import load_config


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", required=True)
    parser.add_argument("--set", action="append", default=[], dest="overrides")
    options, _ = parser.parse_known_args(arguments)
    config = load_config(options.config, options.overrides)
    result = subprocess.run(
        [sys.executable, "-m", "decompcond.cli", *arguments],
        env={**os.environ, **config.remote.environment},
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
