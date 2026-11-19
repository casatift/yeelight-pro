"""Yeelight Pro LAN v2.4: CRLF framing, push state and correlated replies."""
import asyncio
import json
import logging

from .const import PID_WIFI_PANEL
from .device import XDevice, GatewayDevice, WifiPanelDevice
from .protocol import validate_command
from .cabinet import cabinet_endpoint_kind

_LOGGER = logging.getLogger(__name__)
MSG_SPLIT = b'\r\n'
TOPOLOGY_METHODS = {'gateway_post.topology', 'device_post.topology', 'getway_post.topology'}


class ProGateway:
    def __init__(self, host: str, **options):
        self.host = host
        self.port = options.get('port', 65443)
        self.pid = int(options.get('pid', 1))
        self.hass = options.get('hass')
        self.timeout = options.get('timeout', 5)
        # v2.4 p8: a full state snapshot is sent every ten minutes.
        self.keepalive = options.get('keepalive', 660)
        self.entry_id = options.get('entry_id')
        self.devices = {}
        self.setups = {}
        self.rooms = {}
        self.device = None
        self.reader = self.writer = self.main_task = None
        self.log = options.get('logger', _LOGGER)
        self._msgs = {}
        self._methods = {}
        self._sequence = 0
        self._connect_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._ready = asyncio.Event()

    @property
    def connected(self):
        return self.writer is not None and not self.writer.is_closing()

    @staticmethod
    def node_key(nid, nt=2):
        # IDs are unique within a node type, not across all node types.
        nid = int(nid)
        return nid if int(nt) == 2 else (int(nt), nid)

    def add_setup(self, domain, handler):
        self.setups[domain.rsplit('.', 1)[-1]] = handler

    async def setup_entity(self, domain, device, conv):
        if handler := self.setups.get(domain):
            handler(device, conv)

    async def add_device(self, device):
        device.hass = device.hass or self.hass
        key = self.node_key(device.id, device.nt) if isinstance(device.id, int) else device.id
        self.devices.setdefault(key, device)
        if self not in device.gateways:
            device.gateways.append(self)
        if len(device.gateways) == 1:
            await device.setup_entities()

    async def start(self):
        self.main_task = asyncio.create_task(self.run_forever())
        await self.ready()

    async def ready(self):
        await asyncio.wait_for(self._ready.wait(), self.timeout)

    async def stop(self, *args):
        if self.main_task:
            self.main_task.cancel()
            await asyncio.gather(self.main_task, return_exceptions=True)
            self.main_task = None
        await self._disconnect()
        for device in self.devices.values():
            if self in device.gateways:
                device.gateways.remove(self)

    async def _disconnect(self):
        writer, self.writer = self.writer, None
        self.reader = None
        self._ready.clear()
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ConnectionError):
                pass
        for future in self._msgs.values():
            if not future.done():
                future.set_exception(ConnectionError('Yeelight Pro connection closed'))
        for device in self.devices.values():
            device.prop['o'] = False
            device.observed_props.clear()
            device.update({'available': False, 'connection': False})

    async def run_forever(self):
        try:
            while True:
                try:
                    if not await self.connect():
                        await asyncio.sleep(30)
                        continue
                    await self.topology()
                    if self.pid != PID_WIFI_PANEL:
                        for method in ('node', 'group', 'room', 'scene'):
                            await self.send('gateway_get.' + method, wait_result=False, params={'id': 0})
                    while self.connected:
                        await self.readline()
                except (OSError, TimeoutError, ValueError, ConnectionError, asyncio.IncompleteReadError):
                    self.log.warning('Yeelight Pro connection lost; reconnecting')
                finally:
                    await self._disconnect()
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass

    async def connect(self):
        async with self._connect_lock:
            if self.connected:
                return True
            try:
                self.reader, self.writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port, limit=1024 * 1024), self.timeout)
            except (OSError, TimeoutError):
                return False
            self._ready.set()
            return True

    async def check_available(self):
        """Probe only; a config-flow check must not leave a TCP session open."""
        try:
            if not await self.connect():
                return ConnectionError('Cannot connect; enable LAN control in the Yeelight Pro app')
            await self.topology()
            line = await asyncio.wait_for(self.reader.readuntil(MSG_SPLIT), self.timeout)
            message = json.loads(line)
            if not isinstance(message, dict) or not isinstance(message.get('nodes'), list):
                return ValueError('Gateway did not return a topology message')
        except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError) as exc:
            return exc
        finally:
            await self._disconnect()
        return None

    async def readline(self):
        line = await asyncio.wait_for(self.reader.readuntil(MSG_SPLIT), self.keepalive)
        try:
            await self.on_message(line)
        except (json.JSONDecodeError, UnicodeError):
            self.log.warning('Ignoring malformed Yeelight Pro JSON frame')
        return line

    async def on_message(self, msg):
        dat = json.loads(msg) if isinstance(msg, (str, bytes)) else msg
        if not isinstance(dat, dict):
            return
        cmd, cid = dat.get('method'), dat.get('id')
        topology = cmd in TOPOLOGY_METHODS
        if topology and cid not in self._msgs:
            cid = next((key for key, value in self._methods.items() if value.endswith('_get.topology')), cid)

        if not self.device and (topology or (self.pid != PID_WIFI_PANEL and dat.get('nodes'))):
            nodes = dat.get('nodes') or []
            self.device = (WifiPanelDevice(nodes[0]) if self.pid == PID_WIFI_PANEL and nodes
                           else GatewayDevice(self))
            await self.add_device(self.device)
            self.device.update({'connection': self.connected})

        nodes = dat.get('nodes') or []
        if cmd == 'device_post.event':
            nodes = [dat['params']] if isinstance(dat.get('params'), dict) else nodes
        for node in nodes + (dat.get('groups') or []):
            if not isinstance(node, dict) or node.get('id') is None:
                continue
            nt = node.get('nt', 4 if 'groups' in dat else 2)
            key = self.node_key(node['id'], nt)
            device = self.devices.get(key)
            # The verified cabinet omits virtual endpoints from topology/get.node.
            # Admit only its exact product/channel/type/property contract.
            cabinet_push = (cmd == 'gateway_post.prop' and
                            cabinet_endpoint_kind(node, require_value=True))
            if topology or (not device and (cabinet_push or
                    (cmd is None and ('type' in node or 'pt' in node)))):
                if self.pid == PID_WIFI_PANEL:
                    device = self.device
                else:
                    device = await XDevice.from_node(self, dict(node, nt=nt))
            if not device:
                continue
            if cmd in ('gateway_post.event', 'device_post.event'):
                await device.event_fired(node)
            elif 'params' in node or 'o' in node or 'fv' in node:
                await device.prop_changed(node)

        for room in dat.get('rooms') or []:
            self.rooms[int(room['id'])] = room
        if isinstance(self.device, GatewayDevice):
            for scene in dat.get('scenes') or []:
                await self.device.add_scene(scene)

        if future := self._msgs.get(cid):
            if not future.done():
                if dat.get('result') == 'error':
                    future.set_exception(ValueError('Yeelight Pro gateway rejected the command'))
                else:
                    future.set_result(dat)

    async def send(self, method, wait_result=True, **kwargs):
        validate_command(method, kwargs)
        if self.main_task and not self.connected:
            raise ConnectionError('Yeelight Pro gateway is reconnecting; command not replayed')
        if not self.connected and not await self.connect():
            raise ConnectionError('Yeelight Pro gateway unavailable')
        self._sequence = self._sequence % 2_147_483_647 + 1
        cid = self._sequence
        future = asyncio.get_running_loop().create_future() if wait_result else None
        if future is not None:
            self._msgs[cid], self._methods[cid] = future, method
        dat = {'id': cid, 'method': method, **kwargs}
        try:
            async with self._write_lock:
                self.writer.write(json.dumps(dat, ensure_ascii=False).encode() + MSG_SPLIT)
                await self.writer.drain()
            if future is not None:
                return await asyncio.wait_for(future, self.timeout)
            return None
        finally:
            self._msgs.pop(cid, None)
            self._methods.pop(cid, None)

    async def topology(self, wait_result=False):
        method = 'device_get.topology' if self.pid == PID_WIFI_PANEL else 'gateway_get.topology'
        return await self.send(method, wait_result=wait_result)

    async def get_node(self, nid=0, wait_result=True):
        return await self.send('gateway_get.node', params={'id': nid}, wait_result=wait_result)

    async def get_group(self, gid=0, wait_result=True):
        return await self.send('gateway_get.group', params={'id': gid}, wait_result=wait_result)

    async def get_room(self, rid=0, wait_result=True):
        return await self.send('gateway_get.room', params={'id': rid}, wait_result=wait_result)

    async def get_scene(self, rid=0, wait_result=True):
        result = await self.send('gateway_get.scene', params={'id': rid}, wait_result=wait_result)
        return result.get('scenes', []) if result else result
