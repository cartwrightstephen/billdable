"""Field rules shared by every form: phone, ZIP, state, email, licence and contract numbers,
names and addresses. Each rule returns {field: message}, the shape of a 409's "fields".

Imported at the bottom of domain/__init__.py (from .validate import *). Helpers from domain
are imported inside each function, so the two modules never import each other at load time.
"""

__all__: list[str] = []
