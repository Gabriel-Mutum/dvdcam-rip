import os
import sys

from .cli import main

code = main()
# Started from dvdcam-rip.bat (a window that closes when we exit): after an
# error, wait so the message can be read.
if code not in (0, 130) and os.environ.get("DVDCAM_RIP_LAUNCHER") == "bat" and sys.stdin.isatty():
    try:
        input("\nPress Enter to close this window.")
    except (EOFError, KeyboardInterrupt):
        pass
raise SystemExit(code)
