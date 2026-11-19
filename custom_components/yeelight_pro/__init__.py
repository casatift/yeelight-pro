"""The component."""
import json
import ast
import logging
import asyncio
import datetime
from homeassistant.core import HomeAssistant, State, callback
import voluptuous as vol
from homeassistant.const import (
    CONF_HOST,
    EVENT_HOMEASSISTANT_STOP,
    SERVICE_RELOAD,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import Entity, DeviceInfo
from homeassistant.helpers.reload import (
    async_integration_yaml_config,
    async_reload_integration_platforms,
)
from homeassistant.components import persistent_notification
import homeassistant.helpers.device_registry as dr
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.exceptions import ConfigEntryNotReady


from .core.const import *
from .core.gateway import ProGateway
from .core.device import XDevice, GatewayDevice, WifiPanelDevice
from .core.converters.base import Converter, PropConv

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = datetime.timedelta(seconds=60)

GATEWAY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_GATEWAYS): vol.All(cv.ensure_list, [GATEWAY_SCHEMA]),
            },
        ),
    },
    extra=vol.ALLOW_EXTRA,
)


def init_integration_data(hass):
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(CONF_GATEWAYS, {})


async def async_setup(hass: HomeAssistant, hass_config: dict):
    init_integration_data(hass)

    gws = hass_config.get(DOMAIN, {}).get(CONF_GATEWAYS) or []
    for gwc in gws:
        host = gwc.get(CONF_HOST)
        if not host:
            continue
        gtw = await get_gateway_from_config(hass, gwc)
        gwc['gateway'] = gtw
        hass.data[DOMAIN][CONF_GATEWAYS][host] = gtw

        await asyncio.gather(
            *[
                async_load_platform(hass, domain, DOMAIN, gwc, gwc)
                for domain in SUPPORTED_DOMAINS
            ]
        )
        await gtw.start()

    ComponentServices(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    init_integration_data(hass)
    await hass.config_entries.async_forward_entry_setups(entry, SUPPORTED_DOMAINS)

    if gtw := await get_gateway_from_config(hass, entry):
        try:
            await gtw.start()
        except (ConnectionError, TimeoutError) as exc:
            await gtw.stop()
            raise ConfigEntryNotReady('Cannot connect to Yeelight Pro LAN gateway') from exc

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, gtw.stop)
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, SUPPORTED_DOMAINS)
    if unload_ok:
        gtw = hass.data[DOMAIN][CONF_GATEWAYS].pop(entry.entry_id, None)
        if gtw:
            await gtw.stop()
    return unload_ok


async def async_remove_config_entry_device(hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry):
    """Supported from Hass v2022.3"""
    dr.async_get(hass).async_remove_device(device.id)


async def async_add_setuper(hass: HomeAssistant, config, domain, setuper):
    gtw = await get_gateway_from_config(hass, config)
    if isinstance(gtw, ProGateway):
        gtw.add_setup(domain, setuper)


async def get_gateway_from_config(hass, config, renew=False):
    if isinstance(config, ConfigEntry):
        cfg = {
            **config.data,
            **config.options,
            'hass': hass,
            'entry_id': config.entry_id,
            'config_entry': config,
        }
    else:
        cfg = {
            **config,
            'hass': hass,
            'entry_id': config.get(CONF_HOST),
        }
    if not (eid := cfg.get('entry_id')):
        _LOGGER.warning('Config invalid: %s', cfg)
        return None
    host = cfg.pop(CONF_HOST, None)
    if renew:
        return ProGateway(host, **cfg)
    gtw = hass.data[DOMAIN][CONF_GATEWAYS].get(eid)
    if not gtw:
        gtw = ProGateway(host, **cfg)
        hass.data[DOMAIN][CONF_GATEWAYS][eid] = gtw
    return gtw


async def async_reload_integration_config(hass, config):
    hass.data[DOMAIN]['config'] = config
    return config


