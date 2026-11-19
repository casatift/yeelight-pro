"""Write validation based on LAN protocol v2.4, not observed read properties."""
import re

TIMING = ('duration', 'delay', 'delayoff')
READ_ONLY = {'o', 'fv', 'cp', 'rs', 'cra', 'trs', 'mv', 'level', 'dc', 'alm', 'h', 't', 'luminance'}
BOOL_PROPERTIES = {'p', '0-blp'}
RANGES = {'l': (1, 100), 'c': (0, 16777215), 'ct': (2700, 6500), 'angle': (0, 255),
          'tp': (0, 100), 'tra': (0, 180), 'do': (1, 120), 've': (0, 3), 'fa': (0, 3),
          'he': (0, 3), 'tgt': (0, 50)}
INDEXED = re.compile(r'^([1-9][0-9]*)-(p|sp|acp|acm|acct|actt|acf|aco|acd|acdfltr|acrc)$')


def validate_property(key, value, relative=False):
    suffix = key
    match = INDEXED.fullmatch(key)
    if match:
        if not 1 <= int(match[1]) <= 64:
            raise ValueError('Channel index outside 1..64')
        suffix = match[2]
    if suffix in READ_ONLY or suffix in {'acct', 'aco'}:
        raise ValueError(f'{key} is read-only')
    booleans = BOOL_PROPERTIES | {'sp', 'acp', 'acrc'}
    ranges = {**RANGES, 'actt': (16, 32), 'acd': (1, 43200000), 'acdfltr': (0, 255)}
    enums = {'acm': {1, 4, 8}, 'acf': {1, 2, 4}, 'bhm': {1, 2, 3, 4}}
    if suffix not in booleans and suffix not in ranges and suffix not in enums:
        raise ValueError(f'Undocumented writable property: {key}')
    if relative:
        if suffix in booleans or not isinstance(value, str) or not re.fullmatch(r'[+-][0-9]+/[1-9][0-9]*', value):
            raise ValueError('adjust requires a numeric property and a signed fraction')
        return
    if suffix in booleans:
        if not isinstance(value, bool):
            raise ValueError(f'{key} requires a boolean')
    elif isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{key} requires an integer')
    elif suffix in enums and value not in enums[suffix]:
        raise ValueError(f'Invalid or report-only value for {key}')
    elif suffix in ranges and not ranges[suffix][0] <= value <= ranges[suffix][1]:
        raise ValueError(f'{key} outside protocol range')


def validate_command(method, payload):
    if method not in {'gateway_set.prop', 'device_set.prop', 'gateway_set.event'}:
        if method not in {'gateway_get.topology', 'gateway_get.node', 'gateway_get.group',
                          'gateway_get.room', 'gateway_get.scene', 'device_get.topology'}:
            raise ValueError('Undocumented LAN command')
        return
    for node in payload.get('nodes', []):
        if not isinstance(node.get('id'), int) or not 0 <= node['id'] < 2**64:
            raise ValueError('A uint64 node ID is required')
        if node.get('nt') not in range(1, 7):
            raise ValueError('A documented node type is required')
        if method == 'gateway_set.event':
            if node.get('value') not in {'motion.true', 'motion.false', 'contact.open', 'contact.close',
                'contact.alarm', 'contact.normal', 'panel.click', 'panel.hold', 'panel.release', 'knob.spin'}:
                raise ValueError('Undocumented virtual sensor event')
            continue
        for key, value in node.get('set', {}).items():
            validate_property(key, value)
        for key, value in node.get('adjust', {}).items():
            validate_property(key, value, relative=True)
        for key in node.get('toggle', []):
            validate_property(key, True)
        for key in TIMING:
            if key in node and (isinstance(node[key], bool) or not isinstance(node[key], int) or not 0 <= node[key] <= 3600000):
                raise ValueError('Timing must be 0..3600000 milliseconds')
        for key, action in node.get('action', {}).items():
            if key == 'motorAdjust':
                valid = action.get('type') in {'pause', 'toggle', 'continue', 'auto', 'openOrPause', 'closeOrPause'}
            elif key == 'blink':
                valid = action.get('type') in {'smooth', 'notify', 'urgent'} and isinstance(action.get('repeat'), int) and action['repeat'] > 0
            elif key == 'delayCancel':
                valid = action.get('type') == 'off' and action.get('addr') == 1
            else:
                valid = False
            if not valid:
                raise ValueError('Invalid or undocumented device action')
    for scene in payload.get('scenes', []):
        if not isinstance(scene.get('id'), int) or not 0 <= scene['id'] < 2**64:
            raise ValueError('A uint64 scene ID is required')
        for key in ('duration', 'delay'):
            if key in scene and (isinstance(scene[key], bool) or not isinstance(scene[key], int) or not 0 <= scene[key] <= 3600000):
                raise ValueError('Scene timing outside protocol range')
