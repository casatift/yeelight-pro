# LAN v2.4 implementation notes

Source: user-supplied **Yeelight Pro 局域网协议**, revision **2.4**, dated
**2025-07-04**, 38 pages. The document revision is different from the JSON wire
version `1.0`. The private PDF itself is not redistributed in this repository.

| Document pages | Implementation / limits |
| --- | --- |
| 2-5 | TCP 65443, UTF-8 JSON with CRLF framing. Enable LAN control in the app first. Numeric request IDs; ACKs correlate requests, not physical completion. |
| 6-9 | Metadata `ch_num`, `cids`, `roomid`, `type`/`pt` and uint64 IDs preserved. Mesh groups use `nt=4`; identifiers cannot collide with an `nt=2` node of the same numeric ID. Sparse property and topology pushes retain previously learned fields. |
| 10-19 | Node, group, room and scene queries; scene buttons and reported scene-state sensors. `send_command` accepts top-level `nodes` and `scenes`, including `set`, `adjust`, `toggle`, `action`, timing and virtual event messages. Room IDs are exposed as metadata; HA area membership is not forced. |
| 20 | Light power, brightness, RGB, 2700-6500 K, transition/delay in milliseconds and focus angle 0-255. CID 72 omits color temperature. No undocumented dynamic-light presets are invented. |
| 21 | `cp`/`cra` are observations; `tp`/`tra` are writable targets. Dream curtain target angle is a separate 0-180 degree number, not a percentage with an invented open/closed interpretation. `rs`/`trs` are read-only calibration sensors. Stop uses `action.motorAdjust`. |
| 21-26 | Double relays, multi-key panels, scenes and knob events. Multi-channel VRF controls keep per-channel data separate. Documented HVAC modes are 1/4/8 and fan values 1/2/4. |
| 26-29 | Motion, light **level** (not lux), door open/closed and tamper, temperature `/100` for type 136 only, humidity and type-138 measured lux/proximity. Missing values remain unknown. |
| 30-33 | Bath heater power, modes 1-4, delayed off in minutes, fan/ventilation/heat 0-3 and target temperature. Mode 5 is report-only: select shows unknown while raw reported mode sensor shows 5. TOF handwave. S-series panels, E-series heater, pendant and G60 sky light follow the gateway's already split virtual nodes; IDs are never truncated or rebuilt by guessing. |
| 34 | CIDs used for known capabilities such as dream curtains and measured illuminance. New-air/underfloor component names alone do not specify all enums, scales and bounds; no guessed standalone control is created for them. |
| 34-38 | Wi-Fi panel discovery uses a different discovery string (manual IP configuration here). Both `getway_post.topology` (document spelling) and corrected topology names are accepted. Shared relay/panel IDs produce one combined device; `device_set.prop` and nested key events are handled. |

The gateway may coalesce pushes at up to a two-second interval and sends a full
snapshot approximately every ten minutes (p8). Cached state is not proof of new
physical feedback. Disconnect makes entities unavailable and clears per-session
property observations. A later online flag alone does not make old primary
property values fresh. There is no automatic replay of writes after reconnect.

Events also emit `yeelight_pro_event` with `entry_id`, `node_id`, `nt`, `event` and
`params`, so repeated identical panel/knob/handwave events remain observable.

## E-series smart low-voltage cabinet / E 系列智能弱电箱 (0.1.1+)

This is an observed extension, not a claim that protocol v2.4 documents these
types. Product `17000064` reports the following virtual endpoints in
`gateway_post.prop`, although they are absent from topology and the all-node
query. App operations confirmed the two power transitions; the displayed
temperature/humidity confirmed both scales. The fan is on/off only in the app.

| High byte of virtual ID | Type | Property | HA capability |
| --- | --- | --- | --- |
| 1 | 1 | `p` boolean | On/off light |
| 2 | 2054 | `p` boolean | On/off fan |
| 6 | 2056 | `t` / 100 | Temperature, °C, read-only |
| 7 | 2057 | `h` / 100 | Humidity, %, read-only |

Discovery from pushes requires this product, endpoint number, type and a valid
primary value. Unknown product types, other cabinet endpoints, speed `lv` and
direction `dir` are not promoted to controls. The common low 56 bits identify
one physical cabinet device; full 64-bit endpoint IDs remain distinct for
entities and commands. This follows the virtual-ID layout observed for this
product, rather than truncating command targets or grouping unrelated products.

This extension has app-to-LAN observation evidence, not a complete firmware
specification. No extra control commands are inferred from raw property names.

## Migration and coexistence

Back up the old component and HA registries. Keep existing Matter/native
pairings until each new capability is validated. Existing mesh-device entity
unique IDs are retained; group identifiers include node type to avoid collisions.
The old erroneous `reverse` entity (mapped to `rs`) is no longer provided. Remove
its obsolete entity-registry entry only after checking consumers. No migration
automatically changes a device's direction or moves a physical curtain.

The integration domain remains `yeelight_pro`; do not install this fork alongside
another integration with that same domain. This fork is distinct from the newer
official `Yeelight/ha_yeelight_pro` project and does not claim official ownership.

## Verification scope

`tests/test_protocol_v24.py` runs entirely against synthetic devices and a local
fake TCP server. It covers property semantics, partial updates, multi-channel
isolation, command validation, fragmented frames, ACK errors, connection cleanup
and unavailable state. Tests are necessary but do not prove the behavior of every
real product/firmware. Real gateway/hardware observations belong in private
deployment records, not public fixtures.
