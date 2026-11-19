"""Protocol fixtures are synthetic. No real gateways or credentials are used."""
import asyncio
import json
from types import SimpleNamespace
import unittest

from custom_components.yeelight_pro.core.gateway import ProGateway
from custom_components.yeelight_pro.core.device import (
    XDevice, LightDevice, RelayDoubleDevice, SwitchPanelDevice, MotionDevice,
    ContactDevice, CoverDevice, ClimateDevice, TemperatureHumidityDevice,
    IlluminanceDevice, BathHeaterDevice, TofDevice,
    CabinetEndpointDevice,
)
from custom_components.yeelight_pro.core.protocol import validate_command, validate_property
from custom_components.yeelight_pro.cover import XCoverEntity
from custom_components.yeelight_pro.climate import XClimateEntity
from custom_components.yeelight_pro.light import XLightEntity
from custom_components.yeelight_pro.select import XSelectEntity
from custom_components.yeelight_pro.fan import XFanEntity
from custom_components.yeelight_pro.sensor import XSensorEntity
from custom_components.yeelight_pro.core.cabinet import CABINET_PID
from homeassistant.components.fan import FanEntityFeature
from homeassistant.components.light import ColorMode


class DeviceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.g = ProGateway('127.0.0.1')

    async def device(self, type, **kwargs):
        node = {'id': 101, 'nt': 2, 'n': 'Test', 'type': type, **kwargs}
        return await XDevice.from_node(self.g, node)

    async def test_all_documented_types(self):
        expected = {1: LightDevice, 2: LightDevice, 3: LightDevice, 4: LightDevice,
            6: CoverDevice, 7: RelayDoubleDevice, 10: ClimateDevice, 13: SwitchPanelDevice,
            14: LightDevice, 15: ClimateDevice, 128: XDevice, 129: MotionDevice,
            130: ContactDevice, 132: XDevice, 134: MotionDevice, 135: IlluminanceDevice,
            136: TemperatureHumidityDevice, 138: MotionDevice, 2049: BathHeaterDevice, 2052: TofDevice}
        for type, cls in expected.items():
            with self.subTest(type=type):
                self.g.devices.clear()
                self.assertIsInstance(await self.device(type), cls)

    async def test_missing_cids_motion(self):
        d = await self.device(129)
        self.assertEqual(d.decode({'params': {'mv': 0}})['motion'], False)
        self.assertIsNone(d.decode({'params': {'mv': None}})['motion'])

    async def test_sparse_params_and_metadata(self):
        d = await self.device(129, cids=[73], ch_num=1, roomid=23, pid=17000012)
        await d.prop_changed({'o': True, 'params': {'mv': 1, 'luminance': 630}})
        await d.prop_changed({'params': {'luminance': 900}})
        self.assertEqual(d.prop_params, {'mv': 1, 'luminance': 900})
        await XDevice.from_node(self.g, {'id': 101, 'nt': 2, 'n': 'Renamed'})
        self.assertEqual((d.name, d.cids, d.ch_num, d.roomid, d.pid), ('Renamed', [73], 1, 23, 17000012))

    async def test_pt_node_reply_and_uint64_split_products(self):
        for pt in (3, 129, 128, 2049, 2052, 4):
            nid = (pt << 56) | 42
            d = await XDevice.from_node(self.g, {'id': nid, 'nt': 2, 'pt': pt})
            self.assertEqual(d.id, nid)
        self.assertEqual(len(self.g.devices), 6)

    async def test_node_and_mesh_group_same_id(self):
        d = await self.device(3)
        group = await self.device(3, nt=4)
        self.assertIsNot(d, group)
        self.assertNotEqual(d.unique_id, group.unique_id)
        self.assertEqual(len(self.g.devices), 2)

    async def test_contact_full_state_and_tamper(self):
        d = await self.device(130)
        self.assertEqual(d.decode({'params': {'dc': 0, 'alm': 1}}), {'contact': True, 'tamper': True})
        self.assertEqual(d.decode_event({'value': 'contact.normal'}), {'tamper': False})
        self.assertEqual(d.decode_event({'value': 'contact.close'}), {'contact': False})

    async def test_relay_and_panel_late_channels(self):
        d = await self.device(13, ch_num=3, cids=[16, 16, 16])
        await d.prop_changed({'params': {'1-sp': False, '2-sp': True}})
        await d.prop_changed({'params': {'3-sp': False}})
        self.assertEqual(d.encode({'switch3': True}), {'set': {'3-sp': True}})
        self.assertEqual(d.prop_params['2-sp'], True)

    async def test_dream_curtain_and_readonly_calibration(self):
        d = await self.device(6, cids=[39])
        await d.prop_changed({'params': {'cp': 12, 'tp': 70, 'rs': 1, 'cra': 90, 'tra': 180, 'trs': 1}})
        self.assertNotIn('reverse', d.converters)
        self.assertEqual(d.encode({'target_angle': 180}), {'set': {'tra': 180}})
        self.assertEqual(d.encode({'motor': 'pause'}), {'action': {'motorAdjust': {'type': 'pause'}}})
        for attr in ('current_position', 'route_calibrated', 'current_angle', 'tilt_calibrated'):
            with self.subTest(attr=attr), self.assertRaises(ValueError):
                d.encode({attr: 0})
        entity = XCoverEntity(d, d.converters['motor'])
        entity.async_set_state({'position': 70, 'current_position': 12})
        self.assertEqual(entity.current_cover_position, 12)
        entity.async_set_state({'position': 90})
        self.assertEqual(entity.current_cover_position, 12)

    async def test_zoom_and_light_units(self):
        d = await self.device(14)
        self.assertIn('color_temp_kelvin', d.converters)
        self.assertEqual(d.encode({'angle': 200}), {'set': {'angle': 200}})
        self.assertEqual(d.encode({'transition': 2, 'brightness': 128}), {'duration': 2000, 'set': {'l': 50}})
        self.assertEqual(d.encode({'color_temp_kelvin': 2000}), {'set': {'ct': 2700}})
        d = await self.device(4, id=102, cids=[72])
        self.assertNotIn('color_temp_kelvin', d.converters)
        self.assertEqual(d.encode({'rgb_color': (255, 0, 0)}), {'set': {'c': 16711680}})

    async def test_temperature_scaling_depends_on_type(self):
        d = await self.device(136)
        self.assertAlmostEqual(d.decode({'params': {'t': 2356}})['temperature'], 23.56)
        d = await self.device(2049, id=102)
        self.assertEqual(d.decode({'params': {'t': 24}})['temperature'], 24)

    async def test_bath_mode_five_readonly(self):
        d = await self.device(2049)
        self.assertEqual(d.decode({'params': {'bhm': 5}})['heating_mode'], 'other')
        self.assertEqual(d.encode({'heating_mode': 'rapid_heat'}), {'set': {'bhm': 4}})
        with self.assertRaises(ValueError):
            d.encode({'heating_mode': 'other'})
        entity = XSelectEntity(d, d.converters['heating_mode'])
        self.assertNotIn('other', entity.options)
        entity.async_set_state({'heating_mode': 'other'})
        self.assertIsNone(entity.current_option)

    async def test_vrf_independent_channels(self):
        d = await self.device(10, ch_num=3)
        self.assertIn('climate_3', d.converters)
        self.assertEqual(d.encode({'ch2_is_on': True, 'ch2_target_temperature': 24}),
                         {'set': {'2-acp': True, '2-actt': 24}})
        data = d.decode({'params': {'1-acct': 21, '2-acct': 25, '2-actt': 26, '2-acm': 8, '2-acp': True}})
        entity = XClimateEntity(d, d.converters['climate_2'])
        entity.async_set_state(data)
        self.assertEqual(entity.native_current_temperature, 25)
        self.assertEqual(entity.native_target_temperature, 26)
        self.assertEqual(entity.hvac_mode, 'heat')
        self.assertNotIn('dry', entity.hvac_modes)

    async def test_toftime_and_knob_events(self):
        d = await self.device(2052)
        self.assertEqual(d.decode_event({'value': 'handwave'})['action'], 'handwave')
        d = await self.device(132, id=102)
        self.assertEqual(d.decode_event({'value': 'knob.spin', 'params': {'hold_spin': -5}})['hold_spin'], -5)

    async def test_ack_does_not_claim_physical_light_state(self):
        d = await self.device(3)
        d.hass = SimpleNamespace(loop=asyncio.get_running_loop())
        entity = XLightEntity(d, d.converters['light'])
        entity.async_set_state({'light': False})
        async def accepted(*args, **kwargs): return {'result': 'ok'}
        d.set_prop = accepted
        await entity.async_turn_on()
        self.assertFalse(entity.is_on)


