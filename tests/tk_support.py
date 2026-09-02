"""Tearing a Tk root down without aborting the interpreter.

`tk.Variable.__del__` calls back into its interpreter. If the variable is
collected *after* `root.destroy()`, that call fails — and on Windows it does not
merely raise, it prints

    Tcl_AsyncDelete: async handler deleted by the wrong thread

and takes the whole process with it. Whether it happens depends only on when the
garbage collector runs, which is why a suite can pass for weeks and then abort
the moment something changes the timing: adding a test file, reordering, or
running under `coverage`, which is how this was actually found. The plain suite
was green and `coverage run` aborted every time.

So every test that builds a Tk root releases its widgets *while the interpreter
is still alive*, and collects before and after destroying it. One helper rather
than a copy per file, because a copy per file is how three of sixteen ended up
with it.
"""
import gc


def destroy(case, *extra_attributes):
    """Tear down `case.root`, releasing widget-holding attributes first.

    Pass the names of any extra attributes holding widgets or Tk variables;
    `app` and `dialog` are released automatically because almost every test
    here calls them one of those.
    """
    for name in ("app", "dialog") + tuple(extra_attributes):
        held = getattr(case, name, None)
        if held is None:
            continue
        # Clearing the instance dict drops the StringVars it owns. Doing it now
        # -- before destroy() -- is the whole point: __del__ then runs against a
        # live interpreter.
        if hasattr(held, "__dict__"):
            try:
                held.__dict__.clear()
            except Exception:                    # noqa: BLE001 - teardown only
                pass
        try:
            delattr(case, name)
        except AttributeError:
            pass

    gc.collect()

    root = getattr(case, "root", None)
    if root is not None:
        try:
            root.destroy()
        except Exception:                        # noqa: BLE001 - already gone
            pass
        try:
            delattr(case, "root")
        except AttributeError:
            pass

    gc.collect()
