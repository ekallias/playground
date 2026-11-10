# Override setup: Wohnzimmer Steckdose 1 (GA `1/0/33`)

Project: "Kreittmayrstr 18, 3. OG links" (ETS 6.4). Written 2026-11-10 for the general manual in [README.md](README.md).

Goal: while the override is on, the Gira relay stays closed, the three wall buttons and the Siemens touch panel keep sending `1/0/33`, Home Assistant (HA) uses those telegrams to control a Zigbee lamp, and the button LEDs do not show the socket as on. With the lock at 0 the installation behaves exactly as before.

## 1. What exists today (read from the project)

| Item | Value |
| --- | --- |
| Actuator | D3 16-gang Schaltaktor (1.1.3), channel "Wohnzimmer Steckdose 1" |
| Switch / feedback / scene objects | 382 / 383 / 388 |
| Switch GAs on object 382 | `1/0/33`, `1/0/0` (Gesamte Wohnung) |
| Feedback GA | `1/3/33` |
| Scene GA | `1/7/10` (Wohnen Essen) |
| Lock | **disabling function is off**, so no lock object exists yet |

Controllers and indicators of `1/0/33`:

| Device | Object | Role | Stays or changes |
| --- | --- | --- | --- |
| 1.1.30 Taster Wohnzimmer Tür (Lite 4-fold) | 30 | PB4 short: toggle `1/0/33` | stays |
| 1.1.30 | 65 | LED 4 on `1/3/33` | **change to `1/5/33`** |
| 1.1.30 | 50 | Logic 1 input A on `1/3/33` (toggle status group) | stays |
| 1.1.31 Taster Wohnzimmer Couch (Lite 2-fold) | 12 | PB2 long press: toggle `1/0/33` | stays |
| 1.1.31 | 67 | LED 2 Priority on `1/3/33` | **change to `1/5/33`** |
| 1.1.32 Taster Wohnzimmer Links (Lite 1-fold) | 10, 11 | PB2: toggle `1/0/33`, status `1/3/33` | stays |
| 1.1.32 | 63 | LED 2 on `1/3/33` | **change to `1/5/33`** |
| 1.1.17 Siemens Touch Control TC5 | 80, 82 | Page 2 "Fenster": switching `1/0/33`, status `1/3/33` | stays (optional: object 82 to `1/5/33`) |

## 2. New group addresses

| GA | Name | DPT | Linked objects |
| --- | --- | --- | --- |
| `1/6/33` | Wohnzimmer Steckdose 1 Lock | 1.003 | Gira lock object (step 3), 1.1.32 logic input B (step 4), HA switch |
| `1/5/33` | Wohnzimmer Steckdose 1 LED | 1.001 | 1.1.32 logic 1 output, three LED objects (step 4) |

Both addresses are free in your project. Convention: `1/6/x` holds the locks, `1/5/x` the logic outputs, and the last number matches the socket as in your other ranges.

Lock polarity used throughout: **1 = locked, 0 = released**.

## 3. Gira actuator (1.1.3), channel "Wohnzimmer Steckdose 1"

ETS shows English parameter names:

- [ ] **disabling function:** on
- [ ] **Polarity of the disabling object:** `0 = enabled, 1 = disabled` (this is the default; 1 = locked)
- [ ] **Beginning of the disabling function:** switch on (default)
- [ ] **Acknowledgment:** off (default)
- [ ] **End of the disabling function:** **switch off** (the default "setting tracked state" would apply the last button state sent during the lock)
- [ ] Link the new *Switching ... Disabling* object of this channel to `1/6/33`. By the pattern of your other channels it should be object 390; confirm the number in ETS.

Notes from the Gira datasheet:
- The lock also overrides the central off `1/0/0` and scene `1/7/10`.
- After a bus power failure or an ETS download of this device the lock is inactive. HA's periodic re-send restores it.
- The relay can still be operated by hand at the actuator.
- There is no timeout. If HA stops while the lock is on, the lock stays on.

## 4. MDT buttons