class ComponentServices:
    def __init__(self, hass: HomeAssistant):
        self.hass = hass

        async_register_admin_service(
            hass, DOMAIN, SERVICE_RELOAD, self.handle_reload_config,
        )

        async_register_admin_service(
            hass, DOMAIN, 'send_command', self.async_send_command,
            schema=vol.Schema({
                vol.Required(CONF_HOST): cv.string,
                vol.Required('method'): cv.string,
                vol.Optional('params', default=None): vol.Any(dict, None),
                vol.Optional('nodes'): list,
                vol.Optional('scenes'): list,
                vol.Optional('throw', default=False): cv.boolean,
            }),
        )

        async_register_admin_service(
            hass, DOMAIN, 'mock_incoming_message', self.async_mock_incoming_message,
            schema=vol.Schema({
                vol.Optional(CONF_HOST): cv.string,
                vol.Required('message'): cv.string,
            }),
        )


    async def handle_reload_config(self, call):
        config = await async_integration_yaml_config(self.hass, DOMAIN)
        if not config or DOMAIN not in config:
            return
        await async_reload_integration_config(self.hass, config.get(DOMAIN) or {})
        current_entries = self.hass.config_entries.async_entries(DOMAIN)
        reload_tasks = [
            self.hass.config_entries.async_reload(entry.entry_id)
            for entry in current_entries
        ]
        await asyncio.gather(*reload_tasks)
        await async_reload_integration_platforms(self.hass, DOMAIN, SUPPORTED_DOMAINS)

    async def async_send_command(self, call):
        dat = call.data or {}
        gip = dat.get(CONF_HOST)
        gtw = None
        for g in self.hass.data[DOMAIN][CONF_GATEWAYS].values():
            if not isinstance(g, ProGateway):
                continue
            if g.host == gip or not gip:
                gtw = g
                break
        if not gtw:
            _LOGGER.warning('Gateway %s not found.', gip)
            return False
        method = dat['method']
        params = dat.get('params')
        payload = {key: dat[key] for key in ('nodes', 'scenes') if key in dat}
        if params is not None:
            payload['params'] = params
        rdt = await gtw.send(method, wait_result=True, **payload)
        if dat.get('throw', True):
            persistent_notification.async_create(
                self.hass, f'{rdt}', 'Yeelight Pro command result', f'{DOMAIN}-debug',
            )
        self.hass.bus.async_fire(f'{DOMAIN}.send_command', {
            'host': gip,
            'method': method,
            'params': params,
            'result': rdt,
        })
        return rdt

    async def async_mock_incoming_message(self, call):
        dat = call.data or {}
        gip = dat.get(CONF_HOST)
        gtw = None
        for g in self.hass.data[DOMAIN][CONF_GATEWAYS].values():
            if not isinstance(g, ProGateway):
                continue
            if g.host == gip or not gip:
                gtw = g
                break
        if not gtw:
            _LOGGER.warning('Gateway %s not found.', gip)
            return False
        message = dat['message']

        # 兼容python字典打印复制
        try:
            msg = json.loads(message)
        except json.JSONDecodeError:
            try:
                msg = ast.literal_eval(message)
            except (ValueError, SyntaxError):
                msg = None
                
        if not isinstance(msg, dict):
            title = 'Yeelight Pro mock incoming message'
            err_info = f'❌ Format error: {message}\n\n'
            err_info += '''✅JSON: {"id": 8218, "method": "gateway_post.event", "nodes": [{"params": {}, "value": "motion.false", "id": 301809111, "nt": 2}]}\n'''
            err_info += '''✅PYTHON: {'id': 8218, 'method': 'gateway_post.event', 'nodes': [{'params': {}, 'value': 'motion.false', 'id': 301809111, 'nt': 2}]}\n'''
            persistent_notification.async_create(
                self.hass, err_info, title=title, notification_id=f'{DOMAIN}-debug',
            )
            return False
        message = json.dumps(msg)
        _LOGGER.info('Mock message: %s', message)
        await gtw.on_message(message.encode('utf-8'))
        
class XEntity(Entity):
    added = False
    _attr_should_poll = False

    def __init__(self, device: XDevice, conv: Converter, option=None):
        self.device = device
        self.hass = device.hass
        self._name = conv.attr
        self._option = option or {}
        self._attr_name = f'{device.name} {conv.attr}'.strip()
        identity = device.id if device.nt in (2, -1) else f'{device.nt}-{device.id}'
        self._attr_unique_id = f'{identity}-{conv.attr}'
        self.entity_id = device.entity_id(conv)
        self._attr_icon = self._option.get('icon')
        self._attr_entity_picture = self._option.get('picture')
        self._attr_device_class = self._option.get('class') or conv.device_class
        self._attr_native_unit_of_measurement = conv.unit_of_measurement
        self._attr_entity_category = self._option.get('category')
        self._attr_translation_key = self._option.get('translation_key', conv.attr)

        via_device = None
        if not isinstance(device, (GatewayDevice, WifiPanelDevice)):
            via_device = (DOMAIN, str(device.gateway.device.id if device.gateway.device else device.gateway.host))
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(device.id) if device.nt in (2, -1) else f'{device.nt}-{device.id}')},
            name=device.name,
            model=str(device.pid or device.type or ''),
            via_device=via_device,
            sw_version=device.firmware_version,
            manufacturer=DEFAULT_NAME,
        )
        self._attr_extra_state_attributes = {}
        if device.roomid is not None:
            self._attr_extra_state_attributes['yeelight_room_id'] = str(device.roomid)
        self._vars = {}
        self.subscribed_attrs = device.subscribe_attrs(conv)
        device.entities[conv.attr] = self

    @property
    def available(self):
        gateway = self.device.gateway
        if not gateway or not gateway.connected:
            return False
        if isinstance(self.device, GatewayDevice):
            return not self._name.endswith('_state') or self._name in self.device.observed_props
        if not isinstance(self.device, WifiPanelDevice) and self.device.online is not True:
            return False
        conv = self.device.converters[self._name]
        prop = conv.prop if isinstance(conv, PropConv) else None
        if self._name == 'motor':
            prop = 'cp'
        if conv.domain == 'climate':
            prop = f'{conv.channel}-acp'
        return not prop or prop in self.device.observed_props

    async def async_added_to_hass(self):
        """Run when entity about to be added to hass."""
        if hasattr(self, 'async_get_last_state'):
            state: State = await self.async_get_last_state()
            if state:
                self.async_restore_last_state(state.state, state.attributes)

        self.added = True
        await super().async_added_to_hass()

    @callback
    def async_restore_last_state(self, state: str, attrs: dict):
        """Restore previous state."""
        self._attr_state = state

    @callback
    def async_set_state(self, data: dict):
        """Handle state update from gateway."""
        if self._name in data:
            self._attr_state = data[self._name]
        for k in self.subscribed_attrs:
            if k not in data:
                continue
            self._attr_extra_state_attributes[k] = data[k]
        _LOGGER.info('%s: State changed: %s', self.entity_id, data)

    async def device_send_props(self, value: dict):
        payload = self.device.encode(value)
        if not payload:
            return False
        return await self.device.set_prop(**payload)
