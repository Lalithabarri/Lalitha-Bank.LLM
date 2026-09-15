"""Route-pattern matching for allowlists and risk rules.

A pattern is a URL path whose segments are either literal or ``{name}``; ``{name}`` matches
exactly one non-empty segment. Matching is whole-path: ``/members/{id}`` matches
``/members/M1001`` and not ``/members/M1001/transfer``. No regular expressions are accepted
from configuration.
"""

from urllib.parse import urlsplit


def split_origin_and_path(url: str) -> tuple[str, str]:
    """Split ``http://host:port/path?q`` into (``http://host:port``, ``/path``)."""
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    return origin, parts.path or "/"


def _segments(path: str) -> list[str]:
    return [segment for segment in path.strip("/").split("/") if segment != ""]


def route_matches(pattern: str, path: str) -> bool:
    pattern_segments = _segments(pattern)
    path_segments = _segments(path)
    if len(pattern_segments) != len(path_segments):
        return False
    for expected, actual in zip(pattern_segments, path_segments, strict=True):
        if expected.startswith("{") and expected.endswith("}") and len(expected) > 2:
            continue
        if expected != actual:
            return False
    return True


def any_route_matches(patterns: tuple[str, ...] | list[str], path: str) -> bool:
    return any(route_matches(pattern, path) for pattern in patterns)
