"""A deliberately small, safe placeholder engine for receipt templates.

Three features, and no more (PLAN-generalization.md, governing principle 1 --
"the engine stays dumb; the renderer precomputes"):

    {{key}}              value, HTML-escaped
    {{key|raw}}          value inserted verbatim -- engine-produced fragments ONLY
    {{#if key}}...{{/if}}  emitted only when key is truthy (nestable)

Dotted keys (``{{item.sku}}``) resolve through nested mappings. There are no
loops, no expressions, no filters beyond ``raw``, and no inheritance: repetition
is done in Python by rendering a small row template N times and joining. If a
template ever needs a decision, receipt_render computes a boolean or a
pre-formatted string and passes it in.

Failures are loud and early. ``compile_template`` parses and lints the whole
source up front, so a malformed tag or a typo'd placeholder raises
``TemplateError`` with the file and line at load time -- never as a silently
blank field on a legal document halfway through a render.
"""
import os
import re
import warnings

#: Bumped when the template syntax changes in a way that can break existing
#: user-edited files. Templates declare theirs with ``{{! template_api_version: N }}``.
TEMPLATE_API_VERSION = 1

# A tag body never spans lines and never contains braces, which keeps a stray
# "{{" in prose from swallowing the rest of the document.
_TAG_RE = re.compile(r"\{\{([^{}\n]*)\}\}")
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_VERSION_RE = re.compile(r"^template_api_version\s*:\s*(\d+)$")

# Node kinds
_TEXT, _VAR, _IF = "text", "var", "if"


class TemplateError(Exception):
    """A template could not be compiled, or referenced a key it may not use."""

    def __init__(self, message, filename=None, line=None):
        self.message = message
        self.filename = filename
        self.line = line
        where = filename or ""
        if line is not None:
            where = f"{where}:{line}" if where else f"line {line}"
        super().__init__(f"{where}: {message}" if where else message)


class TemplateVersionWarning(UserWarning):
    """A template declares an api version this engine does not implement."""


