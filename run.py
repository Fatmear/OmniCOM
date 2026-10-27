"""OmniCOM launcher.

    python run.py            native window
    python run.py --demo     native window with a virtual DEMO serial port
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    from omnicom.ui import main
    raise SystemExit(main())
