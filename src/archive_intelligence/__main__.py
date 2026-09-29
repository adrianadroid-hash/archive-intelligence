"""Entry point for ``python -m archive_intelligence``."""

from archive_intelligence import __version__


def main() -> int:
    """Skeleton entry point; replaces itself as real functionality lands."""
    print(f"archive-intelligence {__version__} (skeleton)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
