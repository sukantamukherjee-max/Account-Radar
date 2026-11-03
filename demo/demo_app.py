"""Account Radar -- Live Demo Mode entry point (thin launcher only).

Started by run_demo.bat:

    py -m streamlit run demo\\demo_app.py --server.port 8502

This file contains NO business logic. It runs the one Account Radar app
(app.main, via app.run_demo) with the live-demo presentation layer switched on (see
ui/demo_mode.py). Research is always live: nothing is loaded from disk and
no earlier result is replayed.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app  # noqa: E402  (the single Account Radar application)

app.run_demo()
