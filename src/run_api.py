#!/usr/bin/env python
"""
Launch the Quality Checker API server.

Usage::

    python run_api.py                  # default: http://localhost:8000
    python run_api.py --port 3001      # custom port
    python run_api.py --reload         # dev mode with auto-reload
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ensure src/ is on the path
_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def main() -> None:
    p = argparse.ArgumentParser(description="OECD Quality Checker API server")
    p.add_argument("--host", default="localhost", help="Bind host (default: localhost)")
    p.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    p.add_argument("--reload", action="store_true", help="Enable auto-reload (dev mode)")
    args = p.parse_args()

    import uvicorn

    uvicorn.run(
        "api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
