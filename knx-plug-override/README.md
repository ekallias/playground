# KNX Plug Override for Zigbee Lamps – Setup Manual

Written 2026-11-03.

## 1. Overview

This setup lets a KNX wall button control a Zigbee lamp that is plugged into a KNX-switched socket, while the plain KNX behaviour (button switches the socket) stays fully intact without Home Assistant (HA).

**How it works**

1. The Gira switch actuator has a *lock function*. While locked, it forces the socket relay ON and ignores the button's switching telegrams. The Zigbee lamp therefore has permanent power.
2. The MDT push-button keeps sending its normal telegram on every press. HA listens to that group address and toggles the Zigbee lamp (or starts an animation) instead.
3. The button's status LED is forced off by the MDT *LED priority* object, wired to the same lock group address, so the LED does not glow permanently.
4. HA owns one switch, `Zigbee Plug Override`, that writes the lock group address. It turns it on at HA start and re-asserts it every 15 minutes.

**Design principle:** KNX handles the essentials, HA is an additive overlay. With the lock at 0, the socket, button and LED behave exactly as a normal KNX installation. Nothing in ETS depends on HA being present.

**Devices covered**

- Gira Schaltaktor 16 A / Jalousieaktor 16 A Standard (order no. 5023 00, 5028 00, 5030 00)
- MDT Taster Smart 55/63 (BE-TAS5504.01 and variants)
- Home Assistant with the KNX integration and a Zigbee integration (Zigbee2MQTT or ZHA)

**Scope note:** parameter names below are taken from the Gira datasheet (50233300) and the MDT technical manual (08/2020, v1.0). Application versions differ slightly, so check the labels in your ETS version.

## 2. Prerequisites and group address plan

Four group addresses carry the whole setup; `2/1/1` is the override.

**Prerequisites**

- ETS (any current version) with both product databases imported, and programming access to the bus.
- Home Assistant with the KNX integration connected, and a Zigbee lamp already paired and working.
- In your Zigbee integration, set the lamp's *power-on behaviour* to "off" or "previous". Otherwise the lamp lights up the moment the override powers the socket.
- Know which actuator channel feeds the socket and which button on the MDT panel is the socket's button. The examples use button 1 and channel A.

**Group address plan** (adjust the numbers to your own scheme)

| GA | Name | DPT | Objects linked |
| --- | --- | --- | --- |
| `1/1/1` | Plug_Switch | 1.001 | Taster obj 0 (button, **sending** GA first), Gira *Schalten* |
| `1/1/2` | Plug_Status | 1.001 | Gira *Rückmeldung*, Taster obj 1 (*Status für Umschaltung/Anzeige*) |
| `2/1/1` | Plug_Override_Lock | 1.003 | Gira *Sperren* (obj 330 for channel A, then 350, ...), Taster obj 56 (*LED 1 Priorität*), HA switch |
| `1/1/4` | Button_Long (optional) | 1.001 | Taster obj 2 (*Gruppe lang*), HA only |

The Gira lock object is DPT 1.003. On the wire it is the same single bit as 1.001, so ETS may show a DPT warning if you leave the GA at 1.001. Set the GA to 1.003 to keep it quiet.

## 3. Gira actuator configuration

Enable the lock function on the channel that feeds the socket: the relay is forced on while `2/1/1` = 1, and switched off when the override ends.

**Parameters** (page *Relaisausgang... → SA... – Allgemein*)

| Page | Parameter | Value |
| --- | --- | --- |
| Freigaben | Sperrfunktion / Zwangsstellung | **Sperrfunktion** |
| Sperrfunktion | Quittierung | **off** (otherwise only the acknowledge object can release the lock) |
| Sperrfunktion | Polarität Sperrobjekt | the option meaning **1 = gesperrt, 0 = freigegeben** |
| Sperrfunktion | Beginn der Sperrfunktion | **Einschalten** |
| Sperrfunktion | Ende der Sperrfunktion | **Ausschalten** |

- The datasheet lists both polarity options with identical wording (apparent typo). Choose the one that reads "1 = gesperrt".
- Do not use "keine Änderung" at the start: if the socket was off when HA locked it, the lamp would stay unpowered.
- "Ende: Ausschalten" gives a predictable end state. "Nachgeführter Zustand" would apply the last state the actuator saw during the lock, which depends on the last button press.

**Communication objects**

- *Schalten* (this channel) → `1/1/1`
- *Rückmeldung Schalten* → `1/1/2`
- *Sperren* (object 330 for channel A, 350 for the next, ...) → `2/1/1`. Flags are K, (L), S, -, A: it receives but does not transmit and is not readable by default.

**Behaviour to know about (from the datasheet)**

- After a bus voltage failure or an ETS download the lock is always inactive (value 0). There is no parameter for this.
- A repeated update "locked → locked" or "unlocked → unlocked" does nothing. HA's periodic re-send is therefore harmless.
- There is no cyclic monitoring or timeout on the lock object.
- The relay can still be operated by hand at the actuator while KNX-locked.
- During the lock the actuator keeps evaluating telegrams internally (relevant only for the "nachgeführt" end setting).

