import os

import pynetbox


def get_client():
    nb_url = os.environ["nb_url"]
    nb = pynetbox.api(nb_url)
    # nb_token is the complete Authorization header value.
    nb.http_session.headers["Authorization"] = os.environ["nb_token"]
    return nb


def get_or_create(endpoint, lookup, defaults=None):
    obj = endpoint.get(**lookup)
    if obj:
        return obj
    payload = {**lookup, **(defaults or {})}
    return endpoint.create(**payload)
