"""Source-cell presence without changing character-exact dataset values."""


def is_missing_value(value: str | None) -> bool:
    """Whitespace-only cells are missing data; preserve the original text."""
    return value is None or not value.strip()
