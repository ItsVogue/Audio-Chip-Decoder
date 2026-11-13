"""`python -m audiochip` starts the desktop app; with arguments it runs the command line."""
import sys

if len(sys.argv) > 1 and sys.argv[1] in ("analyze", "convert", "codecs", "profiles", "-h", "--help"):
    from .cli import main
    sys.exit(main())
from .gui.app import run

run()
