import asyncio
import logging
from enum import IntEnum
from .converters.base import *

from typing import Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .. import XEntity
    from .gateway import ProGateway
    from homeassistant.core import HomeAssistant

from homeassistant.components.light import ColorMode
from homeassistant.components.climate import (
    FAN_LOW,
    FAN_MEDIUM,
    FAN_HIGH,
)
from homeassistant.components.climate.const import (
    HVACMode,
)

_LOGGER = logging.getLogger(__name__)


class NodeType(IntEnum):
    GATEWAY = -1
    ROOM = 1
    MESH = 2
    GROUP = 3
    MRSH_GROUP = 4
    HOME = 5
    SCENE = 6


class DeviceType(IntEnum):
    LIGHT = 1
    LIGHT_WITH_BRIGHTNESS = 2
    LIGHT_WITH_COLOR_TEMP = 3
    LIGHT_WITH_COLOR = 4
    CURTAIN = 6
    RELAY_DOUBLE = 7
    VRF = 10
    SWITCH_PANEL = 13
    LIGHT_WITH_ZOOM_CT = 14
    AIR_CONDITIONER = 15
    SWITCH_SENSOR = 128
    MOTION_SENSOR = 129
    MAGNET_SENSOR = 130
    KNOB = 132
    MOTION_WITH_LIGHT = 134
    ILLUMINATION_SENSOR = 135
    TEMPERATURE_HUMIDITY = 136
    MAIRUI_MOTION = 138
    BATH_HEATER = 2049
    TOF = 2052


DEVICE_TYPE_LIGHTS = [
    DeviceType.LIGHT,
    DeviceType.LIGHT_WITH_BRIGHTNESS,
    DeviceType.LIGHT_WITH_COLOR_TEMP,
    DeviceType.LIGHT_WITH_COLOR,
    DeviceType.LIGHT_WITH_ZOOM_CT,
]


