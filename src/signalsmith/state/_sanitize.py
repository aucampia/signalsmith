import re

_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize(value: str) -> str:
    return _SANITIZE_RE.sub("_", value)
