import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
import signing
import msgpack
import os

cookies   = {}
address   = 'https://mxlive.5c.postech.ac.kr'
beamline  = 'BL-5C'
APP_CACHE_DIR = '/data/users/jjh/.config/mxdc/'
_KEY_FILE = os.path.join(os.path.dirname(APP_CACHE_DIR), 'keys.dsa')

def keys_exist():
    return os.path.exists(_KEY_FILE)

def get_keys():
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import dsa
    from cryptography.hazmat.primitives import serialization
    if not keys_exist():
        key = dsa.generate_private_key(key_size=1024, backend=default_backend())
        data = {
            'private': key.private_bytes(
                serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()
            ),
            'public': key.public_key().public_bytes(
                serialization.Encoding.OpenSSH,
                serialization.PublicFormat.OpenSSH
            )
        }
    else:
        with open(_KEY_FILE, 'rb') as handle:
            data = msgpack.load(handle)
    return data

def url(path):
    keys = get_keys()
    signer = signing.Signer(**keys)
    url_path = path[1:] if path[0] == '/' else path
    return '{}/api/v2/{}/{}'.format(
        address,
        signer.sign('jjh'),
        url_path
    )

def get(path, *args, **kwargs):

    print( url(path) )

    r = requests.get(url(path), *args, verify=False, cookies=cookies, **kwargs)
    if r.status_code == requests.codes.ok:
        return r.json()
    else:
        r.raise_for_status()

def get_samples(beamline):
    path = '/samples/{}/'.format(beamline)
    try:
        reply = get(path)
    except (IOError, ValueError, requests.HTTPError) as e:
        pass
    return reply


data = get_samples(beamline)
for item in data:
    print( item )
