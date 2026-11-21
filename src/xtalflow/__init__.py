"""XtalFlow's new implementation."""

from xtalflow.build import BUILD

__version__ = "1.1.0"

# The version belongs in the title bar. Several are installed beside one
# another under /usr/local/fbdd_apps/XtalFlow/, sharing one config file, so
# "which one is this?" has no other answer from a running window or from a
# screenshot somebody attaches to a question.
#
# This number is not the name of the folder it is installed in, whatever this
# comment used to claim. Measured on the beamline: the folder 0.9 holds the
# build whose __version__ is 0.1.0, and 1.0 holds this one, 1.1.0. The folder
# is a deployment slot somebody types - the launchers set VERSION to it - and
# renaming it to match would break them. So the two numbers answer different
# questions, and the one that says which installation a window came from is
# the root recorded in the startup log.
APP_TITLE = f"XtalFlow {__version__}"


APP_WINDOW_TITLE = f"{APP_TITLE}{BUILD.title_suffix}"
"""What a window says it is: the version, and which commit of it.

``APP_TITLE`` stays the version on its own, because that number has a second
job - it is meant to be the name of the installed folder somebody types, and
a test keeps it equal to the one in pyproject.toml. The commit is what the
version cannot say, and ``xtalflow.build`` explains why it is read where it
is. A copy with no checkout adds nothing and says the version alone, which is
what it always said."""