## 4. MDT Taster Smart 55 configuration

The button keeps its normal toggle function, and the LED goes dark via the LED priority object whenever `2/1/1` = 1. No logic block is needed.

**4.1 Button (Taste 1)**

- *Einzel-Tastenfunktion*: Schalten, *Unterfunktion*: **Umschalten**.
- Object 0 *Taste 1 – Umschalten* → `1/1/1` (put this GA first so it is the sending address).
- Object 1 *Taste 1 – Status für Umschaltung/Anzeige* → `1/1/2`.

"Umschalten" sends the inverse of the last status it received. While the relay is locked on, the status stays 1, so the button sends 0 on every press. That is fine for the actuator (it ignores it) but means HA must not rely on the telegram value. See section 6.

**4.2 Status LED (LED 1)**

| Parameter | Value |
| --- | --- |
| LED aktiv | Ja |
| LED reagiert auf | **internes Objekt**, object number **1** |
| Farbe bei Tag / Nacht (Wert EIN) | your choice, e.g. white |
| Farbe bei Tag / Nacht (Wert AUS) | Aus |
| Objekt für Priorität | **aktiv, wenn Objekt LED Priorität Wert = 1** |
| Priorität: Farbe bei Tag / bei Nacht | **Aus**, both set to Dauer |

- Link object 56 *LED 1 Priorität* → `2/1/1`.
- Normal operation: the LED follows the plug status. Override on: the LED is forced dark, whatever the plug status says.
- Do not use object 60 *LED – Sperrobjekt* for this: it turns off all four LEDs of the panel.

**4.3 Optional: long press for the HA animation**

- In the button parameters, set *Gruppe langer Tastendruck* to aktiv.
- Link object 2 *Taste 1 Gruppe lang* → `1/1/4`. Only HA listens to it, so without HA a long press does nothing.
- The long-press time is adjustable (*Zeit langer Tastendruck*, 0.1 to 30 s).

**4.4 Optional: HA-independent release button**

The long-press object of a toggle button is not documented to send a fixed value, so do not use it to release the lock. Use a spare button instead: *Einzel-Tastenfunktion* Schalten, *Unterfunktion* Schalten, *Wert betätigte Taste* **Aus**, object 0 → `2/1/1`.

If you add this, change the HA re-assert automation (section 6) to trigger only at HA start, or it will re-lock after 15 minutes.

**Known limitation:** the display symbol for button 1 follows the status object and will most likely show "on" for the whole override. The LED can be darkened; I found no setting that does the same for the symbol.

## 5. Test in ETS before touching Home Assistant

Program both devices, open the ETS group monitor, and confirm the KNX side works on its own.

- [ ] **Baseline (lock = 0).** Press the button: the relay toggles, `1/1/1` and `1/1/2` show telegrams, the LED follows the plug status.
- [ ] **Lock on.** Write `1` to `2/1/1`. The relay closes (or stays closed) and the LED goes dark.
- [ ] **Button ignored.** With the lock on, press the button. A telegram appears on `1/1/1` (always value 0, see section 4.1), but the relay stays on.
- [ ] **Lock off.** Write `0` to `2/1/1`. The relay opens ("Ende: Ausschalten") and the button controls the plug and LED as before.
- [ ] **Hand switch.** Operate the relay by hand at the actuator while locked: it still works, as the datasheet states.
- [ ] **Bus reset.** Cycle bus power while locked. The lock returns to 0, the relay and LED behave as in the baseline.
- [ ] **Optional:** hold the button past the long-press time and check that `1/1/4` receives a telegram; press the release button and check that `2/1/1` gets 0.

If any step fails, fix it here. Do not start the HA part until this works.

## 6. Home Assistant configuration

HA gets one override switch, listens to the button's group address, and toggles the Zigbee lamp only while the override is on.

**6.1 KNX entities** (`configuration.yaml`, as ONE `knx:` block)

```yaml
knx:
  switch:
    - name: "Zigbee Plug Override"
      address: "2/1/1"
      state_address: "2/1/1"
      sync_state: false
  event:
    - address:
        - "1/1/1"
        - "1/1/4"   # optional: long press
      type: "switch"
```

- `sync_state: false` stops HA from sending a read at startup. The Gira lock object is not readable, so nobody would answer.
- HA is the source of truth for the lock. It cannot read the real state back.
- The switch entity is `switch.zigbee_plug_override`. Adjust the entity ids below to your own.

**6.2 Button toggles the Zigbee lamp**

```yaml
- alias: "KNX button -> Zigbee lamp"
  triggers:
    - trigger: event
      event_type: knx_event
      event_data:
        destination: "1/1/1"
        telegram_type: GroupValueWrite
  conditions:
    - condition: state
      entity_id: switch.zigbee_plug_override
      state: "on"
  actions:
    - action: light.toggle
      target:
        entity_id: light.your_zigbee_bulb
```

Do not filter on the telegram's value. The button sends 0 on every press while the relay is locked (section 4.1), so the automation toggles on any write.

