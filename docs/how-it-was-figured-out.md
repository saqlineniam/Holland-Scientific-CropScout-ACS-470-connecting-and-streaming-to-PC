# How this was figured out

Notes on the investigation, written up for anyone with a different Crop Circle
model, cable or adapter. The same techniques should carry over.

## 1. Starting point

The available instructions said: *"If you set the 470 to auto send in the
config software, it will output an RS-232 signal on the brown wire. Black is
ground and white is +12 V."* Neither the config software nor a manual was
available.

Brown and black were connected to a USB serial adapter (FTDI FT232R, which
appears as `COMx` on Windows) and the sensor was powered from 12 V.

## 2. Finding the baud rate (76800)

Bytes arrived at every common baud rate, but they were always garbage. Key
steps:

1. **Look at the waveform, not the bytes.** The FT232R's async bit-bang mode
   (via the `ftd2xx` Python package) can sample the RX pin like a crude logic
   analyser. Bursts arrived every 0.5 s (2 Hz), each lasting about 6 ms.
   Bit-bang sample timing on this chip is unreliable, so treat the numbers as
   rough.
2. **Measure pulse widths with the UART itself.** Open the port at a much
   higher baud rate, e.g. 230400 or 460800. Each received byte then shows how
   long the line stayed low after a falling edge (count the start bit plus
   the leading zero bits). At 230400 the low pulses clustered at 3, 6 and 9
   bit-times, so the real bit length was ~13 µs, i.e. **76800 baud**. That rate
   is not in the usual 9600/19200/38400/57600/115200 list, which is why the
   scan missed it.
3. At 76800 8N1 the output was clean ASCII.

Lesson: if every standard baud rate gives garbage but the garbage repeats in
a regular pattern, measure the bit time directly.

**Polarity:** inverting RX (FT232R EEPROM `InvertRXD`) was tried along the
way. It is **not** needed. The adapter's EEPROM is back at its default, and
no EEPROM changes are needed to use this repo.

## 3. Discovering it is RS-485, not RS-232

The cable has a fourth wire (blue) that the instructions did not mention.
With a multimeter (DC volts, black probe on ground):

| Wire | Reading |
|---|---|
| white | +11.7 V (power) |
| brown | ~3.0 V |
| blue | ~3.0 V |

Putting **blue alone** on the adapter's RX gave a scrambled version of the data
stream. Inverting the adapter's RX made it clean. So blue carries **the same
data, inverted**: a differential pair, i.e. RS-485 rather than RS-232. True
RS-232 would also swing to ±5–12 V, not sit at +3 V. The sensor releases
both lines between messages, which is how a device that also *listens* on
the pair behaves.

Switching the adapter to RS485 mode with blue→TX and brown→RX gave clean
data. (On the first try A/B were reversed and the data was scrambled again;
swapping the two wires fixed it.)

Blind probes (`Enter`, `?`, `help`, `ESC`) got no response. The sensor only
reacts to its own command set.

## 4. Recording the GeoSCOUT GLS-400

The sensor had previously been run by a Holland Scientific **GeoSCOUT
GLS-400** logger. RS-485 is a shared bus, so the PC can **listen in parallel**
while the GLS-400 controls the sensor (see
[wiring_gls400_sniff.svg](wiring_gls400_sniff.svg)).

- The GLS-400 cable was tapped by pushing thin wires into its M12 socket. An
  M12 Y-splitter wired 1:1 is the cleaner option. Pins were identified with a
  multimeter: the pair reading +11.7 V is power (the black-probe side is
  ground), and the two remaining pins read about +1.6 V against ground (data).
- **The GLS-400 powers the sensor through this cable.** Don't connect a second
  supply.
- Recording was done with [`acs470_sniff.py`](../acs470_sniff.py) (listen only)
  while power-cycling the GLS-400 and changing one menu option at a time.

Result: plain-text NMEA-style commands. `$NEWCFG,...,S2,...` gave 2 Hz and
`...,S25,...` gave 25 Hz. See [protocol.md](protocol.md).

## 5. Cracking the checksum

The two hex digits after `*` did not match standard NMEA XOR. Trying common
8-bit checksums against the three captured sentences, only one matched all
three: **NOT(sum of body bytes) & 0xFF**. A `$NEWCFG,...,S10,...` built with
it and sent from the PC was acknowledged (`$OK`) and the sensor switched to
10.0 Hz.

## Gotchas hit along the way

- **Only one program can open the COM port at a time.** "Access denied" means
  another copy (viewer, logger, sniffer) still has it open.
- **Windows console crash on garbled bytes.** Decoding with `"replace"` and
  printing to a cp1252 console or file raises `UnicodeEncodeError`. Use
  `"backslashreplace"`.
- **Microsoft Store Python** runs as `python3.12.exe`, not `python.exe`, so look
  for that name when finding or killing processes.
- **Leaving FT232R bit-bang mode** can leave the chip unresponsive. Recover by
  calling `cyclePort()` or re-plugging the USB.
