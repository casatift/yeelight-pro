"""Enumerated settings defined by the Yeelight Pro LAN protocol."""
from homeassistant.components.select import SelectEntity
from homeassistant.core import callback

from . import XEntity, async_add_setuper


def setuper(add_entities):
    def setup(device, conv):
        if not (entity := device.entities.get(conv.attr)):
            entity = XSelectEntity(device, conv)
        if not entity.added:
            add_entities([entity])
    return setup


async def async_setup_entry(hass, entry, async_add_entities):
    await async_add_setuper(hass, entry, 'select', setuper(async_add_entities))


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    await async_add_setuper(hass, config or discovery_info, 'select', setuper(async_add_entities))


class XSelectEntity(XEntity, SelectEntity):
    def __init__(self, device, conv, option=None):
        super().__init__(device, conv, option)
        self._attr_options = [value for value in conv.map.values() if value != 'other']

    @callback
    def async_set_state(self, data):
        super().async_set_state(data)
        if self._name in data:
            value = data[self._name]
            self._attr_current_option = value if value in self._attr_options else None

    async def async_select_option(self, option):
        await self.device_send_props({self._name: option})
