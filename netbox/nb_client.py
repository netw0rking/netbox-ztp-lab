import os

import pynetbox


def get_client():
    nb_url = os.environ["nb_url"]
    nb = pynetbox.api(nb_url)
    # This NetBox instance authenticates via a full "Bearer <key>.<token>"
    # Authorization header, not NetBox's classic "Token <token>" scheme -
    # confirmed live (a bare pynetbox token= constructor sends "Token <value>"
    # and gets a 403). nb_token holds the complete header value already.
    nb.http_session.headers["Authorization"] = os.environ["nb_token"]
    return nb


def get_or_create(endpoint, lookup, defaults=None):
    obj = endpoint.get(**lookup)
    if obj:
        return obj
    payload = {**lookup, **(defaults or {})}
    return endpoint.create(**payload)
