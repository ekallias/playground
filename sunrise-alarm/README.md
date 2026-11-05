# Sunrise Alarm for Home Assistant

A blueprint that simulates a sunrise with your smart bulbs, finishing exactly at your alarm time. It supports RGB/RGBWW Zigbee bulbs and dimmable-only bulbs, reads iPhone alarms through Shortcuts, and works for two people sharing a room.

Requires Home Assistant **2024.10 or newer**.

## Files

| File | Purpose |
|---|---|
| `sunrise_alarm.yaml` | The blueprint |
| `packages/sunrise_helpers.yaml` | Helpers: 2 alarm-time fields and 2 "armed" switches |

## How it works

1. Each evening, when your iPhone enters Sleep Focus, a Shortcut reads your alarm and stores its time in a Home Assistant helper. It also turns your "armed" switch on, or off if no alarm is set.
2. The blueprint checks every minute. When `alarm time - duration` arrives, and you are armed and home, the sunrise starts.
3. The sunrise is about 40 short transitions that follow a curve: dim and red for a long time, then faster, then white, then bright.
4. Up to three light groups join at different points. Turning on a chosen "abort" light or switch stops the sunrise immediately.
5. The first sunrise to start disarms everyone, so the second person's alarm cannot restart it.

## Setup

### 1. Install the helpers

**Option A, package file:** copy `packages/sunrise_helpers.yaml` to `/config/packages/`. Make sure `configuration.yaml` contains:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

Restart Home Assistant.

**Option B, UI:** go to Settings > Devices & services > Helpers and create:

| Type | Name | Settings |
|---|---|---|
| Date and/or time | Alarm time Temirlan | Time only (no date) |
| Date and/or time | Alarm time Husband | Time only (no date) |
| Toggle | Sunrise armed Temirlan | |
| Toggle | Sunrise armed Husband | |

The entity IDs must be `input_datetime.alarm_time_temirlan`, `input_datetime.alarm_time_husband`, `input_boolean.sunrise_armed_temirlan` and `input_boolean.sunrise_armed_husband`. The package file creates these automatically.

### 2. Install the blueprint

Copy `sunrise_alarm.yaml` to:

```
/config/blueprints/automation/sunrise/sunrise_alarm.yaml
```

Go to Settings > Automations & scenes > Blueprints and confirm "Sunrise Alarm" is listed. Reload automations if needed.

### 3. Create the automation

Settings > Automations & scenes > Create automation > Use blueprint > Sunrise Alarm. Fill in the sections below.

### 4. Create the iPhone Shortcuts

Do this on each phone. Use that person's own helpers. Both need the Home Assistant Companion app.

**Shortcut A: arm**
- Trigger: Shortcuts > Automation > Personal Automation > **Sleep Focus turns on**, set to Run Immediately.
- Actions:
  1. *Find Alarms* where *Is Enabled* is true. Add a *Label* filter if you have several alarms. If several match, add *Sort by Time* and *Get First Item*.
  2. *If* the alarm has any value:
     - *Get Details of Alarm* > *Time*.
     - *Format Date* with a custom format of `HH:mm:ss`.
     - Home Assistant *Call Service* `input_datetime.set_datetime` with data `{"entity_id": "input_datetime.alarm_time_temirlan", "time": "<formatted time>"}`.
     - Home Assistant *Call Service* `input_boolean.turn_on` on `input_boolean.sunrise_armed_temirlan`.
  3. *Otherwise*: Call Service `input_boolean.turn_off` on the same armed switch.

**Shortcut B: disarm**
- Trigger: **Sleep Focus turns off**.
- Action: Call Service `input_boolean.turn_off` on your armed switch.

If you get up at 3 AM, you are disarmed. When you go back into Sleep Focus, Shortcut A runs again and re-reads your alarm, so a changed or cancelled alarm is picked up.

## Blueprint settings

### Lights

| Setting | What it does |
|---|---|
| Ember lights | RGB/RGBWW lights. They start first, from the very beginning. Required. |
| Dawn lights | RGB/RGBWW lights that join later. Optional. |
| Daylight lights | Dimmable-only lights that join last. They only receive brightness commands, never colour. Optional. |
| Dawn lights join at | Percent of the sunrise at which the dawn lights come in (default 40). |
| Daylight lights join at | Percent at which the daylight lights come in (default 75). |

Each group can hold several lights. Each light starts at 1% and fades up from there.

### Schedule

| Setting | What it does |
|---|---|
| Sunrise duration | Length of the sunrise, in minutes (default 20). It also sets the lead time: the sunrise starts this long before the alarm. |
| Preview duration | Length of the test run (default 2 minutes). |
| Active days | Weekdays the sunrise may run on. |
| Skip when sun is above | Skips the sunrise if the real sun is already this high (degrees). 90 disables it. Around 8 skips bright summer mornings. |

