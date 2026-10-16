"""
Configure the ACS-470 output rate from the PC.

Protocol learned by recording a GeoSCOUT GLS-400 talking to the sensor
(see captures/gls400_startup_and_rate_change.txt). RS-485, 76800 8N1, ASCII:

    $AUTOOFF,99*4D                   stop auto-send            (GLS sends 3x)
    $NEWCFG,99,ON,HI,S2,AUTO*0B      new config, S<n> = n Hz   (GLS repeats until OK)
    $OK,00*                          sensor acknowledges

Checksum (two hex digits after '*') = bitwise NOT of the byte-sum of the
characters between '$' and '*', masked to 8 bits.

Seen working: S2 (2 Hz) and S25 (25 Hz). ON / HI / AUTO are kept exactly as
the GLS-400 sent them; their meaning is not confirmed.
The sensor does NOT remember the setting across power cycles.

    python acs470_config.py 10            # set 10 Hz
    python acs470_config.py 25 --port COM10
"""
import argparse
import time

import serial

from acs470_logger import BAUD, find_port


def checksum(body):
    return (~sum(body.encode("ascii"))) & 0xFF


def sentence(body):
    return f"${body}*{checksum(body):02X}\r\n".encode("ascii")


def set_rate(ser, hz, tries=3):
    """Send the GLS-400's command sequence; return True once the sensor says $OK."""
    for _ in range(3):
        ser.write(sentence("AUTOOFF,99"))
        ser.flush()
        time.sleep(0.2)
    cmd = sentence(f"NEWCFG,99,ON,HI,S{hz},AUTO")
    for _ in range(tries):
        ser.reset_input_buffer()
        ser.write(cmd)
        ser.flush()
        deadline = time.time() + 0.5
        while time.time() < deadline:
            if ser.readline().strip().startswith(b"$OK"):
                return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hz", type=int, help="output rate in Hz (seen working: 2, 25)")
    ap.add_argument("--port", help="COM port (default: auto-detect FTDI)")
    args = ap.parse_args()
    with serial.Serial(args.port or find_port(), BAUD, timeout=0.2) as ser:
        ok = set_rate(ser, args.hz)
        print(f"Sent S{args.hz}: {'sensor replied $OK' if ok else 'no $OK received'}")
        # measure the resulting rate
        ser.reset_input_buffer()
        t0, n = time.time(), 0
        while time.time() - t0 < 3:
            line = ser.readline().strip()
            if line and not line.startswith(b"$"):
                n += 1
        print(f"Measured ~{n / 3:.1f} lines/s")


if __name__ == "__main__":
    main()
