#!/usr/bin/env python3
"""One entry point for local Ubuntu and remote SSH Joi installation."""
import sys

if sys.version_info < (3, 11):
    raise SystemExit("The Joi installer requires Python 3.11 or newer (Ubuntu target: 3.12).")

from installer.install import main

if __name__ == "__main__":
    main()
