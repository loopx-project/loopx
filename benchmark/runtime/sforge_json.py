"""Lossless JSON transport for local SForge result artifacts.

Finite legacy documents keep their ordinary JSON representation. Nonfinite
numbers use an explicit envelope and paths into its value, never inline tags
that could be confused with a task's literal metric. This is transport only;
native grading and selection still receive the original numeric values.
"""
from __future__ import annotations

import json
import math

_MARKER = "sforge_nonfinite_json"
_KINDS = {"positive_infinity": "inf", "negative_infinity": "-inf", "nan": "nan"}


def encode_document(document, *, fields=None):
    """Return a JSON-safe document; optional fields restrict numeric encoding."""
    if not isinstance(document, dict):
        raise ValueError("SForge result transport requires an object")
    values = []

    def visit(value, path):
        if isinstance(value, float) and not math.isfinite(value):
            if fields is not None and path[0] not in fields:
                raise ValueError("Nonfinite number outside released result fields")
            kind = "nan" if math.isnan(value) else (
                "positive_infinity" if value > 0 else "negative_infinity")
            values.append({"path": path, "kind": kind})
            return None
        if isinstance(value, dict):
            pairs = {}
            for key, item in value.items():
                # Follow JSON's existing conversion of scalar object keys.
                if not isinstance(key, str):
                    key = next(iter(json.loads(json.dumps({key: None}, allow_nan=False))))
                if key in pairs:
                    raise ValueError("SForge result keys collide in JSON")
                pairs[key] = item
            # Stable path order also keeps capture bindings independent of dict order.
            return {key: visit(item, [*path, key]) for key, item in sorted(pairs.items())}
        if isinstance(value, (list, tuple)):
            return [visit(item, [*path, index]) for index, item in enumerate(value)]
        return value

    value = visit(document, [])
    if not values and _MARKER not in document:
        return document
    # Escape a literal root marker as data, even when it contains only finite
    # numbers. Decoding unwraps exactly once; nested marker-shaped values stay data.
    return {_MARKER: 1, "value": value, "nonfinite_values": values}


def decode_document(document, *, fields=None):
    """Decode one envelope from a strictly parsed JSON document."""
    if not isinstance(document, dict):
        raise ValueError("SForge result transport requires an object")
    if _MARKER not in document:
        return document
    if (set(document) != {_MARKER, "value", "nonfinite_values"}
            or type(document[_MARKER]) is not int or document[_MARKER] != 1
            or not isinstance(document["value"], dict)):
        raise ValueError("Invalid SForge nonfinite transport envelope")
    value, entries = document["value"], document["nonfinite_values"]
    if not isinstance(entries, list) or (not entries and _MARKER not in value):
        raise ValueError("SForge nonfinite transport requires value paths")

    def lookup(container, key):
        if isinstance(container, dict) and isinstance(key, str) and key in container:
            return container[key]
        if isinstance(container, list) and type(key) is int and 0 <= key < len(container):
            return container[key]
        raise ValueError("SForge nonfinite value path is unavailable")

    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "kind"}:
            raise ValueError("Invalid SForge nonfinite value")
        path, kind = entry["path"], entry["kind"]
        if (not isinstance(path, list) or not path
                or not all(type(key) is str or (type(key) is int and key >= 0) for key in path)
                or not isinstance(kind, str) or kind not in _KINDS
                or (fields is not None and path[0] not in fields)):
            raise ValueError("Invalid SForge nonfinite value path or kind")
        identity = tuple(path)
        if identity in seen:
            raise ValueError("Duplicate SForge nonfinite value path")
        seen.add(identity)
        container = value
        for key in path[:-1]:
            container = lookup(container, key)
        if lookup(container, path[-1]) is not None:
            raise ValueError("SForge nonfinite value must replace null")
        container[path[-1]] = float(_KINDS[kind])
    return value


def dumps(document, *, fields=None, **options):
    return json.dumps(encode_document(document, fields=fields), allow_nan=False, **options)


def loads(encoded, *, fields=None):
    def finite_number(text):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("Unmarked nonfinite SForge result number")
        return value

    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate SForge result object key")
            value[key] = item
        return value

    document = json.loads(encoded, parse_float=finite_number, parse_constant=finite_number,
                          object_pairs_hook=unique_object)
    return decode_document(document, fields=fields)
