"""raw ARIA snapshot text  ->  parse  ->  driver-neutral SurfaceElement[]

Pure Python: imports nothing from Playwright. Written against snapshots captured from the real
Legacy Bank app in Chromium (tests/surface/fixtures/*.aria.txt). The AI-mode format is one node
per line, two-space indentation for nesting::

    - role "accessible name" [attr] [attr=value] [ref=f1e30]: trailing text
    - text: free text node
    - /url: property of the parent node

Attributes are anything in square brackets: ``[ref=…]``, ``[level=1]``, ``[selected]``,
``[disabled]``, ``[active]``, ``[cursor=pointer]``. Trailing text after the closing ``:`` is the
node's own text (a textbox's value, an alert's message, a caption).

Entry point: ``read_snapshot(text) -> (elements, outline)``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from cua.domain import SurfaceElement

_LINE = re.compile(r"^(?P<indent>\s*)- (?P<body>.*)$")
_ROLE = re.compile(r"^(?P<role>[a-z]+)")
_NAME = re.compile(r'^\s*"(?P<name>(?:[^"\\]|\\.)*)"')
# Playwright emits trailing text (a textbox value, an alert message, a free text node) as a
# YAML scalar and quotes it only when YAML would otherwise misread it — e.g. a number-like
# value such as ``"500.00"``. The quotes are serialisation, not page content.
_QUOTED_SCALAR = re.compile(r'^"(?P<text>(?:[^"\\]|\\.)*)"$')
_ATTR = re.compile(r"^\s*\[(?P<key>[a-z]+)(?:=(?P<value>[^\]]*))?\]")

# Nodes whose semantics are carried by the elements they contain (or by context_hint / the text
# outline) rather than by themselves. Kept in the tree for context, not emitted as elements.
STRUCTURAL_ROLES = frozenset(
    {
        "generic",
        "rowgroup",
        "paragraph",
        "main",
        "banner",
        "contentinfo",
        "navigation",
        "table",
        "row",
        "group",
        "caption",
    }
)

# Roles whose accessible name *is* their content; ``value`` mirrors it so a READ target is
# expressed the same way whether the content came from a name or from trailing text. Public:
# discovery drops the name from a durable target of these roles (the name is the value read).
CONTENT_ROLES = frozenset({"cell", "rowheader", "columnheader"})

_OUTLINE_ROLES = frozenset({"heading", "caption", "alert", "status", "paragraph", "banner"})


# --- stage 1: raw text -> tree ---------------------------------------------------------------


@dataclass
class AriaNode:
    role: str
    name: str = ""
    ref: str | None = None
    attrs: dict[str, str | None] = field(default_factory=dict)
    text: str | None = None
    children: list[AriaNode] = field(default_factory=list)
    parent: AriaNode | None = field(default=None, repr=False)

    @property
    def is_text(self) -> bool:
        return self.role == "text"

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


def parse_ai_snapshot(text: str) -> list[AriaNode]:
    """Parse AI-mode aria snapshot text into a forest of ``AriaNode``s."""
    roots: list[AriaNode] = []
    stack: list[tuple[int, AriaNode]] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        match = _LINE.match(raw)
        if match is None:
            raise ValueError(f"unrecognised aria snapshot line: {raw!r}")
        indent = len(match.group("indent"))
        body = match.group("body")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else None

        if body.startswith("/"):
            # Property line, e.g. "/url: /members/search" — belongs to the parent node.
            key, _, value = body[1:].partition(":")
            if parent is not None:
                parent.attrs[key.strip()] = value.strip()
            continue

        node = _parse_node(body)
        node.parent = parent
        if parent is None:
            roots.append(node)
        else:
            parent.children.append(node)
        stack.append((indent, node))
    return roots


def _unquote_scalar(text: str) -> str:
    """A fully quoted YAML scalar becomes its content; anything else is returned verbatim."""
    match = _QUOTED_SCALAR.match(text)
    if match is None:
        return text
    return match.group("text").replace('\\"', '"').replace("\\\\", "\\")


def _parse_node(body: str) -> AriaNode:
    if body.startswith("text:"):
        return AriaNode(role="text", text=_unquote_scalar(body[len("text:") :].strip()))
    role_match = _ROLE.match(body)
    if role_match is None:
        raise ValueError(f"unrecognised aria node: {body!r}")
    node = AriaNode(role=role_match.group("role"))
    rest = body[role_match.end() :]
    name_match = _NAME.match(rest)
    if name_match:
        node.name = name_match.group("name").replace('\\"', '"')
        rest = rest[name_match.end() :]
    while True:
        attr_match = _ATTR.match(rest)
        if attr_match is None:
            break
        node.attrs[attr_match.group("key")] = attr_match.group("value")
        rest = rest[attr_match.end() :]
    rest = rest.strip()
    if rest.startswith(":"):
        trailing = _unquote_scalar(rest[1:].strip())
        if trailing:
            node.text = trailing
    elif rest:
        raise ValueError(f"unparsed remainder {rest!r} in aria node {body!r}")
    node.ref = node.attrs.get("ref")
    return node


# --- stage 2: tree -> driver-neutral elements ------------------------------------------------


def context_hint_for(node: AriaNode) -> str | None:
    """Nearest named ancestors, outermost first, ``" > "``-joined, at most three segments.

    A ``row`` is labelled by its ``rowheader`` child (Chromium leaves ``<tr>`` unnamed); a
    ``table`` by its ``caption`` child when it has no name of its own.
    """
    segments: list[str] = []
    ancestor = node.parent
    while ancestor is not None and len(segments) < 3:
        label = _context_label(ancestor)
        if label:
            segments.append(label)
        ancestor = ancestor.parent
    return " > ".join(reversed(segments)) or None


def _context_label(node: AriaNode) -> str | None:
    if node.role == "row":
        header = next((c.name for c in node.children if c.role == "rowheader" and c.name), None)
        name = header or node.name
        return f"row: {name}" if name else None
    if node.role == "table":
        caption = next((c.text for c in node.children if c.role == "caption" and c.text), None)
        name = node.name or caption
        return f"table: {name}" if name else None
    if node.role in {"group", "form", "dialog", "region", "navigation"} and node.name:
        return f"{node.role}: {node.name}"
    return None


def value_for(node: AriaNode) -> str | None:
    if node.role == "combobox":
        selected = next(
            (c.name for c in node.children if c.role == "option" and "selected" in c.attrs), None
        )
        return selected if selected is not None else node.text
    if node.text is not None:
        return node.text
    if node.role in CONTENT_ROLES:
        return node.name
    return None


def build_elements(roots: list[AriaNode]) -> list[SurfaceElement]:
    """Every ref-bearing, non-structural node, in tree order. Nothing is deduplicated."""
    elements: list[SurfaceElement] = []
    for root in roots:
        for node in root.walk():
            if node.ref is None or node.is_text or node.role in STRUCTURAL_ROLES:
                continue
            elements.append(
                SurfaceElement(
                    ref=node.ref,
                    role=node.role,
                    accessible_name=node.name,
                    value=value_for(node),
                    enabled="disabled" not in node.attrs,
                    tag_hint=None,
                    context_hint=context_hint_for(node),
                )
            )
    return elements


def build_outline(roots: list[AriaNode], limit: int = 2000) -> str:
    """Readable text in tree order: headings, captions, alerts, paragraphs, free text."""
    lines: list[str] = []
    for root in roots:
        for node in root.walk():
            line = None
            if node.is_text and node.text:
                line = node.text
            elif node.role == "heading":
                level = node.attrs.get("level") or "?"
                line = f"h{level}: {node.name}"
            elif node.role in _OUTLINE_ROLES and (node.text or node.name):
                line = f"{node.role}: {node.text or node.name}"
            if line:
                lines.append(line)
    return "\n".join(lines)[:limit]


# --- entry point -----------------------------------------------------------------------------


def read_snapshot(text: str) -> tuple[list[SurfaceElement], str]:
    """raw ARIA snapshot text -> parse -> (SurfaceElement[], visible_text_outline)."""
    roots = parse_ai_snapshot(text)
    return build_elements(roots), build_outline(roots)
