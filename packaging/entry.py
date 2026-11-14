"""Entry point of the packaged Windows build (see tuckvault.spec)."""

import sys

from tuckvault.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
