# XtalFlow

XtalFlow reviews crystallization images, records crystal target positions, builds
experiment plans, and generates SHIFTER/ECHO worksheets. The current planning
workflows are Raw Crystal Plan and Fragment Screening.

## Supported Python

- Python 3.9 through 3.12
- Python must include the standard `ssl` module for MxLive HTTPS access.
- The operating server has been tested with Python 3.12.7.

Check the interpreter before creating a virtual environment:

```bash
python3.12 -V
python3.12 -c 'import ssl; print(ssl.OPENSSL_VERSION)'
```

If the operating-server Python uses `/usr/local/openssl`, set its runtime
library path before creating or activating the environment:

```bash
export LD_LIBRARY_PATH="/usr/local/openssl/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
```

## Operating-server installation

Run these commands from the repository root, where `pyproject.toml` and
`requirements.txt` are located. Do not reuse or copy another application's
virtual environment.

```bash
cd /usr/local/fbdd_apps/XtalViewer/2.0

export LD_LIBRARY_PATH="/usr/local/openssl/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

/opt/pyenv/versions/3.12.7/bin/python -m venv .venv3127
source .venv3127/bin/activate

python -V
python -c 'import ssl; print(ssl.OPENSSL_VERSION)'
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install --no-deps --no-build-isolation -e .
```

`requirements-py39.txt` is retained as an alias for Python 3.9 server
installations. It installs the same Python 3.9–3.12 compatible pins.
The original XtalViewer dependency snapshot is preserved separately as
`requirements-legacy.txt`; do not install it into the XtalFlow environment.

Verify the installation:

```bash
which python
which xtalflow-viewer
which xtalflow-mxlive-read
python -c 'import xtalflow; print(xtalflow.__file__)'
```

For a shared installation, root may create the environment, but each scientist
should run XtalFlow from their own login account. Each user needs read/execute
access to the repository and virtual environment. User databases and MxLive
credentials must not be shared.

At each new login:

```bash
export LD_LIBRARY_PATH="/usr/local/openssl/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
source /usr/local/fbdd_apps/XtalViewer/2.0/.venv3127/bin/activate
```

## Running the viewer

Development defaults point to repository fixtures. On the operating server,
pass the site paths explicitly unless `DEFAULT_SETTINGS` has been switched to
`OPERATING_SERVER_SETTINGS`:

```bash
xtalflow-viewer \
  --root /smbmount/rmserver/RockMakerStorage/WellImages \
  --library-dir /usr/local/fbdd_apps/XtalViewer/2.0/chems \
  --worksheet-dir /tmp/xtalflow/worksheets \
  --echo-dir /smbmount/echo650 \
  --shifter1-dir /smbmount/shifter1 \
  --shifter2-dir /smbmount/shifter2
```

Use `xtalflow-viewer --help` to see all options.

## MxLive read-only verification

The verified MxLive hostname is `mxlive.postech.ac.kr`. The obsolete
`mxlive.5c.postech.ac.kr` hostname does not match the server certificate.

MxLive v2 authenticates the signed username using that user's legacy DSA key:

```text
/data/users/{username}/.config/mxdc/keys.dsa
```

Run the check as the actual MxLive user, or supply both the matching username
and key explicitly:

```bash
xtalflow-mxlive-read \
  --url https://mxlive.postech.ac.kr \
  --beamline BL-5C \
  --username "$USER" \
  --key "/data/users/$USER/.config/mxdc/keys.dsa" \
  --ca /etc/pki/tls/certs/ca-bundle.crt \
  --year 2026
```

To verify the samples endpoint without printing sample details:

```bash
xtalflow-mxlive-read \
  --url https://mxlive.postech.ac.kr \
  --beamline BL-5C \
  --username "$USER" \
  --key "/data/users/$USER/.config/mxdc/keys.dsa" \
  --ca /etc/pki/tls/certs/ca-bundle.crt \
  --year 2026 \
  --include-sample-count
```

This command performs GET requests only. It does not create or modify MxLive
records.

## Common installation errors

### `No matching distribution found for requirements.txt`

The `-r` option is required:

```bash
python -m pip install -r requirements.txt
```

### `Cannot import 'setuptools.build_meta'`

Install the requirements before the editable package. `setuptools==75.8.2` is
included in `requirements.txt`:

```bash
python -m pip install -r requirements.txt
python -m pip install --no-deps --no-build-isolation -e .
```

### `xtalflow-viewer: command not found`

Activate the XtalFlow environment and install the editable package:

```bash
source .venv3127/bin/activate
python -m pip install --no-deps --no-build-isolation -e .
```

### `Python SSL support is unavailable`

The interpreter was built without a working `_ssl` module or cannot locate its
OpenSSL runtime. Fix the Python/OpenSSL installation before accessing MxLive;
TLS verification is not disabled by XtalFlow.

### MxLive hostname mismatch

Use `https://mxlive.postech.ac.kr`. Supplying a different CA bundle cannot fix
a certificate hostname mismatch.

## Development checks

```bash
source .venv_air/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
```
