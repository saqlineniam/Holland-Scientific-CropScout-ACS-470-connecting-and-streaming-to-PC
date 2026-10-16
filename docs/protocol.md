# ACS-470 serial protocol (as observed)

Holland Scientific has not published this protocol. Everything here was
observed on one ACS-470 by listening to a GeoSCOUT GLS-400 logger configure it
(see [how-it-was-figured-out.md](how-it-was-figured-out.md)). The raw
recordings are in [`../captures/`](../captures/).

## Physical layer

| Item | Value |
|---|---|
| Bus | RS-485, two-wire, half-duplex (brown + blue pair, black = ground) |
| Logic levels | ~0–3.3 V; both lines idle at ~3 V between messages (sensor releases the bus) |
| Serial | **76800 baud, 8 data bits, no parity, 1 stop bit** (non-standard rate) |
| Encoding | ASCII, lines end with `\r\n` |

The sensor drives the pair only while it is sending, then releases it. That
is what allows a logger (or this PC) to send commands on the same two wires.

## Data lines (sensor → host)

In auto-send mode the sensor sends one line per sample:

```
-0.054,-0.062, 2.0339, 1.8262, 2.0662
```

Five comma-separated numbers. See the README for what they mean. Fields are
fixed-width with a space for the sign, so leading spaces are normal.

**Transitional line:** right after `$AUTOOFF` or a config change the sensor
emits one line with huge or non-ASCII values, e.g.
`0.568,-0.249,1747119.0000,6345309.0000,10772720.0000`. Discard it. The tools
here drop any line that does not parse as five numbers or has a value over
1000.

## Commands (host → sensor)

NMEA-style sentences: `$<BODY>*<CS>\r\n`.

| Sentence | Meaning |
|---|---|
| `$AUTOOFF,99*4D` | Stop auto-send. The GLS-400 sends this 3× about 0.22 s apart. |
| `$NEWCFG,99,ON,HI,S<hz>,AUTO*<CS>` | New configuration. `S<hz>` = output rate in Hz. `AUTO` = resume auto-send. |
| `$OK,00*` | Sensor's acknowledgement (sent back after each accepted `$NEWCFG`). |

### Verified rates

| Command | Measured output |
|---|---|
| `$NEWCFG,99,ON,HI,S2,AUTO*0B` | 2.0 Hz |
| `$NEWCFG,99,ON,HI,S10,AUTO*DC` | 10.0 Hz (sent from the PC) |
| `$NEWCFG,99,ON,HI,S25,AUTO*D6` | 24.7 Hz |

Other values (1, 5, 20 …) are offered in the viewer. Check for the `$OK`
reply and the measured rate before relying on them.

### Checksum

Unlike standard NMEA (XOR), the checksum is the **bitwise NOT of the byte-sum**
of the characters between `$` and `*`, taken modulo 256, written as two
uppercase hex digits:

```python
def checksum(body: str) -> int:
    return (~sum(body.encode("ascii"))) & 0xFF

checksum("AUTOOFF,99")               # 0x4D
checksum("NEWCFG,99,ON,HI,S2,AUTO")  # 0x0B
```

The sensor also accepted a `$NEWCFG` sent by the GLS-400 with an empty
checksum (`...*`), so it may not enforce it. Send a correct checksum anyway.

### Sequence used to set the rate

```
host  → $AUTOOFF,99*4D            (×3, ~0.2 s apart)
host  → $NEWCFG,99,ON,HI,S10,AUTO*DC
sensor→ $OK,00*                   (within ~0.1 s; resend NEWCFG if missing)
sensor→ <one garbled transitional line>
sensor→ data lines at the new rate
```

Implemented in [`../acs470_config.py`](../acs470_config.py) (`set_rate`).

### Persistence

**The sensor does not keep the setting across a power cycle.** The GLS-400
re-sends its configuration at every start-up. The viewer does the same on
connect, and re-sends automatically if the measured rate drops (a sign the
sensor rebooted).

## Unknowns

- `99`: probably an address or "all sensors" broadcast value.
- `ON`, `HI`: never changed by any GLS-400 menu option for the ACS-470. `HI`
  may be a gain or emitter-power setting. Kept exactly as the GLS-400 sends them.
- When the GLS-400's *sensor type* menu was changed, it sent
  `$NEWCFG,99,ON,HI,2,M1,AUTO*` (no `S`, an extra `M1` field, no checksum).
  This is probably the format for another Crop Circle model (ACS-430/225LR).
  **Don't send it to an ACS-470.**