### Alarm sources

| Setting | What it does |
|---|---|
| Person 1/2 alarm time helper | The `input_datetime` helper the Shortcut fills. |
| Person 1/2 armed flag | The `input_boolean` the Shortcut sets. If blank, the person is always treated as armed. |
| Person 1/2 presence | A `person` entity. The sunrise only counts for that person while they are `home`. Blank ignores presence. |
| Also run at a fixed start time / Fixed start time | An optional fixed schedule that works without a phone. Note that this is the start time, not the alarm time. |

Leave person 2 blank if only one person uses it. For a single person with no phone, leave all alarm fields blank and use the fixed start time.

### Look and feel

| Setting | What it does |
|---|---|
| Brightness curve | 1 is linear. Higher values stay dim longer and brighten faster at the end. Default 2.5. |
| Switch from RGB to white at | Percent of the sunrise at which the bulbs move from the red/orange RGB phase to white (default 55). |
| White temperature when switching | Starting colour temperature once on white (default 2200 K). |
| White temperature at the end | Final colour temperature (default 4000 K). Raise it for cooler daylight. |
| Seconds per step | Time between commands (default 30). Shorter is smoother but sends more Zigbee traffic. |

### Abort and ending

| Setting | What it does |
|---|---|
| Abort when any of these turn on | Required. Pick things like the bedroom ceiling light or a KNX wall switch. **Do not pick any of the sunrise lights**, because the blueprint's own commands would abort it. |
| On abort | Turn the sunrise lights off (default), set them to neutral white at 70%, or leave them alone. |
| Turn lights off after the sunrise | Minutes after the end to switch the lights off. 0 keeps them on. The abort entities cancel this wait too. |

## Testing

Test in this order.

1. **Preview.** Open the automation and choose *Run actions*. A 2-minute sunrise starts. Check the colours, the order the bulbs join in, and how smooth it looks. Manual runs skip the conditions and leave your armed flags alone.
2. **Abort.** Start a preview and turn on your abort light. The sunrise should stop within one step and the lights should follow your "On abort" choice.
3. **Shortcut.** Run Shortcut A by hand. In Developer tools > States, check that `input_datetime.alarm_time_*` shows your alarm time and the armed switch is on. Change the alarm or turn it off, run it again, and confirm the helper and switch update.
4. **Full schedule.** Set the alarm helper to about 25 minutes from now with the duration at 20, turn the armed switch on, and watch it start on time.
5. **Overnight.** Do the real thing once and see what you think.

## Tuning

- **Too orange or too blue at the end:** change *White temperature at the end*. Bulb makers map colour temperature differently, so expect to adjust it.
- **Visible jump from red to white:** lower *Switch from RGB to white at*, or raise *White temperature when switching*.
- **Starts too bright:** raise *Brightness curve* (3 to 3.5). Some bulbs cannot go below a fixed minimum at "1%".
- **Light group joins too noticeably:** move its join point later, or lower it.
- **Zigbee bulbs stutter or lag:** raise *Seconds per step* to 45 or 60.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Nothing happens at the start time | Armed switch is off; person is not `home`; today is not in *Active days*; the sun-elevation limit is too low; the helper holds an old or wrong time. Check the automation's trace. |
| Starts at the wrong time | Duration (the lead time) differs from what you expect, or the Shortcut wrote the wrong time. Look at the helper value. |
| Runs on weekends | A "weekdays" alarm stays enabled on Friday night. Untick Saturday and Sunday in *Active days*. |
| Second person's alarm restarts it | It should not, because the first run disarms both. Check that both armed flags are selected in the blueprint. |
| Sunrise aborts instantly | An abort entity is one of the sunrise lights, or is changing to `on` by itself. |
| Bulbs flash bright at the start | The bulb ignores the 1% pre-ignite. Use a pre-set "power-on behaviour" for the bulb in ZHA or Zigbee2MQTT. |
| Blueprint rejected with a "trigger" error | Home Assistant is older than 2024.10. |
| Colour is wrong on a bulb | It may not support `color_temp_kelvin` or RGB. Put dimmable-only bulbs in the *Daylight* group. |

Use Settings > Automations > your automation > Traces to see every step, the values calculated for each step, and why a run stopped.

## Limitations

- Triggers are checked once a minute, so the start can be up to a minute off.
- The RGB-to-white switch happens once. Some bulbs may jump visibly at that point.
- The iPhone cannot expose alarm *repeat days* to Shortcuts. Use *Active days* for that.
- This was checked for valid YAML and the brightness and colour calculations were simulated, but it has not been run on a live Home Assistant. Treat the first preview as a test.
