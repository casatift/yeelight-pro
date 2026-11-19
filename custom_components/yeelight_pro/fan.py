"""On/off fan for the verified smart low-voltage cabinet extension."""
from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import callback

from . import XEntity, async_add_setuper


def setuper(add_entities):
    def setup(device, conv):
        entity = device.entities.get(conv.attr)
        if entity is None:
            entity = XFanEntity(device, conv)
        if not entity.added:
            add_entities([entity])
    return setup


async def async_setup_entry(hass, config_entry, async_add_entities):
    await async_add_setuper(hass, config_entry, 'fan', setuper(async_add_entities))


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    await async_add_setuper(hass, config or discovery_info, 'fan', setuper(async_add_entities))


class XFanEntity(XEntity, FanEntity):
    _attr_is_on = None
    _attr_supported_features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF

    @callback
    def async_set_state(self, data):
        super().async_set_state(data)
        if self._name in data:
            self._attr_is_on = data[self._name]

    async def async_turn_on(self, percentage=None, preset_mode=None, **kwargs):
        if percentage is not None or preset_mode is not None:
            raise ValueError('This cabinet fan supports on/off only')
        return await self.device_send_props({self._name: True})

    async def async_turn_off(self, **kwargs):
        return await self.device_send_props({self._name: False})
