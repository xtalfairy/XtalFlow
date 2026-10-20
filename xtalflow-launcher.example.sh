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
#   /usr/local/bin/xtalflow      VERSION=0.9   what people type; unchanged
#   /usr/local/bin/xtalflow09    VERSION=0.9   the same, said explicitly
#   /usr/local/bin/xtalflow10    VERSION=1.0   the new one
#
# Promoting a version then means editing one line of /usr/local/bin/xtalflow,
# and the numbered launchers go on meaning exactly what they say.
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
# a Qt backtrace about xcb that tells a researcher nothing.
if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    echo "No display: open XtalFlow from the console session, not over plain ssh." >&2
    echo "ssh -X also works if X forwarding is allowed." >&2
    exit 1
fi

# The system OpenSSL on this machine is too old for the wheels in the venv.
export LD_LIBRARY_PATH="/usr/local/openssl/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# Instrument folders, the image share and MxLive come from the operating
# profile rather than from flags, so that no launcher carries a path of its
# own to go stale.
export XTALFLOW_SETTINGS=operating

# Each researcher runs as themselves: the review database, the MxLive
# credentials and the crystal scores are all per user and must not be shared.
if [ "$(id -u)" = "0" ]; then
    echo "Do not run XtalFlow as root; run it as your own account." >&2
    exit 1
fi

. "$HERE/.venv3127/bin/activate"
exec xtalflow-viewer "$@"