class XDevice:
    hass: "HomeAssistant" = None
    converters: Dict[str, Converter] = None

    def __init__(self, node: dict):
        self.id = int(node['id'])
        self.nt = node.get('nt', 0)
        self.pid = node.get('pid')
        self.type = node.get('type', node.get('pt', 0))
        self.name = node.get('n', '')
        self.cids = node.get('cids') or []
        self.ch_num = node.get('ch_num')
        self.roomid = node.get('roomid')
        self.prop = {}
        self.observed_props = set()
        self.entities: Dict[str, "XEntity"] = {}
        self.gateways: List["ProGateway"] = []
        self.converters = {}
        self.setup_converters()

    def setup_converters(self):
        pass

    def add_converter(self, conv: Converter):
        self.converters[conv.attr] = conv

    def add_converters(self, *args: Converter):
        for conv in args:
            self.add_converter(conv)

    @staticmethod
    async def from_node(gateway: "ProGateway", node: dict):
        if node.get('nt') not in [NodeType.MESH, NodeType.MRSH_GROUP, NodeType.SCENE]:
            return None
        if not (nid := node.get('id')):
            return None
        if dvc := gateway.devices.get(gateway.node_key(nid, node.get('nt', 2))):
            if n := node.get('n'):
                dvc.name = n
            for key in ('pid', 'ch_num', 'roomid'):
                if key in node:
                    setattr(dvc, key, node[key])
            if 'cids' in node:
                dvc.cids = node['cids'] or []
            dvc.setup_converters()
            await dvc.setup_entities()
        else:
            dvc = XDevice(node)
            if dvc.nt in [NodeType.SCENE]:
                if isinstance(gateway.device, GatewayDevice):
                    await gateway.device.add_scene(node)
                return gateway.device
            elif dvc.type in DEVICE_TYPE_LIGHTS:
                dvc = LightDevice(node)
            elif dvc.type in [DeviceType.SWITCH_PANEL]:
                dvc = SwitchPanelDevice(node)
            elif dvc.type in [DeviceType.RELAY_DOUBLE]:
                dvc = RelayDoubleDevice(node)
            elif dvc.type in [DeviceType.SWITCH_SENSOR]:
                # Add support for the E-Series Knob as its DeviceType ID is 128.
                dvc = KnobDevice(node)                  
            elif dvc.type in [DeviceType.KNOB]:
                dvc = KnobDevice(node)
            elif dvc.type in [DeviceType.MOTION_SENSOR, DeviceType.MOTION_WITH_LIGHT, DeviceType.MAIRUI_MOTION]:
                dvc = MotionDevice(node)
            elif dvc.type == DeviceType.ILLUMINATION_SENSOR:
                dvc = IlluminanceDevice(node)
            elif dvc.type == DeviceType.TEMPERATURE_HUMIDITY:
                dvc = TemperatureHumidityDevice(node)
            elif dvc.type == DeviceType.TOF:
                dvc = TofDevice(node)
            elif dvc.type == DeviceType.BATH_HEATER:
                dvc = BathHeaterDevice(node)
            elif dvc.type in [DeviceType.MAGNET_SENSOR]:
                dvc = ContactDevice(node)
            elif dvc.type in [DeviceType.CURTAIN]:
                dvc = CoverDevice(node)
            elif dvc.type in [DeviceType.AIR_CONDITIONER, DeviceType.VRF]:
                dvc = ClimateDevice(node)
            else:
                _LOGGER.warning('Unsupported device: %s', node)
                return None
            if gateway.pid == 2:
                await gateway.get_node(dvc.id, wait_result=False)
            await gateway.add_device(dvc)
        return dvc

    @staticmethod
    async def from_nodes(gateway: "ProGateway", nodes: List[dict]):
        dls = []
        for node in nodes:
            if (dvc := await XDevice.from_node(gateway, node)) is None:
                continue
            dls.append(dvc)
        return dls

    async def prop_changed(self, data: dict):
        # v2.4 p8: a push may contain only the properties that changed.
        params = {**self.prop_params, **(data.get('params') or {})}
        self.prop.update(data)
        self.prop['params'] = params
        self.observed_props.update((data.get('params') or {}).keys())
        if 'pid' in data:
            self.pid = data['pid']
        self.setup_converters()
        await self.setup_entities()
        self.update(self.decode(data))

    async def event_fired(self, data: dict):
        decoded = self.decode_event(data)
        self.update(decoded)
        if self.hass:
            self.hass.bus.async_fire('yeelight_pro_event', {
                'entry_id': self.gateway.entry_id, 'node_id': self.id, 'nt': self.nt,
                'event': (data.get('value') or data.get('type') or '').strip(),
                'params': data.get('params') or {},
            })
        _LOGGER.debug('Event fired: %s', [data, decoded])

    @property
    def gateway(self):
        if self.gateways:
            return self.gateways[0]
        return None

    @property
    def online(self):
        return self.prop.get('o')

    @property
    def firmware_version(self):
        return self.prop.get('fv')

    @property
    def prop_params(self):
        return self.prop.get('params') or {}

    @property
    def unique_id(self):
        return f'{self.type}_{self.id}' if self.nt == 2 else f'{self.nt}_{self.type}_{self.id}'

    def entity_id(self, conv: Converter):
        return f'{conv.domain}.yp{self.unique_id}_{conv.attr}'

    async def setup_entities(self):
        if not (gateway := self.gateway):
            return
        if not self.converters:
            _LOGGER.warning('Device has none converters: %s', [type(self), self.id])
        for conv in self.converters.values():
            domain = conv.domain
            if domain is None:
                continue
            if conv.attr in self.entities:
                continue
            await gateway.setup_entity(domain, self, conv)

    def subscribe_attrs(self, conv: Converter):
        attrs = {conv.attr}
        if conv.childs:
            attrs |= set(conv.childs)
        attrs.update(c.attr for c in self.converters.values() if c.parent == conv.attr)
        return attrs

    def decode(self, value: dict) -> dict:
        """Decode device props for HA."""
        payload = {}
        if 'o' in value:
            payload['available'] = value['o'] is True
        for conv in self.converters.values():
            prop = conv.prop or conv.attr
            data = value
            if isinstance(conv, PropConv):
                data = value.get('params') or {}
            if prop not in data:
                continue
            conv.decode(self, payload, data[prop])
        return payload

    def decode_event(self, data: dict) -> dict:
        """Decode device event for HA."""
        payload = {}
        event = (data.get('value') or data.get('type') or '').strip()
        if conv := self.converters.get(event):
            value = data.get('params') or {}
            conv.decode(self, payload, value)
        return payload

    def encode(self, value: dict) -> dict:
        """Encode payload for device."""
        payload = {}
        for conv in self.converters.values():
            if conv.attr not in value:
                continue
            if isinstance(conv, PropConv):
                dat = payload.setdefault('set', {})
            else:
                dat = payload
            conv.encode(self, dat, value[conv.attr])
        return payload

    def encode_read(self, attrs: set) -> dict:
        payload = {}
        for conv in self.converters.values():
            if conv.attr not in attrs:
                continue
            conv.read(self, payload)
        return payload

    def update(self, value: dict):
        """Push new state to Hass entities."""
        if not value:
            return
        attrs = value.keys()

        for entity in self.entities.values():
            if not (entity.subscribed_attrs & attrs) and 'available' not in value:
                continue
            entity.async_set_state(value)
            if entity.added:
                entity.async_write_ha_state()

    async def get_node(self):
        if not self.gateway:
            return None
        return await self.gateway.send('gateway_get.node', params={'id': self.id})

    async def set_prop(self, **kwargs):
        if not self.gateway:
            return None
        cmd = kwargs.pop('method', 'gateway_set.prop')
        node = {
            'id': self.id,
            'nt': self.nt,
            **kwargs,
        }
        return await self.gateway.send(cmd, nodes=[node])