def cabinet_node(channel, pt, params, **kwargs):
    # Synthetic physical ID; virtual endpoints follow the observed product layout.
    return {'id': (channel << 56) | 321, 'nt': 2, 'pid': CABINET_PID,
            'pt': pt, 'n': f'Fixture cabinet-{channel}', 'params': params, 'o': True, **kwargs}


class CabinetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.g = ProGateway('127.0.0.1')

    async def push(self, nodes):
        await self.g.on_message({'method': 'gateway_post.prop', 'nodes': nodes})

    async def test_verified_pushes_share_one_physical_device(self):
        await self.push([cabinet_node(1, 1, {'p': False}),
            cabinet_node(2, 2054, {'p': False, 'lv': 60, 'dir': 0}),
            cabinet_node(6, 2056, {'t': 2690}), cabinet_node(7, 2057, {'h': 2380})])
        devices = [d for d in self.g.devices.values() if isinstance(d, CabinetEndpointDevice)]
        self.assertEqual(len(devices), 4)
        entities = []
        for d in devices:
            conv = next(iter(d.converters.values()))
            cls = {'light': XLightEntity, 'fan': XFanEntity,
                   'temperature': XSensorEntity, 'humidity': XSensorEntity}[d.cabinet_kind]
            entities.append(cls(d, conv))
        self.assertEqual({next(iter(e.device_info['identifiers'])) for e in entities},
                         {('yeelight_pro', 'cabinet_321')})
        self.assertEqual({e.device_info['name'] for e in entities}, {'Fixture cabinet'})
        self.assertEqual(len({e.unique_id for e in entities}), 4)
        self.assertEqual(len({e.entity_id for e in entities}), 4)
        self.assertEqual(entities[0].supported_color_modes, {ColorMode.ONOFF})
        self.assertEqual(entities[1].supported_features,
                         FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF)
        self.assertEqual(set(devices[1].converters), {'fan'})

    async def test_temperature_humidity_scaling_and_readonly(self):
        await self.push([cabinet_node(6, 2056, {'t': 2690}), cabinet_node(7, 2057, {'h': 2380})])
        t, h = self.g.devices[(6 << 56) | 321], self.g.devices[(7 << 56) | 321]
        self.assertAlmostEqual(t.decode({'params': {'t': 2690}})['temperature'], 26.9)
        self.assertAlmostEqual(h.decode({'params': {'h': 2380}})['humidity'], 23.8)
        self.assertIsNone(h.decode({'params': {'h': None}})['humidity'])
        for d, attr in ((t, 'temperature'), (h, 'humidity')):
            with self.assertRaises(ValueError):
                d.encode({attr: 30})
        # Ordinary type 136 humidity semantics are unchanged.
        ordinary = await XDevice.from_node(self.g, {'id': 99, 'nt': 2, 'type': 136})
        self.assertEqual(ordinary.decode({'params': {'h': 42}})['humidity'], 42)

    async def test_unknown_products_channels_types_and_malformed_values_ignored(self):
        for node in [cabinet_node(2, 2054, {'p': True}, pid=999),
            cabinet_node(3, 2050, {}), cabinet_node(8, 130, {'dc': True}),
            cabinet_node(2, 1, {'p': True}), cabinet_node(6, 2056, {}),
            cabinet_node(2, 2054, {'p': 1}), cabinet_node(7, 2057, {'h': 10001}),
            cabinet_node(6, 2056, {'t': float('nan')})]:
            await self.push([node])
        self.assertFalse(any(isinstance(d, CabinetEndpointDevice) for d in self.g.devices.values()))

    async def test_sparse_push_updates_existing_endpoint_without_duplicate(self):
        await self.push([cabinet_node(2, 2054, {'p': False})])
        nid = (2 << 56) | 321
        d = self.g.devices[nid]
        await self.push([{'id': nid, 'nt': 2, 'params': {'p': True}}])
        self.assertIs(self.g.devices[nid], d)
        self.assertTrue(d.prop_params['p'])
        self.assertEqual(len([x for x in self.g.devices.values() if isinstance(x, CabinetEndpointDevice)]), 1)

    async def test_fan_uses_exact_virtual_id_and_only_power_no_optimism(self):
        await self.push([cabinet_node(2, 2054, {'p': False})])
        d = self.g.devices[(2 << 56) | 321]
        entity = XFanEntity(d, d.converters['fan'])
        entity.async_set_state({'fan': False})
        requests = []
        async def accepted(method, **kwargs):
            requests.append((method, kwargs))
            validate_command(method, kwargs)
            return {'result': 'ok'}
        self.g.send = accepted
        await entity.async_turn_on()
        self.assertFalse(entity.is_on)
        await entity.async_turn_off()
        self.assertEqual(requests, [('gateway_set.prop', {'nodes': [{'id': (2 << 56) | 321, 'nt': 2, 'set': {'p': True}}]}),
                                    ('gateway_set.prop', {'nodes': [{'id': (2 << 56) | 321, 'nt': 2, 'set': {'p': False}}]})])
        with self.assertRaises(ValueError):
            await entity.async_turn_on(percentage=50)
        self.assertEqual(len(requests), 2)

    async def test_reconnect_requires_new_primary_observation(self):
        await self.push([cabinet_node(2, 2054, {'p': False})])
        d = self.g.devices[(2 << 56) | 321]
        entity = XFanEntity(d, d.converters['fan'])
        self.g.writer = SimpleNamespace(is_closing=lambda:False)
        self.assertTrue(entity.available)
        self.g.writer = None
        await self.g._disconnect()
        self.g.writer = SimpleNamespace(is_closing=lambda:False)
        await self.push([{'id': d.id, 'nt': 2, 'o': True}])
        self.assertFalse(entity.available)
        await self.push([{'id': d.id, 'nt': 2, 'params': {'p': False}}])
        self.assertTrue(entity.available)


