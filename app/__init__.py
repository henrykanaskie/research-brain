"""research_brain: a local knowledge graph for papers and notes."""

__version__ = "1.0.85"


def _safe_output() -> None:
    """Printing must never be what breaks. On Windows a redirected stdout (app.log, when opened from the Start menu) is
    written in the old code page, and print() of anything it can't spell (a paper's title with a Greek letter, a
    folder named in another script) raised UnicodeEncodeError: the request, or the whole start, failed. Every entry point
    imports this package, so here: output that isn't a console is UTF-8, and nothing is ever refused."""
    import sys
    for s in (sys.stdout, sys.stderr):
        try:  # None under pythonw; not reconfigurable when a test or a tool replaced it
            if s.isatty():
                s.reconfigure(errors="backslashreplace")
            else:
                s.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError, ValueError):
            pass


_safe_output()