class GatewayDevice(XDevice):
    def __init__(self, gateway: "ProGateway"):
        super().__init__({
            'id': 0,
            'nt': NodeType.GATEWAY,
            'pid': 'gateway',
            'type': 'gateway',
        })
        self.id = gateway.host
        self.name = 'Yeelight Pro'

    def setup_converters(self):
        self.add_converter(Converter('connection', 'binary_sensor', writable=False, device_class='connectivity'))

    async def add_scene(self, node: dict):
        if not (nid := node.get('id')):
            return
        self.add_converter(SceneConv(f'scene_{nid}', 'button', node=node))
        self.add_converter(Converter(f'scene_{nid}_state', 'sensor', writable=False))
        await self.setup_entities()
        if state := (node.get('params') or {}).get('state'):
            self.observed_props.add(f'scene_{nid}_state')
            self.update({f'scene_{nid}_state': state})

    def entity_id(self, conv: Converter):
        return f'{conv.domain}.yp_{conv.attr}'


class LightDevice(XDevice):
    def setup_converters(self):
        super().setup_converters()
        self.add_converter(PropBoolConv('light', 'light', prop='p'))
        self.add_converter(DurationConv('delay', parent='light'))
        self.add_converter(DurationConv('delayoff', 'number', readable=False))
        self.add_converter(DurationConv('transition', prop='duration', parent='light'))
        if ColorMode.BRIGHTNESS in self.color_modes:
            self.add_converter(BrightnessConv('brightness', prop='l', parent='light'))
        if ColorMode.COLOR_TEMP in self.color_modes:
            self.add_converter(ColorTempKelvin('color_temp_kelvin', prop='ct', parent='light'))
        if ColorMode.RGB in self.color_modes:
            self.add_converter(ColorRgbConv('rgb_color', prop='c', parent='light'))
        if self.type == DeviceType.LIGHT_WITH_ZOOM_CT:
            self.add_converter(NumericConv('angle', 'number', prop='angle', min=0, max=255))

    @property
    def color_modes(self):
        modes = {
            ColorMode.ONOFF,
        }
        if self.type == DeviceType.LIGHT_WITH_BRIGHTNESS:
            modes.add(ColorMode.BRIGHTNESS)
        if self.type in [DeviceType.LIGHT_WITH_COLOR_TEMP, DeviceType.LIGHT_WITH_ZOOM_CT]:
            modes.add(ColorMode.BRIGHTNESS)
            modes.add(ColorMode.COLOR_TEMP)
        if self.type == DeviceType.LIGHT_WITH_COLOR:
            modes.add(ColorMode.BRIGHTNESS)
            modes.add(ColorMode.COLOR_TEMP)
            modes.add(ColorMode.RGB)
        if 72 in self.cids:
            modes.discard(ColorMode.COLOR_TEMP)
        return modes


class ActionDevice(XDevice):
    def setup_converters(self):
        super().setup_converters()
        self.add_converter(Converter('action', 'sensor'))


class SwitchSensorDevice(ActionDevice):
    def setup_converters(self):
        super().setup_converters()
        self.add_converters(
            EventConv('panel.click'),
            EventConv('panel.hold'),
            EventConv('panel.release'),
        )


class RelayDevice(XDevice):
    def setup_converters(self):
        super().setup_converters()
        switches = self.switches
        if len(switches) == 1:
            self.add_converter(PropBoolConv('switch', 'switch', prop='1-p'))
        else:
            for i, p in self.switches.items():
                self.add_converter(PropBoolConv(f'switch{i}', 'switch', prop=f'{i}-p'))

    @property
    def switches(self):
        lst = {}
        for i in range(1, min(64, self.ch_num or 8) + 1):
            if (p := self.switch_power(i)) is None:
                continue
            lst[i] = p
        return lst

    def switch_power(self, index=1):
        return self.prop_params.get(f'{index}-p')


