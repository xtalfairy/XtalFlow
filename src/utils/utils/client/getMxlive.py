import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
from . import signing # <-
import msgpack
import os
import getpass
from datetime import datetime

cookies   = {}
address   = 'https://mxlive.5c.postech.ac.kr'
beamline  = 'BL-5C'
username  = getpass.getuser()
APP_CACHE_DIR = '/data/users/{0}/.config/mxdc/'.format(username)
#APP_CACHE_DIR = '/data/users/jjh/.config/mxdc/'
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
            raw_data = msgpack.load(handle, raw=True)
            data = {key.decode('utf-8') if isinstance(key, bytes) else key: value for key, value in raw_data.items()}
    return data

def url(path):
    keys = get_keys()
    signer = signing.Signer(**keys)
    url_path = path[1:] if path[0] == '/' else path
    return '{}/api/v2/{}/{}'.format(
        address,
        #signer.sign('jjh'),
        signer.sign(username),
        url_path
    )

def get(path, *args, **kwargs):
    r = requests.get(url(path), *args, verify=False, cookies=cookies, **kwargs)
    if r.status_code == requests.codes.ok:
        return r.json()
    else:
        r.raise_for_status()

def get_labworks(beamline, expid):
    global reply
    path = '/labworks/{}/{}/'.format(beamline, expid)
    try:
        reply = get(path)
    except (IOError, ValueError, requests.HTTPError) as e:
        pass
    return reply

def expOnMxlive():
    year = datetime.today().year
    listA = []
    data = get_labworks(beamline, str(year)) # <-
    for item in data:
        if item['expri_id'] in listA:
            pass
        else:
            listA.append(item['expri_id'])
    return listA

def successXtals(expid):
    data = get_labworks(beamline, str(expid))
    listA = []
    for item in data:
        if 'puck_name' not in item:
            pass
        else:
            if item['puck_name'] == None  or item['puck_name'] == 'None':
                pass
            else:
                listA.append('{0} {1}'.format(item['soak_plate'],item['soak_well']) )
    print(listA)
    return listA
#data = get_labworks(beamline, 'FragSc-202109-NAME-02')
#print( data )
