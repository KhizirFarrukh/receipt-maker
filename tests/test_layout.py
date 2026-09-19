"""The package layout itself: launchers, layering, and no stale flat imports.

The modules used to sit loose at the project root, so `import config` worked
from anywhere. They now live in `receiptmaker/`, and a great many of the imports
that reach them are written *inside functions* -- lazily, to keep tkinter out of
the render path and to break import cycles. Those lines do not run until
somebody opens the right dialog, which makes a missed one exactly the kind of
mistake a green suite hides until a user finds it.

So the first test here does not import anything. It parses every module in the
package and asserts that no import statement still names a module by its old
flat name, whether that statement runs on import or three menus deep.

The rest pin the things the move could quietly have broken:

* the four launchers at the root still resolve to the code they delegate to;
* `ui` is still a leaf -- nothing below it imports tkinter, which is what lets
  the CLI and the golden gate run on a machine with no display.

Run: python -m unittest discover -s tests
"""
import ast
import os
import subprocess
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

PACKAGE = os.path.join(PROJ, "receiptmaker")

#: What each module was called before it was moved into a subpackage. An import
#: naming any of these at top level is a leftover: it would either fail outright
#: or, worse, pick up a same-named file that happens to be on sys.path.
FORMER_TOP_LEVEL = frozenset({
    "config", "money", "template_engine",
    "line_amounts", "line_units", "installments", "shipments", "payment_methods",
    "product_catalogue", "receipt_history", "invoice_counter", "drafts", "csv_io",
    "receipt_render", "receipt_signing", "receipt_service",
    "main", "settings_ui",
    "cli", "keygen", "verify_receipt",
})

#: Every launcher at the root, and the module it exists to start.
LAUNCHERS = {
    "main.py": ("receiptmaker.ui.main_window", "launch"),
    "cli.py": ("receiptmaker.tools.cli", "main"),
    "keygen.py": ("receiptmaker.tools.keygen", "main"),
    "verify_receipt.py": ("receiptmaker.tools.verify_receipt", "main"),
}


def python_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def imported_names(path):
    """Every module name this file imports, wherever the statement sits.

    Yields (module, lineno). A relative import is skipped: it is by definition
    already package-relative and cannot name an old top-level module.
    """
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                yield node.module, node.lineno


def stale_imports(root):
    """Every import under `root` that still names a pre-move module."""
    found = []
    for path in python_files(root):
        for module, lineno in imported_names(path):
            if module.split(".")[0] in FORMER_TOP_LEVEL:
                found.append("%s:%d imports %s"
                             % (os.path.relpath(path, PROJ), lineno, module))
    return found


class NoStaleFlatImports(unittest.TestCase):
    """No module still imports another by the name it had at the root."""

    def test_the_package_never_names_a_former_top_level_module(self):
        stale = stale_imports(PACKAGE)
        self.assertEqual([], stale,
                         "these still use the pre-move module names:\n  "
                         + "\n  ".join(stale))

    def test_the_tests_never_name_a_former_top_level_module(self):
        """A stale test import passes locally only for as long as a stray copy
        of the old module is still lying about in the working tree."""
        stale = stale_imports(os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual([], stale,
                         "these still use the pre-move module names:\n  "
                         + "\n  ".join(stale))

    def test_every_package_module_parses(self):
        """A blunt guard: a file mangled by a bulk rewrite stops being Python."""
        found = list(python_files(PACKAGE))
        self.assertGreater(len(found), 20, "the package looks half-moved")
        for path in found:
            with open(path, "r", encoding="utf-8") as f:
                try:
                    ast.parse(f.read(), filename=path)
                except SyntaxError as exc:  # pragma: no cover - failure detail
                    self.fail("%s does not parse: %s"
                              % (os.path.relpath(path, PROJ), exc))


class LaunchersStillResolve(unittest.TestCase):
    """`python main.py` and friends are what the README tells people to run."""

    def test_each_launcher_delegates_to_a_module_that_exists(self):
        for script, (target, function) in sorted(LAUNCHERS.items()):
            with self.subTest(script=script):
                self.assertTrue(os.path.isfile(os.path.join(PROJ, script)),
                                "%s is missing from the project root" % script)
                module = __import__(target, fromlist=["_"])
                self.assertTrue(callable(getattr(module, function, None)),
                                "%s has no %s() for %s to call"
                                % (target, function, script))

    def test_each_launcher_names_its_target(self):
        """The launcher body has to point at its own module, not just any one."""
        for script, (target, _fn) in sorted(LAUNCHERS.items()):
            with self.subTest(script=script):
                path = os.path.join(PROJ, script)
                package, _, module = target.rpartition(".")
                imports = {name for name, _line in imported_names(path)}
                self.assertIn(package, imports,
                              "%s does not import from %s" % (script, package))
                with open(path, "r", encoding="utf-8") as f:
                    self.assertIn(module, f.read())

    def test_the_headless_launchers_import_without_a_display(self):
        """cli.py, keygen.py and verify_receipt.py must not drag tkinter in.

        A packaged build imports them on a machine that has one, so this would
        never show up there -- it shows up on a build server.
        """
        code = ("import sys;"
                "import cli, keygen, verify_receipt;"
                "assert 'tkinter' not in sys.modules, 'a launcher leaked tkinter';"
                "print('ok')")
        out = subprocess.run([sys.executable, "-c", code], cwd=PROJ,
                             capture_output=True, text=True)
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("ok", out.stdout)


class UiIsALeaf(unittest.TestCase):
    """Only receiptmaker/ui may know tkinter exists."""

    def outside_ui(self):
        for path in python_files(PACKAGE):
            if os.path.sep + "ui" + os.path.sep not in path:
                yield path

    def test_nothing_outside_ui_imports_tkinter(self):
        offenders = []
        for path in self.outside_ui():
            for module, lineno in imported_names(path):
                if module.split(".")[0] == "tkinter":
                    offenders.append("%s:%d" % (os.path.relpath(path, PROJ), lineno))
        self.assertEqual([], offenders,
                         "tkinter outside receiptmaker/ui: " + ", ".join(offenders))

    def test_nothing_outside_ui_imports_the_ui_package(self):
        """`ui` may import anything; nothing may import `ui`.

        Kept separate from the tkinter check because it is the rule that breaks
        first: a storage module reaching back into a dialog for a message box is
        an easy thing to write and an expensive thing to unpick.
        """
        offenders = []
        for path in self.outside_ui():
            for module, lineno in imported_names(path):
                if module.startswith("receiptmaker.ui"):
                    offenders.append("%s:%d imports %s"
                                     % (os.path.relpath(path, PROJ), lineno, module))
        self.assertEqual([], offenders,
                         "these reach back into the UI:\n  " + "\n  ".join(offenders))


if __name__ == "__main__":
    unittest.main()
