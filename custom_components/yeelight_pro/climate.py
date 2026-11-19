"""Support for climate."""
import logging

from homeassistant.core import callback
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    DOMAIN as ENTITY_DOMAIN,
    FAN_LOW,
    FAN_MEDIUM,
    FAN_HIGH,
)
from homeassistant.components.climate.const import (
    ATTR_CURRENT_HUMIDITY,
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_MODE,
    ATTR_FAN_MODE,
    DEFAULT_MAX_HUMIDITY,
    DEFAULT_MIN_HUMIDITY,
    HVACAction,
    HVACMode,
)
from homeassistant.helpers.restore_state import RestoreEntity

from . import (
    XDevice,
    XEntity,
    Converter,
    async_add_setuper,
)

_LOGGER = logging.getLogger(__name__)


def setuper(add_entities):
    def setup(device: XDevice, conv: Converter):
        if not (entity := device.entities.get(conv.attr)):
            entity = XClimateEntity(device, conv)
        if not entity.added:
            add_entities([entity])
    return setup


async def async_setup_entry(hass, config_entry, async_add_entities):
    await async_add_setuper(hass, config_entry, ENTITY_DOMAIN, setuper(async_add_entities))


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    await async_add_setuper(hass, config or discovery_info, ENTITY_DOMAIN, setuper(async_add_entities))


class XClimateEntity(XEntity, ClimateEntity, RestoreEntity):
    def __init__(self, device: XDevice, conv: Converter, option=None):
        super().__init__(device, conv, option)
        self.mode = None
        self.is_on = False
        self.prefix = '' if conv.channel == 1 else f'ch{conv.channel}_'

        # https://developers.home-assistant.io/docs/core/entity/climate#supported-features
        self._attr_hvac_modes = [
            HVACMode.OFF,
            HVACMode.HEAT,
            HVACMode.COOL,
            HVACMode.FAN_ONLY,
        ]
        self._attr_fan_modes = [
            FAN_LOW,
            FAN_MEDIUM,
            FAN_HIGH,
        ]

        self._attr_supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE
        self._attr_supported_features |= ClimateEntityFeature.FAN_MODE
        self._attr_supported_features |= ClimateEntityFeature.TURN_ON
        self._attr_supported_features |= ClimateEntityFeature.TURN_OFF

        self._attr_hvac_mode = HVACMode.OFF
        self._attr_fan_mode = None
        self._attr_native_temperature_unit = '°C'
        self._attr_min_temp = 16
        self._attr_max_temp = 32
        self._attr_target_temperature_step = 1

    @callback
    def async_set_state(self, data: dict):
        for name in ('is_on', 'mode'):
            if self.prefix+name in data:
                setattr(self, name, data[self.prefix+name])
        for name in ('current_temperature', 'target_temperature', 'fan_mode'):
            if self.prefix+name in data:
                attr = '_attr_'+name if name == 'fan_mode' else '_attr_native_'+name
                setattr(self, attr, data[self.prefix+name])
        self._attr_hvac_mode = self.mode if self.is_on else HVACMode.OFF
        

    @callback
    def async_restore_last_state(self, state: str, attrs: dict):
        pass

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        kwargs['target_temperature'] = kwargs['temperature']
        await self.send_channel(kwargs)

    async def async_set_hvac_mode(self, hvac_mode, **kwargs):
        """Set new target hvac mode."""
        if HVACMode.OFF == hvac_mode:
            kwargs['is_on'] = False
        else:
            kwargs['is_on'] = True
            kwargs['mode'] = hvac_mode

        await self.send_channel(kwargs)
    
    async def async_set_fan_mode(self, fan_mode, **kwargs):
        """Set new target fan mode."""
        kwargs['fan_mode'] = fan_mode
        await self.send_channel(kwargs)
    
    async def async_turn_on(self, **kwargs):
        """Turn the entity on."""
        kwargs['is_on'] = True
        await self.send_channel(kwargs)

    async def async_turn_off(self, **kwargs):
        """Turn the entity off."""
        kwargs['is_on'] = False
        await self.send_channel(kwargs)

    async def send_channel(self, kwargs):
        return await self.device_send_props({self.prefix+key: value for key, value in kwargs.items()})

    @property
    def available(self):
        channel = self.device.converters[self._name].channel
        online = self.device.prop_params.get(f'{channel}-aco')
        return super().available and online is not False
