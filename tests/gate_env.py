"""Shared setup for tests that render against the pinned gate configuration.

Renders must not depend on whatever sits in the developer's own APP_DIR, so
these tests re-root config at ``tests/fixtures/env``.

That directory is a real APP_DIR, which means the app seeds a ``Templates/``
copy into it on first use -- and then, correctly, never overwrites it, because
overwriting a user's edited templates is exactly what install must not do. For a
*gate* that behaviour is a trap: the seeded copy is a snapshot, so edits to the
repository's own ``Templates/`` would stop reaching the tests and the golden diff
would quietly go on validating a stale layout. Clearing it on entry keeps the
repository the single source of truth, and still exercises the install path.
"""
import os
import shutil

import config
import receipt_render

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE_ENV = os.path.join(PROJ, "tests", "fixtures", "env")

_saved_app_dir = []


def use_gate_env():
    _saved_app_dir.append(config.APP_DIR)
    shutil.rmtree(os.path.join(GATE_ENV, config.TEMPLATES_DIRNAME), ignore_errors=True)
    config.set_app_dir(GATE_ENV)
    receipt_render.clear_template_cache()


def restore():
    if _saved_app_dir:
        config.set_app_dir(_saved_app_dir.pop())
    receipt_render.clear_template_cache()
