"""Entry point of the packaged Windows build (see safekeep.spec)."""

import sys

from safekeep.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
