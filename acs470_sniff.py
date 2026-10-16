"""
Listen-only recorder for mapping GeoSCOUT GLS-400 settings to sensor commands.

Wire the dongle onto the same RS-485 pair as the GLS-400 and sensor, run this,
then change one GLS-400 menu setting at a time. Every $ command is printed with
which field changed since the previous command of that type, plus the data
rate and a sample line once the sensor settles. Never transmits.

    python acs470_sniff.py
Stop with Ctrl+C. Everything is saved to captures/sniff_<timestamp>.txt.
"""
import argparse
import datetime as dt
import os
import time

import serial

from acs470_config import checksum
from acs470_logger import BAUD, find_port


def check(line):
    """'ok', 'bad' or '-' (no checksum present) for a $...*XX sentence."""
    if "*" not in line:
        return "-"
    body, cs = line[1:].split("*", 1)
    if len(cs) < 2:
        return "-"
    return "ok" if cs[:2].upper() == f"{checksum(body):02X}" else "bad"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="COM port (default: auto-detect FTDI)")
    args = ap.parse_args()
    port = args.port or find_port()
    os.makedirs("captures", exist_ok=True)
    path = os.path.join("captures", f"sniff_{dt.datetime.now():%Y%m%d_%H%M%S}.txt")

    last_fields = {}
    data_times = []
    last_report = 0.0
    sample = ""
    with serial.Serial(port, BAUD, timeout=0.2) as ser, open(path, "w") as log:
        def out(msg):
            print(msg)
            log.write(msg + "\n")
            log.flush()

        out(f"Listening on {port} @ {BAUD} (listen only) -> {path}")
        out("Change ONE GLS-400 setting at a time. Ctrl+C to stop.\n")
        try:
            while True:
                raw = ser.readline()
                now = time.time()
                stamp = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                # backslashreplace: garbled bytes would crash printing to the Windows console
                line = raw.decode("ascii", "backslashreplace").strip()
                if line.startswith("$"):
                    if data_times:
                        # summarize sensor output since the last command
                        span = data_times[-1] - data_times[0]
                        rate = (len(data_times) - 1) / span if span > 0 else 0
                        out(f"           (sensor: {len(data_times)} data lines, ~{rate:.1f} Hz, e.g. {sample})")
                        data_times = []
                    body = line[1:].split("*", 1)[0]
                    fields = body.split(",")
                    name = fields[0]
                    prev = last_fields.get(name)
                    diff = ""
                    if prev and prev != fields:
                        changes = [f"field {i}: {a} -> {b}"
                                   for i, (a, b) in enumerate(zip(prev, fields)) if a != b]
                        if len(prev) != len(fields):
                            changes.append(f"field count {len(prev)} -> {len(fields)}")
                        diff = "   CHANGED " + "; ".join(changes)
                    last_fields[name] = fields
                    out(f"{stamp}  {line:<40} [checksum {check(line)}]{diff}")
                elif line:
                    data_times.append(now)
                    sample = line
                    if len(data_times) > 1 and now - last_report > 10:
                        span = data_times[-1] - data_times[0]
                        out(f"{stamp}  (sensor running ~{(len(data_times) - 1) / span:.1f} Hz, e.g. {sample})")
                        last_report = now
                        data_times = data_times[-1:]
        except KeyboardInterrupt:
            out("\nStopped.")


if __name__ == "__main__":
    main()
