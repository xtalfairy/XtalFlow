import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
from . import signing
import msgpack
import os
import json
import getpass

cookies   = {}
address   = 'https://mxlive.5c.postech.ac.kr'
beamline  = 'BL-5C'
username  = getpass.getuser()
#APP_CACHE_DIR = '/data/users/chj/.config/mxdc/'
APP_CACHE_DIR = '/data/users/{0}/.config/mxdc/'.format(username)
_KEY_FILE = os.path.join(os.path.dirname(APP_CACHE_DIR), 'keys.dsa')

def load_metadata(filename):
    with open(filename, 'r') as handle:
        metadata = json.load(handle)
    return metadata

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
            raw_data = msgpack.load(handle, raw=True)
            data = {key.decode('utf-8') if isinstance(key, bytes) else key: value for key, value in raw_data.items()}
    return data

def url(path):
    keys = get_keys()
    signer = signing.Signer(**keys)
    url_path = path[1:] if path[0] == '/' else path
    return '{}/api/v2/{}/{}'.format(
        address,
        signer.sign(username),
        url_path
    )

def get(path, *args, **kwargs):
    r = requests.get(url(path), *args, verify=False, cookies=cookies, **kwargs)
    if r.status_code == requests.codes.ok:
        return r.json()
    else:
        r.raise_for_status()

def post(path, **kwargs):
    r = requests.post(url(path), verify=False, cookies=cookies, **kwargs)
    if r.status_code == requests.codes.ok:
        return r.json()
    else:
        r.raise_for_status()

def upload(path, filename):
    """
    Upload the Metadata to the Server
    @param path: url path to post data to
    @param filename: json-formatted file containing metadata, file will be updated with object id of
    newly created object in the database. To update the contents on the server, this file must contain
    the object id of the existing database entry.
    @return:
    """
    try:
        data = load_metadata(filename)
        reply = post(path, data=msgpack.dumps(data))
    except (IOError, ValueError, requests.HTTPError) as e:
        data = None
    else:
        data.update(reply)
    return data

def get_labworks(beamline):
    path = '/labworks/{}/'.format(beamline)
    try:
        reply = get(path)
    except (IOError, ValueError, requests.HTTPError) as e:
        pass
    return reply

def upload_labworks(beamline, filename):
    """
    Upload the Report metadata to the Server
    @param beamline: beamline acronym (str)
    @param filename: json-formatted file containing metadata
    """
    return upload('/upload_labworks/{}/'.format(beamline), filename)

#data = upload_labworks(beamline, '/usr/local/XtalViewer/src/utils/client/labwork.json')
#print( data )