### 4.1 Logic on 1.1.32 (Taster Wohnzimmer Links, 1-fold Lite)

- [ ] **Setting Logic 1:** And
- [ ] **Logic object 1 A (external):** normally active, with preallocation 0
- [ ] **Logic object 1 B (external):** inverted active, with preallocation 0
- [ ] **Object type:** Switch (1 bit), output not inverted, send on change of output
- [ ] Link object 50 (Logic Input 1 A) to `1/3/33`
- [ ] Link object 51 (Logic Input 1 B) to `1/6/33`
- [ ] Link object 52 (Logic Output 1) to `1/5/33`

Result: `1/5/33` = socket feedback AND NOT lock, so it follows the socket normally and is 0 while locked.

### 4.2 LED objects

Remove `1/3/33` from these three objects and link `1/5/33` instead:

- [ ] 1.1.30 object 65 (LED 4)
- [ ] 1.1.31 object 67 (LED 2 Priority)
- [ ] 1.1.32 object 63 (LED 2)

Do **not** change the "Status for toggle" objects (1.1.32 object 11, 1.1.30 Logic 1 input A). They keep the real feedback.

### 4.3 Optional

- [ ] 1.1.17 TC5 object 82 (Page 2-Fenster status): replace `1/3/33` with `1/5/33` so the panel does not show the socket as on during the override.
- [ ] If an LED shows the wrong state after a bus reset, set "Behaviour of LEDs on bus power return" to "request LED objects" on that button.

## 5. Test in ETS (before touching HA)

Download 1.1.3, 1.1.30, 1.1.31 and 1.1.32. In the group monitor:

- [ ] **Baseline.** Press the Tür button PB4: relay toggles, `1/0/33` and `1/3/33` show telegrams, `1/5/33` follows the socket, the three LEDs follow.
- [ ] **Lock on.** Write `1` to `1/6/33`. The relay closes (or stays closed), `1/3/33` = 1, `1/5/33` = 0, the three LEDs go dark.
- [ ] **Buttons ignored.** Press each of the three buttons. `1/0/33` telegrams appear, the relay stays on.
- [ ] **Central off.** Send `0` to `1/0/0`. The Wohnzimmer Steckdose 1 relay stays on.
- [ ] **Lock off.** Write `0` to `1/6/33`. The relay switches off, the LEDs stay off, and the buttons control the socket again.
- [ ] **Bus reset.** Lock on, then cycle bus power. The lock is inactive afterwards, the socket and LEDs behave as in the baseline.

## 6. Home Assistant

```yaml
knx:
  switch:
    - name: "Wohnzimmer Steckdose 1 Override"
      address: "1/6/33"
      state_address: "1/6/33"
      sync_state: false
  event:
    - address:
        - "1/0/33"
      type: "switch"
```

```yaml
- alias: "Wohnzimmer Steckdose 1 -> Zigbee lamp"
  triggers:
    - trigger: event
      event_type: knx_event
      event_data:
        destination: "1/0/33"
        telegram_type: GroupValueWrite
  conditions:
    - condition: state
      entity_id: switch.wohnzimmer_steckdose_1_override
      state: "on"
  actions:
    - action: light.toggle
      target:
        entity_id: light.your_zigbee_bulb

- alias: "Wohnzimmer Steckdose 1 override on"
  triggers:
    - trigger: homeassistant
      event: start
    - trigger: time_pattern
      minutes: "/15"
  actions:
    - action: switch.turn_on
      target:
        entity_id: switch.wohnzimmer_steckdose_1_override
```

- Trigger on any write to `1/0/33`, not on its value. While locked the buttons send the inverse of a status that never changes.
- `1/0/33` also carries the TC5 page and the Couch long press, so all of them control the lamp.
- Optional: listen to `1/0/0` with value 0 if "all off" should also switch the lamp off.
- Set the Zigbee lamp's power-on behaviour to "off" or "previous".
- There is no spare button for a manual release. Without HA, write `0` to `1/6/33` from ETS.