class SwitchPanelDevice(RelayDevice, SwitchSensorDevice):
    def setup_converters(self):
        super().setup_converters()
        SwitchSensorDevice.setup_converters(self)

        switches = self.switches
        if len(switches) == 1:
            self.add_converter(PropBoolConv('switch', 'switch', prop='1-sp'))
        else:
            for i, p in self.switches.items():
                self.add_converter(PropBoolConv(f'switch{i}', 'switch', prop=f'{i}-sp'))
        if '0-blp' in self.prop_params:
            self.add_converter(PropBoolConv('backlight', 'light', prop='0-blp'))

    def switch_power(self, index=1):
        return self.prop_params.get(f'{index}-sp')


class RelayDoubleDevice(XDevice):
    def setup_converters(self):
        self.add_converters(
            PropBoolConv('switch1', 'switch', prop='1-p'),
            PropBoolConv('switch2', 'switch', prop='2-p')
        )


class KnobDevice(SwitchSensorDevice):
    def setup_converters(self):
        super().setup_converters()
        self.add_converter(EventConv('knob.spin'))


class MotionDevice(XDevice):
    def setup_converters(self):
        super().setup_converters()
        self.add_converter(Converter('motion', 'binary_sensor'))
        self.add_converters(PropBoolConv('motion', 'binary_sensor', prop='mv', writable=False, device_class='motion'))
        self.add_converter(EventConv('motion.true'))
        self.add_converter(EventConv('motion.false'))
        if self.type in [DeviceType.MOTION_WITH_LIGHT]:
            self.add_converter(PropConv('light', 'sensor', prop='level'))
        
        # This is a presence sensor with a built-in light sensor. Its type is still defined as 129,
        # so we can only temporarily distinguish it by the `cids` value.
        if self.type == DeviceType.MAIRUI_MOTION or set(self.cids) & {10, 69, 73} or 'luminance' in self.prop_params:
            # Regular presence sensors use cids = [9], while ceiling-mounted sensors with light detection use cids = [73].
            self.add_converter(PropConv(
                    attr='luminance',
                    domain='sensor',
                    prop='luminance',
                    unit_of_measurement='lx',
                    device_class='illuminance'
                    ,writable=False
            ))
        if self.type == DeviceType.MAIRUI_MOTION or 73 in self.cids:
            self.add_converter(Converter('approach', 'binary_sensor', writable=False, device_class='occupancy'))
            self.add_converter(EventConv('approach.true'))
            self.add_converter(EventConv('approach.false'))

            # Currently, `approach.true` and `approach.false` seem to behave the same as `mv` (motion).


class ContactDevice(XDevice):
    def setup_converters(self):
        super().setup_converters()
        self.add_converter(PropMapConv('contact', 'binary_sensor', prop='dc', map={0: True, 1: False}, writable=False, device_class='door'))
        self.add_converter(PropBoolConv('tamper', 'binary_sensor', prop='alm', writable=False, device_class='tamper'))
        self.add_converter(EventConv('contact.open'))
        self.add_converter(EventConv('contact.close'))
        self.add_converter(EventConv('contact.alarm'))
        self.add_converter(EventConv('contact.normal'))


class CoverDevice(XDevice):
    def setup_converters(self):
        super().setup_converters()
        self.add_converters(
            MotorConv('motor', 'cover'),
            NumericConv('position', parent='motor', prop='tp', min=0, max=100),
            PropConv('current_position', parent='motor', prop='cp', writable=False),
        )
        if 'rs' in self.prop_params:
            self.add_converter(PropBoolConv('route_calibrated', 'binary_sensor', prop='rs', writable=False))
        if 39 in self.cids or set(self.prop_params) & {'cra', 'tra', 'trs'}:
            self.add_converter(NumericConv('target_angle', 'number', prop='tra', min=0, max=180, unit_of_measurement='°'))
            self.add_converter(PropConv('current_angle', 'sensor', prop='cra', writable=False, unit_of_measurement='°'))
            self.add_converter(PropBoolConv('tilt_calibrated', 'binary_sensor', prop='trs', writable=False))


