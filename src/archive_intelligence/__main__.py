"""Entry point for ``python -m archive_intelligence``."""

from archive_intelligence import __version__


def main() -> int:
    """Print the package version; the v0.1 pipeline runs via its engine
    modules (see README.md)."""
    print(f"archive-intelligence {__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
