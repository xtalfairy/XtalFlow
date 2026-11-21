"""XtalFlow's new implementation."""

from xtalflow.build import BUILD

__version__ = "1.1.0"

# The version belongs in the title bar. Several versions are installed beside
# one another under /usr/local/fbdd_apps/XtalFlow/, sharing one config file,
# so "which one is this?" has no other answer from a running window or from a
# screenshot somebody attaches to a question.
APP_TITLE = f"XtalFlow {__version__}"


APP_WINDOW_TITLE = f"{APP_TITLE}{BUILD.title_suffix}"
"""What a window says it is: the version, and which commit of it.

``APP_TITLE`` stays the version on its own, because that number has a second
job - it is meant to be the name of the installed folder somebody types, and
a test keeps it equal to the one in pyproject.toml. The commit is what the
version cannot say, and ``xtalflow.build`` explains why it is read where it
is. A copy with no checkout adds nothing and says the version alone, which is
what it always said."""