**6.3 Optional: long press starts the animation**

```yaml
- alias: "KNX long press -> animation"
  triggers:
    - trigger: event
      event_type: knx_event
      event_data:
        destination: "1/1/4"
        telegram_type: GroupValueWrite
  conditions:
    - condition: state
      entity_id: switch.zigbee_plug_override
      state: "on"
  actions:
    - action: script.turn_on
      target:
        entity_id: script.your_lamp_animation
```

**6.4 Enable the override at startup**

```yaml
- alias: "Enable plug override"
  triggers:
    - trigger: homeassistant
      event: start
    - trigger: time_pattern
      minutes: "/15"
  actions:
    - action: switch.turn_on
      target:
        entity_id: switch.zigbee_plug_override
```

The 15-minute trigger re-locks the actuator after a bus power failure or an ETS download, because the Gira reports neither to HA. The repeated write is harmless (section 3). If you built the HA-independent release button (section 4.4), remove the `time_pattern` trigger.

**6.5 Zigbee side**

Set the lamp's power-on behaviour to "off" or "previous" in Zigbee2MQTT or ZHA. Expect the lamp to show as unavailable in HA whenever the socket is off, and to rejoin after the override powers it.

## 7. Failure modes and limitations

The lock has no timeout, so a dead HA can leave the override engaged until someone writes 0 to `2/1/1`.

| Situation | What happens | Recovery |
| --- | --- | --- |
| HA crashes or is removed while the lock is 1 | Socket stays on, button does nothing, LED dark | Write `0` to `2/1/1` (ETS, or the optional release button) |
| KNX bus power failure | Gira lock and LED priority reset to 0; normal KNX behaviour returns | HA's 15-minute re-assert sets 1 again |
| ETS download to the actuator | Lock reset to 0 | Same re-assert |
| HA restarts | Switch state unknown until the start automation runs | The start trigger writes 1 |
| Override released | Socket switches off ("Ende: Ausschalten") | Press the button to switch the socket as usual |
| Zigbee lamp socket is off | Lamp is unavailable in HA | Enable the override or press the button |

**Permanent removal of HA:** disable the HA automations, write `0` to `2/1/1`, and optionally remove the `2/1/1` links from the Gira lock object and Taster object 56 in ETS. Everything else keeps working as standard KNX.

**Known limitations**

- The Gira lock object cannot be read, so HA cannot verify the real lock state.
- The MDT display symbol for the button still reflects the status object and probably shows "on" during the override.
- Telegram values on `1/1/1` are meaningless while locked (always 0), so HA logic must trigger on any write.
- Not verified against hardware: exact option labels (e.g. the polarity entry), and whether the MDT LED priority object is re-read after a bus reset. Both devices reset to unlocked together, and HA's re-assert restores the override.

## 8. Troubleshooting and final checklist

Most problems come from a wrong link or polarity, which the ETS group monitor shows within seconds.

| Symptom | Likely cause | Check |
| --- | --- | --- |
| Relay does not close on `2/1/1` = 1 | Lock polarity inverted, or Sperren object not linked | Polarity option reads "1 = gesperrt"; object 330 (or the channel's) is on `2/1/1` |
| Button still switches the relay | Lock function not enabled, or lock not active | Freigaben: Sperrfunktion; watch `2/1/1` in the monitor |
| LED stays on during override | Priority object unlinked or wrong polarity | Object 56 on `2/1/1`; "aktiv, wenn Wert = 1"; color Aus |
| LED does not follow the plug normally | LED not set to internal object 1, or object 1 has no status link | Object 1 on `1/1/2` |
| HA does not see button presses | `1/1/1` missing in the `knx: event:` list | Developer tools → Events, listen to `knx_event` |
| Lamp toggles only every second press | Automation filters on the telegram value | Remove any value condition (section 6.2) |
| Override missing after power failure | Gira lock resets to 0 on bus failure | Wait for the 15-minute re-assert, or fire the automation manually |
| Lamp lights up when override starts | Zigbee power-on behaviour is "on" | Set it to "off" or "previous" |
| ETS warns about DPT on `2/1/1` | GA is 1.001, the lock object is 1.003 | Set the GA to 1.003 (same bit on the wire) |

**Final checklist**

- [ ] Gira channel: lock enabled, polarity "1 = gesperrt", begin "Einschalten", end "Ausschalten", Quittierung off
- [ ] Gira objects linked: Schalten `1/1/1`, Rückmeldung `1/1/2`, Sperren `2/1/1`
- [ ] MDT button: Umschalten, object 0 on `1/1/1`, object 1 on `1/1/2`
- [ ] MDT LED 1: internal object 1, priority object 56 on `2/1/1`, priority color Aus
- [ ] All ETS tests in section 5 pass without HA
- [ ] HA: one `knx:` block, switch with `sync_state: false`, event for `1/1/1`
- [ ] HA automations: toggle on any write, start-up and periodic enable
- [ ] Zigbee lamp power-on behaviour set to "off" or "previous"
- [ ] A way to release the lock without HA is known (ETS write, or the release button)