class ValidationTests(unittest.TestCase):
    def test_readonly_fields_never_written(self):
        for key in ('rs', 'trs', 'cra', 'cp', 'mv', 'dc', 't', 'o', '1-acct', '2-aco'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_property(key, 1)

    def test_ranges_and_enumerations(self):
        for key, value in [('bhm', 5), ('ct', 2000), ('l', 0), ('tra', 181), ('1-acm', 2), ('1-actt', 33), ('he', 4)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_property(key, value)
        for key, value in [('tra', 180), ('ct', 2700), ('bhm', 4), ('1-acf', 4), ('p', False)]:
            validate_property(key, value)

    def test_adjust_toggle_action_and_scenes(self):
        validate_command('gateway_set.prop', {'nodes': [{'id': 42, 'nt': 2,
            'adjust': {'ct': '-1/5'}, 'toggle': ['p'], 'duration': 500,
            'action': {'blink': {'repeat': 4, 'type': 'notify'}}}], 'scenes': [{'id': 19, 'delay': 200}]})
        with self.assertRaises(ValueError):
            validate_property('ct', '-1/0', relative=True)
        validate_command('gateway_set.event', {'nodes': [{'id': 42, 'nt': 2, 'value': 'panel.click', 'params': {'key': 3}}]})


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.clients = []
        self.respond = True
        async def handler(reader, writer):
            self.clients.append(writer)
            try:
                while line := await reader.readline():
                    msg = json.loads(line)
                    self.requests.append(msg)
                    if not self.respond:
                        continue
                    if msg['method'].endswith('get.topology'):
                        response = {'id': msg['id'], 'method': 'gateway_post.topology',
                                    'nodes': [{'id': 42, 'nt': 2, 'type': 3, 'n': 'Fixture'}]}
                    else:
                        response = {'id': msg['id'], 'result': 'ok'}
                    frame = json.dumps(response).encode() + b'\r\n'
                    writer.write(frame[:5]); await writer.drain()
                    await asyncio.sleep(0.001)
                    writer.write(frame[5:]); await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
        self.server = await asyncio.start_server(handler, '127.0.0.1', 0)
        port = self.server.sockets[0].getsockname()[1]
        self.g = ProGateway('127.0.0.1', port=port, timeout=0.3)

    async def asyncTearDown(self):
        await self.g.stop()
        for writer in self.clients:
            writer.close()
            await writer.wait_closed()
        self.server.close()
        await self.server.wait_closed()

    async def test_config_probe_closes_connection(self):
        self.assertIsNone(await self.g.check_available())
        self.assertFalse(self.g.connected)

    async def test_numeric_ids_and_concurrent_ack_correlation(self):
        await self.g.start()
        results = await asyncio.gather(*[self.g.send('gateway_set.prop', nodes=[{'id': 42, 'nt': 2, 'set': {'p': True}}]) for _ in range(5)])
        self.assertEqual(len({r['id'] for r in results}), 5)
        self.assertTrue(all(isinstance(r['id'], int) for r in self.requests))
        self.assertEqual(self.g._msgs, {})

    async def test_error_ack_is_not_success(self):
        await self.g.connect()
        future = asyncio.get_running_loop().create_future()
        self.g._msgs[99] = future
        await self.g.on_message({'id': 99, 'result': 'error'})
        with self.assertRaises(ValueError):
            await future
        self.g._msgs.clear()

    async def test_scenes_rooms_and_state_push(self):
        await self.g.on_message({'method': 'gateway_post.topology', 'nodes': []})
        await self.g.on_message({'scenes': [{'id': 51, 'n': 'Scene', 'params': {'state': 'active'}}],
                                 'rooms': [{'id': 61, 'n': 'Room'}]})
        self.assertIn('scene_51', self.g.device.converters)
        self.assertIn('scene_51_state', self.g.device.converters)
        self.assertEqual(self.g.rooms[61]['n'], 'Room')

    async def test_wifi_panel_topology_alias_and_shared_id(self):
        self.g.pid = 2
        await self.g.on_message({'method': 'getway_post.topology', 'nodes': [
            {'id': 71, 'nt': 2, 'type': 7}, {'id': 71, 'nt': 2, 'type': 128}]})
        await self.g.on_message({'method': 'device_post.prop', 'nodes': [{'id': 71, 'nt': 2, 'params': {'1-p': False}}]})
        self.assertEqual(len(self.g.devices), 1)
        self.assertFalse(self.g.device.prop_params['1-p'])

    async def test_disconnect_marks_offline_and_fails_pending(self):
        await self.g.on_message({'method': 'gateway_post.topology', 'nodes': [{'id': 42, 'nt': 2, 'type': 3}]})
        d = self.g.devices[42]
        await d.prop_changed({'o': True})
        future = asyncio.get_running_loop().create_future()
        self.g._msgs[99] = future
        await self.g._disconnect()
        self.assertFalse(d.online)
        with self.assertRaises(ConnectionError):
            await future
        self.g._msgs.clear()

    async def test_timeout_cleans_request_without_replaying_write(self):
        await self.g.start()
        self.respond = False
        with self.assertRaises(TimeoutError):
            await self.g.send('gateway_set.prop', nodes=[{'id': 42, 'nt': 2, 'set': {'p': True}}])
        writes = [r for r in self.requests if r['method'] == 'gateway_set.prop']
        self.assertEqual(len(writes), 1)
        self.assertEqual(self.g._msgs, {})

    async def test_unknown_internal_gateway_push_not_enumerated(self):
        await self.g.on_message({'method': 'gateway_post.topology', 'nodes': [{'id': 42, 'nt': 2, 'type': 3}]})
        await self.g.on_message({'method': 'gateway_post.prop', 'nodes': [
            {'id': 54321, 'nt': 2, 'pt': 1, 'o': True, 'params': {'p': True}},
            {'id': 54322, 'nt': 2, 'pt': 2056, 'o': True, 'params': {'t': 1234}}]})
        self.assertEqual(len(self.g.devices), 2)  # gateway plus topology-declared light

    async def test_reconnect_online_flag_does_not_refresh_cached_power(self):
        await self.g.connect()
        await self.g.on_message({'method': 'gateway_post.topology', 'nodes': [{'id': 42, 'nt': 2, 'type': 3}]})
        d = self.g.devices[42]
        await d.prop_changed({'o': True, 'params': {'p': True}})
        d.hass = SimpleNamespace(loop=asyncio.get_running_loop())
        entity = XLightEntity(d, d.converters['light'])
        self.assertTrue(entity.available)
        await self.g._disconnect()
        await self.g.connect()
        await d.prop_changed({'o': True})
        self.assertFalse(entity.available)
        await d.prop_changed({'params': {'p': False}})
        self.assertTrue(entity.available)


if __name__ == '__main__':
    unittest.main()
