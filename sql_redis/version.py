try:
    from importlib.metadata import PackageNotFoundError, version
except ImportError:
    # Python < 3.8 fallback
    from importlib_metadata import PackageNotFoundError, version  # type: ignore  # isort: skip

try:
    __version__ = version("sql-redis")
except PackageNotFoundError:
    # Package is not installed (e.g., during development)
    __version__ = "0.0.0.dev"
