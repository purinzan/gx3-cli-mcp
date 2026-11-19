"""Discoverable SVG-only entry point for the existing ladder layout exporter."""
from __future__ import annotations

from gx3cli.gx3_ladder_layout import main as layout_main


def main(argv: list[str] | None = None) -> int:
    return layout_main(argv, svg_only=True)


if __name__ == "__main__":
    raise SystemExit(main())
