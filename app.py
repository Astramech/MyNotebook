"""MyNotebook v2 entry point.

The original application is preserved in ``legacy_app.py``.  The new app uses
additive ``kb_*`` tables, so opening it never overwrites the legacy schema.
"""

from knowledge_base.app import run


if __name__ == "__main__":
    run()
