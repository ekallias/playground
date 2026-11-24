# Handover: Home Assistant setup for the two bedroom plug overrides

For an agent with access to the Home Assistant (HA) instance. Written 2026-11-24. Background manual: [README.md](README.md).

## 1. Goal

Two KNX-switched sockets in the bedroom ("Schlafzimmer Steckdose 1" and "2") should be able to power a Zigbee lamp permanently. While an **override** is on:

- The KNX actuator locks the relay closed, so the Zigbee lamp has constant power. This is done in KNX, not in HA.
- The wall buttons still send their normal group address telegrams.
- HA listens to those telegrams and toggles the Zigbee lamp instead.

Your job is the HA side only: KNX entities, automations, validation. The KNX/ETS side is configured separately by the user.

## 2. Facts you can rely on

| | Steckdose 1 | Steckdose 2 |
| --- | --- | --- |
| Button switch GA (events to listen to) | `1/0/54` | `1/0/55` |
| Override (lock) GA, written by HA | `1/6/54` | `1/6/55` |
| Lock polarity | **1 = locked (override on), 0 = released** | same |
| Zigbee lamp entity | **unknown, ask the user** | **unknown, ask the user** |

- All GAs are 1-bit (DPT 1). The lock GAs are DPT 1.003, which is the same bit on the wire.
- **Button telegrams carry no usable value while the lock is on.** The buttons send "toggle" based on a status that never changes while locked, so they send the same value on every press. Always trigger on **any** `GroupValueWrite` to the button GA and never on its value.
- The lock object **cannot be read** from the bus. HA is the source of truth for the override state.
- After a KNX bus power failure or an ETS download of the actuator, the lock resets to 0 and HA is not told. Therefore HA must re-send `1` periodically (writing 1 to an already locked actuator does nothing).
- If HA stops, the lock stays on until `0` is written. This is accepted.
- The user's KNX config is split: an error reported `knx.yaml, line 136`, so check whether `configuration.yaml` has `knx: !include knx.yaml`. Entities in that file sit directly under top-level keys such as `switch:` and `event:`, without a `knx:` prefix. Use whatever layout is actually in place.

## 3. Prerequisites to confirm before enabling anything

Ask the user to confirm these (they are ETS tasks):

- [ ] The Gira actuator and the MDT buttons are programmed for the bedroom override (lock objects linked to `1/6/54` and `1/6/55`, end of lock "switch off").
- [ ] The Zigbee lamp entity ids for both plugs, and that each lamp's power-on behaviour is "off" or "previous".

You can build all configuration without these. **Do not turn an override on** (`switch.turn_on`, a start automation firing, or a test write of `1` to `1/6/54` or `1/6/55`) until the user confirms the first item. Locking a socket powers whatever is plugged in.

## 4. Tasks

### 4.1 Inspect first

1. Make a backup or snapshot of the files you will change.
2. Find where KNX is configured and the HA version (`Developer tools → About`). Automation syntax below uses the 2024.10+ format (`triggers:` / `trigger:` / `actions:` / `action:`). On an older version use `trigger:` with `platform:` and `service:`.
3. List existing KNX entities and automations that touch `1/0/54`, `1/0/55`, `1/6/54` or `1/6/55`. Do not create duplicates.
4. **Fix invalid config first.** A configuration warning reports that `sync_state: false` is invalid for two `switch` entries in `knx.yaml` (lines 136 and 140; these belong to the living room override). The `switch` platform does not allow `sync_state: false`. In those two entries, delete `sync_state` **and** `state_address`, leaving only `name` and `address`. Change nothing else in them, and report the change to the user.

### 4.2 KNX entities

Add two switches (override state) and the two event addresses. Do not use `state_address` or `sync_state`.

```yaml
switch:
  - name: "Schlafzimmer Steckdose 1 Override"
    address: "1/6/54"
  - name: "Schlafzimmer Steckdose 2 Override"
    address: "1/6/55"

event:
  - address:
      - "1/0/54"
      - "1/0/55"
    type: "switch"
```

