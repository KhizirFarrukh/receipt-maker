"""Stage 2 — template_engine unit tests.

Covers the cases PLAN-generalization.md §"Testing & gates" enumerates for the
engine: escaping, {{#if}} true/false/nested, |raw, dotted keys, repetition by
joining, malformed source -> TemplateError with file+line, unknown placeholder
rejected at compile time, a known-but-absent value rendering blank, and an
api-version mismatch warning.

Run: python -m unittest discover -s tests
"""
import os
import sys
import unittest
import warnings

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import template_engine as te  # noqa: E402


class Escaping(unittest.TestCase):
    def test_text_is_escaped(self):
        got = te.render_string("<p>{{name}}</p>", {"name": '<script>&"x"'})
        self.assertEqual(got, "<p>&lt;script&gt;&amp;&quot;x&quot;</p>")

    def test_apostrophe_escaped_for_attribute_safety(self):
        self.assertEqual(te.render_string("{{v}}", {"v": "O'Neill"}), "O&#39;Neill")

    def test_ampersand_escaped_once(self):
        self.assertEqual(te.render_string("{{v}}", {"v": "a & b"}), "a &amp; b")

    def test_literal_text_is_never_escaped(self):
        # Template authors write real HTML; only interpolated values are escaped.
        self.assertEqual(te.render_string("<b>What's &amp; up</b>"), "<b>What's &amp; up</b>")

    def test_raw_filter_bypasses_escaping(self):
        got = te.render_string("{{frag|raw}}", {"frag": "<td>1</td>"})
        self.assertEqual(got, "<td>1</td>")


class Conditionals(unittest.TestCase):
    def test_if_true(self):
        self.assertEqual(te.render_string("{{#if p}}Phone: {{p}}{{/if}}", {"p": "123"}),
                         "Phone: 123")

    def test_if_false_emits_nothing(self):
        self.assertEqual(te.render_string("{{#if p}}Phone: {{p}}{{/if}}", {"p": ""}), "")

    def test_if_absent_emits_nothing(self):
        self.assertEqual(te.render_string("{{#if p}}x{{/if}}", {}), "")

    def test_zero_is_falsey_but_nonempty_string_is_truthy(self):
        self.assertEqual(te.render_string("{{#if n}}y{{/if}}", {"n": 0}), "")
        self.assertEqual(te.render_string("{{#if n}}y{{/if}}", {"n": "0.00"}), "y")

    def test_nested_ifs(self):
        tpl = "{{#if a}}A{{#if b}}B{{/if}}{{/if}}"
        self.assertEqual(te.render_string(tpl, {"a": 1, "b": 1}), "AB")
        self.assertEqual(te.render_string(tpl, {"a": 1, "b": 0}), "A")
        self.assertEqual(te.render_string(tpl, {"a": 0, "b": 1}), "")

    def test_endif_alias(self):
        self.assertEqual(te.render_string("{{#if a}}x{{#endif}}", {"a": 1}), "x")


class DottedKeys(unittest.TestCase):
    def test_nested_lookup(self):
        got = te.render_string("{{item.sku}}", {"item": {"sku": "KB-87"}})
        self.assertEqual(got, "KB-87")

    def test_missing_branch_renders_blank(self):
        self.assertEqual(te.render_string("[{{item.sku}}]", {"item": {}}), "[]")
        self.assertEqual(te.render_string("[{{item.sku}}]", {}), "[]")

    def test_non_mapping_midway_renders_blank(self):
        self.assertEqual(te.render_string("[{{a.b}}]", {"a": "scalar"}), "[]")

    def test_if_on_dotted_key(self):
        tpl = "{{#if item.serial}}SN {{item.serial}}{{/if}}"
        self.assertEqual(te.render_string(tpl, {"item": {"serial": "X1"}}), "SN X1")
        self.assertEqual(te.render_string(tpl, {"item": {"serial": ""}}), "")


class Repetition(unittest.TestCase):
    """The engine has no loops; rows are rendered N times in Python and joined."""

    def test_row_template_joined(self):
        row = te.compile_template("<tr><td>{{sku}}</td><td>{{qty}}</td></tr>")
        items = [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 2}]
        html = "".join(row.render(i) for i in items)
        self.assertEqual(html, "<tr><td>A</td><td>1</td></tr><tr><td>B</td><td>2</td></tr>")

    def test_compiled_template_is_reusable(self):
        t = te.compile_template("{{v}}")
        self.assertEqual([t.render({"v": 1}), t.render({"v": 2})], ["1", "2"])


