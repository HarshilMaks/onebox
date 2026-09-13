#!/usr/bin/env python3
"""Generate the committed OpenAPI contract from the live FastAPI application."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.main import app  # noqa: E402

DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "openapi.json"


def render_openapi() -> str:
    """Return deterministic OpenAPI JSON for the currently mounted application."""
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="OpenAPI JSON artifact path")
    parser.add_argument("--check", action="store_true", help="Fail instead of writing when the artifact is stale")
    arguments = parser.parse_args()

    output = arguments.output if arguments.output.is_absolute() else PROJECT_ROOT / arguments.output
    rendered = render_openapi()
    try:
        current = output.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = None

    if arguments.check:
        if current != rendered:
            raise SystemExit(f"OpenAPI artifact is stale: regenerate with {Path(__file__).relative_to(PROJECT_ROOT)}")
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
