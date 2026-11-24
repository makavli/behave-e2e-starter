"""Read and change JSON documents by path: "items[0].quantity" or "items.0.quantity"."""
import json
import re

_KEY = re.compile(r"[^.\[\]]+")


def _keys(path):
    return [int(k) if k.isdigit() else k for k in _KEY.findall(path)]


def _walk(doc, keys, path):
    node = doc
    for key in keys:
        try:
            node = node[key]
        except (KeyError, IndexError, TypeError):
            raise AssertionError(f"no '{path}' in {json.dumps(doc, default=str)[:300]}") from None
    return node


def get_path(doc, path):
    return _walk(doc, _keys(path), path)


def set_path(doc, path, value):
    *parents, last = _keys(path)
    parent = _walk(doc, parents, path)
    try:
        parent[last] = value
    except (IndexError, TypeError):
        raise AssertionError(f"cannot set '{path}'") from None


def del_path(doc, path):
    *parents, last = _keys(path)
    parent = _walk(doc, parents, path)
    try:
        del parent[last]
    except (KeyError, IndexError, TypeError):
        raise AssertionError(f"no '{path}' to remove") from None
