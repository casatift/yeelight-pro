# Changelog

## 0.1.2 - 2026-11-19

- Fix E-series smart low-voltage cabinet (E 系列智能弱电箱) fan appearing off
  after a real power-on report. The on/off-only
  entity now reads boolean power directly instead of inheriting HA's default
  speed/preset-based power inference. Unknown remains unknown; no speed is
  fabricated. Adds a regression for app power pushes, sparse updates and state.

## 0.1.1 - 2026-11-19

- Add E-series smart low-voltage cabinet (E 系列智能弱电箱) support for product 17000064.
  Its internal endpoints are omitted from topology and all-node queries, but
  arrive in LAN property pushes. Admit only the verified product/channel/type
  combinations and primary property values; keep other unknown endpoints out.
- Group cabinet light, on/off fan, temperature and humidity into one physical
  HA device while retaining full virtual IDs for commands and entity identity.
- Scale cabinet temperature and humidity by 0.01, confirmed against its app.
  Sensors are read-only. Fan speed/direction and other unverified endpoints
  are intentionally not exposed. Power ACKs do not change reported state.
- Add cabinet regression tests for grouping, virtual command routing, sparse
  updates, invalid discovery data and availability after reconnect.

## 0.1.0 - 2026-11-19

Maintenance fork of `hasscc/yeelight-pro`, based on commit
`3a462a89f1e51d23f966bfc11abb2d0eb0accf92`.

- Align LAN device/property semantics with the supplied protocol v2.4 (2025-07-04).
- Preserve sparse properties/topology metadata and full uint64 virtual node IDs.
- Add type 138, 135, 136, 2049, 2052 handling and multi-channel type 10 climate.
- Support dream curtain angles with separate observed angle and target controls;
  treat `rs`/`trs` as read-only calibration status, never direction switches.
- Decode door state/tamper, proximity and TOF events; keep illuminance levels
  distinct from lux and type-specific temperature scaling.
- Add scene state, group/room responses and the documented command structures.
- Validate writable properties, ranges, actions and report-only bath mode 5.
- Fix motor action nesting, numeric request IDs, correlated errors, probe leaks,
  EOF reconnection and availability after disconnection. Do not retry writes.
- Use push observations for state instead of treating command ACK as actuation.
- Add synthetic protocol and TCP tests, tested with HA 2026.11.2/Python 3.14.
- Use native climate attributes and device-registry parent IDs; remove the
  deprecated config-flow connection-class constant.

This is protocol-level coverage. Untested hardware is not certified compatible.