class Malformed(unittest.TestCase):
    def _err(self, source, **kw):
        with self.assertRaises(te.TemplateError) as ctx:
            te.compile_template(source, name="base.html", **kw)
        return ctx.exception

    def test_unclosed_if_reports_opening_line(self):
        err = self._err("line1\nline2\n{{#if a}}\nbody\n")
        self.assertEqual(err.filename, "base.html")
        self.assertEqual(err.line, 3, "should point at the {{#if}} that was left open")
        self.assertIn("never closed", err.message)

    def test_stray_endif(self):
        err = self._err("a\n{{/if}}")
        self.assertEqual(err.line, 2)
        self.assertIn("without a matching", err.message)

    def test_unknown_block_tag(self):
        err = self._err("{{#unless a}}x{{/if}}")
        self.assertIn("unknown block tag", err.message)

    def test_unknown_filter(self):
        err = self._err("{{a|upper}}")
        self.assertIn("unknown filter", err.message)
        self.assertIn("raw", err.message)

    def test_empty_tag(self):
        self.assertIn("empty tag", self._err("{{}}").message)

    def test_invalid_key_characters(self):
        self.assertIn("not a valid placeholder", self._err("{{a-b}}").message)

    def test_error_message_includes_file_and_line(self):
        err = self._err("x\n\n{{a|nope}}")
        self.assertIn("base.html:3", str(err))


class Linting(unittest.TestCase):
    ALLOWED = {"company_name", "invoice_no", "item"}

    def test_unknown_placeholder_rejected_at_compile_time(self):
        with self.assertRaises(te.TemplateError) as ctx:
            te.compile_template("{{compnay_name}}", name="header.html", allowed=self.ALLOWED)
        self.assertIn("unknown placeholder", ctx.exception.message)

    def test_typo_gets_a_suggestion(self):
        with self.assertRaises(te.TemplateError) as ctx:
            te.compile_template("{{company_nam}}", name="header.html", allowed=self.ALLOWED)
        self.assertIn("did you mean", ctx.exception.message)

    def test_allowed_keys_pass(self):
        t = te.compile_template("{{company_name}}{{invoice_no}}",
                                name="header.html", allowed=self.ALLOWED)
        self.assertEqual(t.render({"company_name": "X", "invoice_no": "1"}), "X1")

    def test_dotted_key_allowed_via_its_root(self):
        t = te.compile_template("{{item.sku}}", allowed=self.ALLOWED)
        self.assertEqual(t.render({"item": {"sku": "S"}}), "S")

    def test_if_key_is_linted_too(self):
        with self.assertRaises(te.TemplateError):
            te.compile_template("{{#if nope}}x{{/if}}", allowed=self.ALLOWED)

    def test_keys_property_reports_every_reference(self):
        t = te.compile_template("{{a}}{{#if b}}{{c.d}}{{/if}}")
        self.assertEqual(t.keys, {"a", "b", "c.d"})


class Comments(unittest.TestCase):
    def test_comment_emits_nothing(self):
        self.assertEqual(te.render_string("a{{! not rendered }}b"), "ab")

    def test_declared_api_version_is_recorded(self):
        t = te.compile_template("{{! template_api_version: 1 }}x")
        self.assertEqual(t.api_version, 1)
        self.assertEqual(t.render(), "x")

    def test_version_mismatch_warns(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            te.compile_template("{{! template_api_version: 99 }}x", name="terms.html")
        self.assertTrue(any(w.category is te.TemplateVersionWarning for w in caught),
                        "no TemplateVersionWarning raised")
        self.assertIn("terms.html", str(caught[-1].message))


class Robustness(unittest.TestCase):
    def test_stray_braces_in_prose_are_literal(self):
        # A lone "{{" with no closing pair must not swallow the document.
        self.assertEqual(te.render_string("cost {{ of x"), "cost {{ of x")

    def test_css_braces_survive(self):
        css = "body { margin: 0; } .a { color: red; }"
        self.assertEqual(te.render_string(css), css)

    def test_none_renders_blank(self):
        self.assertEqual(te.render_string("[{{v}}]", {"v": None}), "[]")

    def test_load_template_missing_file(self):
        with self.assertRaises(te.TemplateError) as ctx:
            te.load_template(os.path.join(PROJ, "does_not_exist.html"))
        self.assertIn("could not be read", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
