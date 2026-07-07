# Smart-home & mesh integrations (Matter, Z-Wave, Thread, Wi-Fi)

> **Opt-in and off by default.** stavau makes no network I/O unless you
> explicitly configure this integration (invariant I3). When enabled, it talks
> only to a broker on **your local network** — no cloud, no telemetry.

## Why the boundary, not the radios

stavau's job is fine-grained proximity of *your personal device* to *your PC*.
Matter, Z-Wave and Thread are the opposite: **home-automation control networks
for stationary devices** (lights, plugs, locks, sensors) over Wi-Fi/Thread —
and Matter uses Bluetooth only for one-time commissioning. A smart bulb does not
move with you, so it cannot be a proximity trust device.

Embedding a Matter controller (IPv6 multicast, the Matter SDK, a Thread border
router) or a Z-Wave stack (a USB stick + zwave-js) would turn a focused,
auditable, privacy-first locker into a home-automation hub — which is exactly
what **Home Assistant** already is. So stavau integrates at the **ecosystem
boundary**: it speaks **MQTT** to Home Assistant (or any MQTT source), and HA
bridges Matter, Z-Wave, Thread, Wi-Fi presence and everything else. One small,
optional, local-only dependency (`paho-mqtt`) reaches the whole mesh world.

```
 Matter / Z-Wave / Thread / Wi-Fi devices
        │  (occupancy sensors, person home/away, ...)
        ▼
   Home Assistant  ── MQTT ──►  stavau  (presence in)
   Home Assistant  ◄─ MQTT ──   stavau  (lock/unlock events out)
```

## What you get, both directions

- **Presence in** — consume an external presence signal as a proximity source.
  A Matter/Z-Wave occupancy sensor, an mmWave presence sensor, or HA's
  "person: home/away" can drive the lock. Set the strategy to
  `external_presence`. Fail-safe: *present* keeps the screen unlocked; *absent*,
  *unknown*, or a lost broker connection all let the normal staleness lock fire
  (absence of evidence is never treated as presence — invariant I1).
- **Actions out** — on lock/unlock, stavau publishes a small JSON event to an
  MQTT topic, so an HA automation can run a leaving/arriving routine (dim the
  desk light, arm a scene, ...). A broker problem never affects locking.

## Setup (MQTT via Home Assistant)

1. Install the extra: `pip install "stavau[integration]"`.
2. Configure the broker and topics (see `stavau setup` / the GUI settings, or
   the config file): `integration_mqtt_host`, `integration_mqtt_port`
   (default 1883), `integration_mqtt_username`, and either/both of
   `integration_presence_topic` (presence in) and `integration_action_topic`
   (events out). The MQTT password is read from the `STAVAU_MQTT_PASSWORD`
   environment variable, never stored in the config file.
3. Presence values counted as "present" are configurable
   (`integration_present_values`, default `on,home,present,occupied,true,1`).
4. For presence in, select the `external_presence` strategy.

### Home Assistant example

Publish a presence signal HA already computes (e.g. an occupancy sensor or a
person entity) to an MQTT topic, and point `integration_presence_topic` at it.
For events out, add an MQTT-trigger automation on your `integration_action_topic`
that runs your routine when the payload's `event` is `locked` / `unlocked`.

## Security & privacy notes

- Everything stays on your LAN; there is no stavau cloud and no telemetry.
- Presence *in* is advisory and fail-safe: it can keep the screen unlocked only
  while it positively reports presence; it can never prevent a lock.
- If you also enable auto-unlock, remember its own constraints
  (Linux-only, paired device, stavau's own lock only — see
  [threat-model.md](threat-model.md) T9). An external presence source feeding
  auto-unlock inherits the relay-attack considerations of whatever sensor you
  trust.

## Future: a direct Matter presence source

The presence-source seam (`core/integration.py::PresenceBackend`) is generic:
a `MatterOccupancyBackend` that talks to `python-matter-server` and reads a
Matter occupancy sensor directly could plug in later behind the same
`ExternalPresenceSource`, for users who prefer not to run Home Assistant. It is
a large subsystem (Python 3.12, IPv6 multicast, the Matter SDK) and is
deliberately deferred — going through Home Assistant already covers Matter today.
