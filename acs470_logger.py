"""
CropScout / Holland Scientific ACS-470 serial logger.

Sensor RS-485 pair (blue + brown) + black GND -> USB serial adapter in RS485 mode.
76800 baud, 8N1, ASCII lines, e.g.
    -0.054,-0.062, 2.0339, 1.8262, 2.0662\r\n
Five comma-separated values per line; see README.md ("Data format") for what
each column means.

Usage:
    python acs470_logger.py                 # auto-detect FTDI port, log to ./data/
    python acs470_logger.py --rate 10       # set 10 Hz first, then log
    python acs470_logger.py --port COM10 --out mydata.csv
Stop with Ctrl+C.
"""
import argparse
import csv
import datetime as dt
import os
import sys

import serial
import serial.tools.list_ports

BAUD = 76800
COLUMNS = ["v1", "v2", "v3", "v4", "v5"]


def find_port():
    for p in serial.tools.list_ports.comports():
        if p.vid == 0x0403:  # FTDI
            return p.device
    sys.exit("No FTDI USB-serial adapter found. Use --port COMx.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="COM port (default: auto-detect FTDI)")
    ap.add_argument("--out", help="CSV output path (default: data/acs470_<timestamp>.csv)")
    ap.add_argument("--rate", type=int, help="set sensor output rate (Hz) before logging")
    args = ap.parse_args()

    port = args.port or find_port()
    out = args.out
    if not out:
        os.makedirs("data", exist_ok=True)
        out = os.path.join("data", f"acs470_{dt.datetime.now():%Y%m%d_%H%M%S}.csv")

    with serial.Serial(port, BAUD, timeout=2) as ser, open(out, "w", newline="") as fh:
        if args.rate:
            from acs470_config import set_rate  # local import: acs470_config imports this module
            ser.timeout = 0.5
            ok = set_rate(ser, args.rate)
            print(f"Rate {args.rate} Hz: {'sensor replied $OK' if ok else 'no reply from sensor'}")
            ser.timeout = 2
        ser.reset_input_buffer()
        ser.readline()  # drop a possibly partial first line
        w = csv.writer(fh)
        w.writerow(["pc_time_iso"] + COLUMNS + ["raw"])
        print(f"Logging {port} @ {BAUD} -> {out}  (Ctrl+C to stop)")
        n = 0
        try:
            while True:
                line = ser.readline()
                if not line:
                    print("  (no data for 2 s - check sensor power / auto-send)")
                    continue
                # backslashreplace: garbled bytes would crash printing to the Windows console
                raw = line.decode("ascii", "backslashreplace").strip()
                parts = [s.strip() for s in raw.split(",")]
                try:
                    vals = [float(s) for s in parts]
                except ValueError:
                    vals = []
                if len(vals) != len(COLUMNS) or any(abs(v) > 1000 for v in vals):
                    print(f"  skipped malformed line: {raw!r}")
                    continue
                ts = dt.datetime.now().isoformat(timespec="milliseconds")
                w.writerow([ts] + vals + [raw])
                fh.flush()
                n += 1
                print(f"{ts}  {raw}")
        except KeyboardInterrupt:
            print(f"\nStopped. {n} rows saved to {out}")


if __name__ == "__main__":
    main()