def escape(value):
    """Escape a value for an HTML text node or a quoted attribute."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _line_of(source, index):
    return source.count("\n", 0, index) + 1


class Template:
    """A compiled template. Immutable and safe to reuse across renders."""

    def __init__(self, nodes, name="<string>", api_version=TEMPLATE_API_VERSION):
        self._nodes = nodes
        self.name = name
        self.api_version = api_version

    @property
    def keys(self):
        """Every key the template references, dotted form, in no order.

        Used by the "shipped defaults reference only declared keys" test.
        """
        found = set()

        def walk(nodes):
            for node in nodes:
                if node[0] == _VAR:
                    found.add(node[1])
                elif node[0] == _IF:
                    found.add(node[1])
                    walk(node[2])

        walk(self._nodes)
        return found

    def render(self, context=None):
        context = context or {}
        out = []
        self._render_nodes(self._nodes, context, out)
        return "".join(out)

    def _render_nodes(self, nodes, context, out):
        for node in nodes:
            kind = node[0]
            if kind == _TEXT:
                out.append(node[1])
            elif kind == _VAR:
                value = _resolve(context, node[1])
                if value is None:
                    continue          # a known-but-absent value renders empty
                out.append(str(value) if node[2] else escape(value))
            else:  # _IF
                if _truthy(_resolve(context, node[1])):
                    self._render_nodes(node[2], context, out)

    def __repr__(self):
        return f"<Template {self.name!r} keys={sorted(self.keys)}>"


def _resolve(context, dotted):
    """Look a dotted key up through nested mappings. Missing -> None."""
    current = context
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _truthy(value):
    """Emptiness, not identity: '', '0'-free semantics stay intuitive for editors."""
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return bool(value)


def compile_template(source, name="<string>", allowed=None):
    """Parse and lint source into a Template, or raise TemplateError.

    ``allowed``, when given, is the set of keys this block may reference; any
    other key is a load-time error rather than a silently blank field. Keys are
    checked by their root segment too, so ``{{item.sku}}`` is accepted when the
    block publishes ``item``.
    """
    root = []
    stack = [(None, root, None)]   # (if-key, node list, opening line)
    api_version = TEMPLATE_API_VERSION
    pos = 0

    for match in _TAG_RE.finditer(source):
        if match.start() > pos:
            stack[-1][1].append((_TEXT, source[pos:match.start()]))
        pos = match.end()

        body = match.group(1).strip()
        line = _line_of(source, match.start())

        if not body:
            raise TemplateError("empty tag '{{}}'", name, line)

        if body.startswith("!"):
            directive = body[1:].strip()
            version_match = _VERSION_RE.match(directive)
            if version_match:
                api_version = int(version_match.group(1))
            continue                                   # comments emit nothing

        if body.startswith("#if "):
            key = body[4:].strip()
            _check_key(key, name, line, allowed)
            block = []
            stack[-1][1].append((_IF, key, block))
            stack.append((key, block, line))
            continue

        if body in ("/if", "#endif"):
            if len(stack) == 1:
                raise TemplateError("{{/if}} without a matching {{#if}}", name, line)
            stack.pop()
            continue

        if body.startswith("#"):
            raise TemplateError(
                f"unknown block tag '{{{{{body}}}}}' -- the engine has only "
                f"{{{{#if key}}}}...{{{{/if}}}}", name, line)

        key, _, filter_name = body.partition("|")
        key, filter_name = key.strip(), filter_name.strip()
        if filter_name and filter_name != "raw":
            raise TemplateError(
                f"unknown filter '|{filter_name}' -- the only filter is '|raw'", name, line)
        _check_key(key, name, line, allowed)
        stack[-1][1].append((_VAR, key, filter_name == "raw"))

    if pos < len(source):
        stack[-1][1].append((_TEXT, source[pos:]))

    if len(stack) > 1:
        unclosed_key, _, opened_at = stack[-1]
        raise TemplateError(
            f"{{{{#if {unclosed_key}}}}} opened here is never closed with {{{{/if}}}}",
            name, opened_at)

    if api_version != TEMPLATE_API_VERSION:
        warnings.warn(
            f"{name} declares template_api_version {api_version}, but this build "
            f"implements {TEMPLATE_API_VERSION}. Re-check the template against the "
            f"current documentation.",
            TemplateVersionWarning, stacklevel=2,
        )

    return Template(root, name=name, api_version=api_version)


def _check_key(key, name, line, allowed):
    if not _KEY_RE.match(key):
        raise TemplateError(
            f"'{key}' is not a valid placeholder name (letters, digits, "
            f"underscores, dots)", name, line)
    if allowed is None:
        return
    # Accept 'item.sku' when the block publishes either the exact dotted key or
    # its root object.
    if key in allowed or key.split(".", 1)[0] in allowed:
        return
    close = _did_you_mean(key, allowed)
    raise TemplateError(
        f"unknown placeholder '{{{{{key}}}}}' for this template" + (close or "")
        + f". Available: {', '.join(sorted(allowed)) or '(none)'}", name, line)


def _did_you_mean(key, allowed):
    import difflib
    matches = difflib.get_close_matches(key, sorted(allowed), n=1, cutoff=0.7)
    return f" -- did you mean '{{{{{matches[0]}}}}}'?" if matches else ""


def load_template(path, allowed=None):
    """Compile the template at path. Missing file -> TemplateError."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
    except OSError as exc:
        raise TemplateError(f"could not be read: {exc}", os.path.basename(path)) from exc
    return compile_template(source, name=os.path.basename(path), allowed=allowed)


def render_string(source, context=None, name="<string>", allowed=None):
    """Compile and render in one call. Convenience for tests and small fragments."""
    return compile_template(source, name=name, allowed=allowed).render(context)