class WifiPanelDevice(RelayDoubleDevice):
    def __init__(self, node: dict):
        super().__init__({
            **node,
            'type': 'wifi_panel',
        })
        self.name = 'Yeelight Wifi Panel'

    async def set_prop(self, **kwargs):
        kwargs['method'] = 'device_set.prop'
        return await super().set_prop(**kwargs)

    def entity_id(self, conv: Converter):
        return f'{conv.domain}.yp_{self.id}_{conv.attr}'

    def setup_converters(self):
        super().setup_converters()
        self.add_converter(Converter('action', 'sensor'))
        self.add_converter(EventConv('keyClick'))


class ClimateDevice(XDevice):
    def setup_converters(self):
        super().setup_converters()
        channels = {int(key.split('-')[0]) for key in self.prop_params
                    if '-' in key and key.split('-')[0].isdigit() and key.endswith('-acp')}
        channels.update(range(1, min(64, self.ch_num or 1) + 1))
        for index in sorted(channels):
            parent = 'climate' if index == 1 else f'climate_{index}'
            prefix = '' if index == 1 else f'ch{index}_'
            self.add_converter(Converter(parent, 'climate', channel=index))
            self.add_converter(PropBoolConv(prefix+'is_on', parent=parent, prop=f'{index}-acp'))
            self.add_converter(PropConv(prefix+'current_temperature', parent=parent, prop=f'{index}-acct', writable=False))
            self.add_converter(NumericConv(prefix+'target_temperature', parent=parent, prop=f'{index}-actt', min=16, max=32))
            self.add_converter(PropMapConv(prefix+'mode', parent=parent, prop=f'{index}-acm', map={
                1: HVACMode.COOL, 4: HVACMode.FAN_ONLY, 8: HVACMode.HEAT}))
            self.add_converter(PropMapConv(prefix+'fan_mode', parent=parent, prop=f'{index}-acf', map={
                1: FAN_HIGH, 2: FAN_MEDIUM, 4: FAN_LOW}))
            self.add_converter(PropBoolConv(prefix+'online', 'binary_sensor', parent=parent, prop=f'{index}-aco', writable=False, device_class='connectivity'))
            self.add_converter(NumericConv(prefix+'delay_remaining', 'number', prop=f'{index}-acd', min=1, max=43200000, unit_of_measurement='ms'))
            self.add_converter(NumericConv(prefix+'deflector', 'number', prop=f'{index}-acdfltr', min=0, max=255))
            self.add_converter(PropBoolConv(prefix+'remote_control', 'switch', prop=f'{index}-acrc'))


class IlluminanceDevice(XDevice):
    def setup_converters(self):
        self.add_converter(PropConv('luminance', 'sensor', prop='luminance', writable=False,
                                    unit_of_measurement='lx', device_class='illuminance'))


class TemperatureHumidityDevice(XDevice):
    def setup_converters(self):
        self.add_converter(NumericConv('temperature', 'sensor', prop='t', writable=False,
            scale=0.01, min=-10, max=50, unit_of_measurement='°C', device_class='temperature'))
        self.add_converter(PropConv('humidity', 'sensor', prop='h', writable=False,
                                    unit_of_measurement='%', device_class='humidity'))


class TofDevice(ActionDevice):
    def setup_converters(self):
        super().setup_converters()
        self.add_converter(EventConv('handwave'))


class BathModeConv(PropMapConv):
    def encode(self, device, payload, value):
        if value == 'other':
            raise ValueError('bhm=5 is report-only, not a writable heating mode')
        super().encode(device, payload, value)


class BathHeaterDevice(XDevice):
    def setup_converters(self):
        self.add_converter(PropBoolConv('power', 'switch', prop='p'))
        self.add_converter(BathModeConv('heating_mode', 'select', prop='bhm', map={
            1: 'dry', 2: 'defog', 3: 'rapid_defog', 4: 'rapid_heat', 5: 'other'}))
        self.add_converter(PropConv('reported_heating_mode', 'sensor', prop='bhm', writable=False))
        self.add_converter(NumericConv('delay_off', 'number', prop='do', min=1, max=120, unit_of_measurement='min'))
        for attr, prop in [('ventilation', 've'), ('fan', 'fa'), ('heat', 'he')]:
            self.add_converter(NumericConv(attr, 'number', prop=prop, min=0, max=3))
        self.add_converter(NumericConv('target_temperature', 'number', prop='tgt', min=0, max=50, unit_of_measurement='°C'))
        # Bath heater t is in degrees Celsius; only type 136 scales t by 100.
        self.add_converter(PropConv('temperature', 'sensor', prop='t', writable=False,
                                    unit_of_measurement='°C', device_class='temperature'))
