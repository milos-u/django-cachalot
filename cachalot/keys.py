"""
Generovani klicu — zamerne BEZ zavislosti na zbytku cachalotu.

``utils`` importuje ``local_store`` a ``local_store`` potrebuje
``gen_random_key``, takze kdyby zustala v ``utils``, vznikl by kruh.
Sdilena pomocna funkce proto bydli v samostatnem modulu, ktery nic
dalsiho neimportuje.
"""
from hashlib import sha1
from uuid import uuid4


def gen_random_key(key=None):
    key = key or str(uuid4())
    return sha1(key.encode('utf-8')).hexdigest()
