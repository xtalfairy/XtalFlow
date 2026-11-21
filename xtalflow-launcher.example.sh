#!/bin/sh
# One launcher per installed version of XtalFlow.
#
# Versions live side by side under /usr/local/fbdd_apps/XtalFlow/ and share
# one xtalflow.toml, so more than one can be installed and run at the same
# time. They also share the per-user review database, which is why no version
# changes its schema: 0.9 and 1.0 open the same plates and the same positions.
#
# Copy this to /usr/local/bin and give it a name that says which version it
# starts. Name them after the version rather than after what they are for
# today - a launcher called xtalflow-test stops being true the moment the
# version it starts becomes the one everybody uses.
#
#   /usr/local/bin/xtalflow      VERSION=1.0   what people type
#   /usr/local/bin/xtalflow09    VERSION=0.9   the old one, said explicitly
#   /usr/local/bin/xtalflow10    VERSION=1.0   the same as xtalflow today
#
# Promoting a version means editing one line of /usr/local/bin/xtalflow, and
# the numbered launchers go on meaning exactly what they say. 1.0 has been
# promoted, so the plain name and xtalflow10 now start the same version; this
# block said 0.9 and "unchanged" until somebody read the installed copy and
# found the comment disagreeing with the VERSION two dozen lines below it.
#
# The folder is a slot, not a version number. 0.9 holds the build whose own
# __version__ is 0.1.0 and 1.0 holds 1.1.0, deliberately: the name is what
# somebody types, and renaming it would break every launcher and the muscle
# memory of everybody who starts this program. Which installation a running
# window came from is answered by the root in its startup log, not by its
# version.
#
#   install -m 755 xtalflow-launcher.example.sh /usr/local/bin/xtalflow10
#   # then set VERSION below

set -eu

VERSION=1.0
ROOT=/usr/local/fbdd_apps/XtalFlow
HERE="$ROOT/$VERSION"

if [ ! -d "$HERE" ]; then
    echo "XtalFlow $VERSION is not installed at $HERE" >&2
    exit 1
fi

# A window needs somewhere to open. Said plainly, because the alternative is
# a Qt backtrace about xcb that tells a researcher nothing. QT_QPA_PLATFORM
# set means somebody has already chosen a backend - offscreen for a smoke
# test, say - and knows better than this check.
if [ -z "${QT_QPA_PLATFORM:-}" ] &&
   [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    echo "No display: open XtalFlow from the console session, not over plain ssh." >&2
    echo "ssh -X also works if X forwarding is allowed." >&2
    exit 1
fi

# The system OpenSSL on this machine is too old for the wheels in the venv.
export LD_LIBRARY_PATH="/usr/local/openssl/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# Instrument folders, the image share and MxLive come from the operating
# profile rather than from flags, so that no launcher carries a path of its
# own to go stale.
# Overridable, so a researcher can point one run at development settings
# without a launcher of their own.
export XTALFLOW_SETTINGS="${XTALFLOW_SETTINGS:-operating}"

# Each researcher runs as themselves: the review database, the MxLive
# credentials and the crystal scores are all per user and must not be shared.
if [ "$(id -u)" = "0" ]; then
    echo "Do not run XtalFlow as root; run it as your own account." >&2
    exit 1
fi

. "$HERE/.venv3127/bin/activate"
exec xtalflow-viewer "$@"