If `event:` already exists, add the two addresses to the matching entry rather than creating a second one. If KNX lives under `knx:` in `configuration.yaml`, nest the keys there.

Expected entity ids: `switch.schlafzimmer_steckdose_1_override` and `switch.schlafzimmer_steckdose_2_override`. Verify them in Developer tools → States and use the real ids below.

### 4.3 Automations

Create these four. Replace the lamp entity ids with the ones from the user.

```yaml
- alias: "Schlafzimmer Steckdose 1 -> Zigbee lamp"
  mode: queued
  max: 5
  triggers:
    - trigger: event
      event_type: knx_event
      event_data:
        destination: "1/0/54"
        telegram_type: GroupValueWrite
  conditions:
    - condition: state
      entity_id: switch.schlafzimmer_steckdose_1_override
      state: "on"
  actions:
    - action: light.toggle
      target:
        entity_id: light.TODO_lamp_steckdose_1

- alias: "Schlafzimmer Steckdose 2 -> Zigbee lamp"
  mode: queued
  max: 5
  triggers:
    - trigger: event
      event_type: knx_event
      event_data:
        destination: "1/0/55"
        telegram_type: GroupValueWrite
  conditions:
    - condition: state
      entity_id: switch.schlafzimmer_steckdose_2_override
      state: "on"
  actions:
    - action: light.toggle
      target:
        entity_id: light.TODO_lamp_steckdose_2

- alias: "Schlafzimmer overrides on"
  triggers:
    - trigger: homeassistant
      event: start
    - trigger: time_pattern
      minutes: "/15"
  actions:
    - action: switch.turn_on
      target:
        entity_id:
          - switch.schlafzimmer_steckdose_1_override
          - switch.schlafzimmer_steckdose_2_override
```

- The two toggle automations do not filter on the telegram value. That is intentional (section 2).
- If the user wants only one plug converted, include only that socket in each automation.
- **Create "Schlafzimmer overrides on" last, and leave it disabled** until the user confirms the ETS prerequisite (section 3). Enable it only after their confirmation.
- Optional, only if the user asks: the lamp could start an animation instead of `light.toggle` (a `script.turn_on` action).

### 4.4 Validate and apply

1. Run the configuration check (Developer tools → YAML → Check configuration, or `ha core check`). It must pass with no KNX warnings.
2. Reload KNX (YAML reload) or restart HA if the integration requires it.
3. Confirm both switch entities exist and the KNX integration shows no errors in the log.

## 5. Acceptance tests

Run these only as far as the prerequisites allow.

| # | Test | Expected |
| --- | --- | --- |
| 1 | Config check and log | No errors or warnings about `knx`, `sync_state` or the new entities |
| 2 | Developer tools → Events, listen to `knx_event`; press each bedroom button | An event with `destination: 1/0/54` or `1/0/55` for each press |
| 3 | (After confirmation) turn override 1 on | The actuator relay for Steckdose 1 closes and the lamp powers up |
| 4 | Override 1 on, press a button for `1/0/54` | The lamp toggles on every press |
| 5 | Override 1 off | The relay opens, the buttons control the socket, the lamp automation does nothing |
| 6 | Repeat 3 to 5 for Steckdose 2 and `1/0/55` | Same behaviour |
| 7 | Enable "Schlafzimmer overrides on" and restart HA | Both overrides come on again after start |

After tests, leave both overrides in the state the user wants.

## 6. Do not

- Do not use `sync_state: false` or `state_address` on the override switches.
- Do not change KNX group addresses, ETS projects or unrelated entities and automations.
- Do not enable an override without the user's confirmation.
- Do not store secrets in the files you write.

## 7. Report back

- The files changed and a diff or summary, including the `sync_state` fix.
- Final entity ids of the two switches and the lamps used.
- Results of the acceptance tests, noting what was skipped and why.
- Anything unclear in the existing config (for example automations that already use these GAs).
