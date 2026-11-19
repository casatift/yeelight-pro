"""Observed smart low-voltage cabinet extension (outside protocol v2.4).

Only this product's verified virtual endpoints are admitted from property
pushes. Other unlisted nodes and unverified speed/direction remain unsupported.
"""
from math import isfinite

CABINET_PID = 17000064
CABINET_ENDPOINTS = {
    1: (1, 'light', 'p'),
    2: (2054, 'fan', 'p'),
    6: (2056, 'temperature', 't'),
    7: (2057, 'humidity', 'h'),
}


def cabinet_endpoint_kind(node, require_value=False):
    nid = node.get('id')
    if (node.get('pid') != CABINET_PID or node.get('nt', 2) != 2
            or isinstance(nid, bool) or not isinstance(nid, int)
            or not 0 < nid < 2**64 or not nid & ((1 << 56) - 1)):
        return None
    expected = CABINET_ENDPOINTS.get(nid >> 56)
    if not expected or node.get('type', node.get('pt')) != expected[0]:
        return None
    _, kind, prop = expected
    if require_value:
        value = (node.get('params') or {}).get(prop)
        if kind in ('light', 'fan'):
            if not isinstance(value, bool):
                return None
        elif (isinstance(value, bool) or not isinstance(value, (int, float))
              or not isfinite(value) or not (-5000 <= value <= 12500
                  if kind == 'temperature' else 0 <= value <= 10000)):
            return None
    return kind
