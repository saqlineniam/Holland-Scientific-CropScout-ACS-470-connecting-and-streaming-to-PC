"""
Live viewer for the ACS-470: a scrolling "line-scan camera" image, big live
readouts, a trend chart, a rate selector and a Record button.

    python acs470_viewer.py                # auto-detect port, set 10 Hz
    python acs470_viewer.py --rate 25 --port COM10

Viewfinder: time runs right-to-left over the last WINDOW_S seconds; each of the
5 channels is one horizontal band, colored low (dark blue) -> high (yellow)
relative to the recent range of that channel. As you move the sensor over the
crop the image builds up like a push-broom scan.

The sensor forgets its rate on power loss, so the viewer sets it on connect and
re-sends it if the measured rate drops well below the target.
"""
import argparse
import csv
import datetime as dt
import os
import queue
import threading
import time
import tkinter as tk
from collections import deque

import serial

from acs470_config import set_rate
from acs470_logger import BAUD, COLUMNS, find_port

WINDOW_S = 120           # seconds shown in viewfinder and chart
BINS = 240               # columns in viewfinder / points in chart
RATES = [1, 2, 5, 10, 20, 25]
VF_W, VF_H = 720, 250    # viewfinder size (px)
CH_W, CH_H = 720, 170    # chart size (px)
LINE_COLORS = ["#4cc9f0", "#f72585", "#90be6d", "#f9c74f", "#f8961e"]
BG, PANEL, FG, DIM = "#111418", "#1b2027", "#e8eaed", "#8a929c"

# viridis-like colormap stops
CMAP = [(68, 1, 84), (59, 82, 139), (33, 145, 140), (94, 201, 98), (253, 231, 37)]


def cmap(t):
    t = min(max(t, 0.0), 1.0) * (len(CMAP) - 1)
    i = min(int(t), len(CMAP) - 2)
    f = t - i
    a, b = CMAP[i], CMAP[i + 1]
    return "#%02x%02x%02x" % tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3))


def parse(line):
    """Return the 5 values of a data line, or None (skips $OK and garbled lines)."""
    # backslashreplace: garbled bytes would crash writing the CSV / console
    raw = line.decode("ascii", "backslashreplace").strip()
    try:
        vals = [float(s) for s in raw.split(",")]
    except ValueError:
        return None
    if len(vals) != len(COLUMNS) or any(abs(v) > 1000 for v in vals):
        return None
    return vals, raw


def reader(port, q, cmd_q, stop, rate):
    """Background thread: owns the serial port, applies rate changes, pushes data to q."""
    target = rate
    while not stop.is_set():
        try:
            with serial.Serial(port, BAUD, timeout=0.5) as ser:
                q.put(("status", f"Connected {port} @ {BAUD}"))
                pending = target          # set the rate on every (re)connect
                arrivals = deque()
                last_set = 0.0
                last_data = time.time()
                while not stop.is_set():
                    while not cmd_q.empty():
                        pending = target = cmd_q.get()
                    if pending is not None:
                        ok = set_rate(ser, pending)
                        q.put(("rate", (pending, ok)))
                        pending, last_set = None, time.time()
                        arrivals.clear()
                    line = ser.readline()
                    now = time.time()
                    if not line:
                        if now - last_data > 2:
                            q.put(("status", "No data - check sensor power"))
                        continue
                    p = parse(line)
                    if not p:
                        continue
                    last_data = now
                    arrivals.append(now)
                    while arrivals and now - arrivals[0] > 5:
                        arrivals.popleft()
                    # sensor power-cycled back to its default rate -> re-send
                    if (target and now - last_set > 8
                            and len(arrivals) / 5 < 0.6 * target):
                        pending = target
                    q.put(("data", (dt.datetime.now(), *p)))
        except serial.SerialException as e:
            q.put(("status", f"Port error: {e} - retrying"))
            time.sleep(2)


class Viewer:
    def __init__(self, root, port, rate):
        self.root, self.port = root, port
        self.q, self.cmd_q, self.stop = queue.Queue(), queue.Queue(), threading.Event()
        self.hist = deque()                 # (time.time(), vals)
        self.rec_file = self.rec_writer = None
        self.rec_rows = 0
        self.last_draw = 0.0
        self.dirty = False

        root.title("ACS-470 Live View")
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)

        top = tk.Frame(root, bg=BG)
        top.pack(fill="x", padx=12, pady=(10, 4))
        self.dot = tk.Label(top, text="●", fg="#555", bg=BG, font=("Segoe UI", 14))
        self.dot.pack(side="left")
        self.status = tk.Label(top, text="Starting...", fg=DIM, bg=BG, font=("Segoe UI", 10))
        self.status.pack(side="left", padx=6)
        self.rec_btn = tk.Button(top, text="●  Record", command=self.toggle_rec,
                                 bg="#2a313a", fg=FG, activebackground="#3a434e",
                                 relief="flat", padx=12, font=("Segoe UI", 10, "bold"))
        self.rec_btn.pack(side="right")
        self.rate_var = tk.StringVar(value=f"{rate} Hz")
        rate_menu = tk.OptionMenu(top, self.rate_var, *[f"{r} Hz" for r in RATES],
                                  command=self.change_rate)
        rate_menu.config(bg="#2a313a", fg=FG, activebackground="#3a434e", relief="flat",
                         highlightthickness=0, font=("Segoe UI", 10))
        rate_menu.pack(side="right", padx=8)
        tk.Label(top, text="Rate", fg=DIM, bg=BG, font=("Segoe UI", 10)).pack(side="right")
        self.rate_lbl = tk.Label(top, text="", fg=DIM, bg=BG, font=("Segoe UI", 10))
        self.rate_lbl.pack(side="right", padx=8)

        self.rec_lbl = tk.Label(root, text="", fg="#ff5d5d", bg=BG, font=("Segoe UI", 9), anchor="e")
        self.rec_lbl.pack(fill="x", padx=12)

        self.vf = tk.Canvas(root, width=VF_W, height=VF_H, bg="#000", highlightthickness=0)
        self.vf.pack(padx=12, pady=4)

        tiles = tk.Frame(root, bg=BG)
        tiles.pack(fill="x", padx=12, pady=4)
        self.val_lbls = []
        for i, name in enumerate(COLUMNS):
            f = tk.Frame(tiles, bg=PANEL)
            f.pack(side="left", expand=True, fill="x", padx=(0 if i == 0 else 6, 0))
            tk.Frame(f, bg=LINE_COLORS[i], height=3).pack(fill="x")
            tk.Label(f, text=name, fg=DIM, bg=PANEL, font=("Segoe UI", 9)).pack(anchor="w", padx=8, pady=(4, 0))
            v = tk.Label(f, text="--", fg=FG, bg=PANEL, font=("Consolas", 20, "bold"))
            v.pack(anchor="w", padx=8, pady=(0, 6))
            self.val_lbls.append(v)

        self.chart = tk.Canvas(root, width=CH_W, height=CH_H, bg=PANEL, highlightthickness=0)
        self.chart.pack(padx=12, pady=(4, 12))

        threading.Thread(target=reader, args=(port, self.q, self.cmd_q, self.stop, rate),
                         daemon=True).start()
        self.tick()

    # ---- controls --------------------------------------------------------
    def change_rate(self, choice):
        hz = int(choice.split()[0])
        self.rate_lbl.config(text=f"setting {hz} Hz...", fg=DIM)
        self.cmd_q.put(hz)

    def toggle_rec(self):
        if self.rec_file:
            self.rec_file.close()
            self.rec_file = self.rec_writer = None
            self.rec_btn.config(text="●  Record", bg="#2a313a")
            self.rec_lbl.config(text=f"Saved {self.rec_rows} rows -> {self.rec_path}")
            return
        os.makedirs("data", exist_ok=True)
        self.rec_path = os.path.join("data", f"acs470_{dt.datetime.now():%Y%m%d_%H%M%S}.csv")
        self.rec_file = open(self.rec_path, "w", newline="")
        self.rec_writer = csv.writer(self.rec_file)
        self.rec_writer.writerow(["pc_time_iso"] + COLUMNS + ["raw"])
        self.rec_rows = 0
        self.rec_btn.config(text="■  Stop", bg="#a4161a")

    # ---- main loop -------------------------------------------------------
    def tick(self):
        now = time.time()
        while not self.q.empty():
            kind, payload = self.q.get()
            if kind == "status":
                self.status.config(text=payload)
            elif kind == "rate":
                hz, ok = payload
                self.rate_lbl.config(text=f"sensor set to {hz} Hz" if ok else f"{hz} Hz: no reply",
                                     fg="#38d97a" if ok else "#ff5d5d")
            else:
                ts, vals, raw = payload
                self.hist.append((now, vals))
                self.dirty = True
                if self.rec_writer:
                    self.rec_writer.writerow([ts.isoformat(timespec="milliseconds")] + vals + [raw])
                    self.rec_rows += 1
        while self.hist and now - self.hist[0][0] > WINDOW_S:
            self.hist.popleft()
        if self.rec_writer:
            self.rec_file.flush()
            self.rec_lbl.config(text=f"REC  {self.rec_rows} rows -> {self.rec_path}")

        recent = [t for t, _ in self.hist if now - t <= 3]
        live = bool(recent) and now - recent[-1] < 2
        self.dot.config(fg="#38d97a" if live else "#555")
        if live and len(recent) > 1:
            hz = (len(recent) - 1) / (recent[-1] - recent[0])
            self.status.config(text=f"LIVE  {self.port} @ {BAUD}  |  {hz:.1f} Hz")
        # redraw at most ~6 fps regardless of sensor rate
        if self.dirty and now - self.last_draw > 0.15:
            self.redraw(now)
            self.dirty, self.last_draw = False, now
        self.root.after(30, self.tick)

    def bins(self, now):
        """Average the history into BINS time slots (oldest first); empty slot -> None."""
        w = WINDOW_S / BINS
        sums = [None] * BINS
        counts = [0] * BINS
        for t, vals in self.hist:
            i = BINS - 1 - int((now - t) / w)
            if 0 <= i < BINS:
                if sums[i] is None:
                    sums[i] = list(vals)
                else:
                    sums[i] = [a + b for a, b in zip(sums[i], vals)]
                counts[i] += 1
        return [None if s is None else [x / c for x in s] for s, c in zip(sums, counts)]

    def ranges(self):
        rs = []
        for c in range(len(COLUMNS)):
            col = sorted(v[c] for _, v in self.hist)
            # 5th-95th percentile so a single spike doesn't flatten the scale
            lo, hi = col[int(0.05 * (len(col) - 1))], col[int(0.95 * (len(col) - 1))]
            pad = max((hi - lo) * 0.1, 1e-3)
            rs.append((lo - pad, hi + pad))
        return rs

    def redraw(self, now):
        if not self.hist:
            return
        for lbl, v in zip(self.val_lbls, self.hist[-1][1]):
            lbl.config(text=f"{v:.4f}")
        rs = self.ranges()
        bins = self.bins(now)
        nch = len(COLUMNS)

        # viewfinder: scrolling line-scan image
        vf = self.vf
        vf.delete("all")
        band = VF_H / nch
        colw = VF_W / BINS
        for j, vals in enumerate(bins):
            if vals is None:
                continue
            x = j * colw
            for c, v in enumerate(vals):
                lo, hi = rs[c]
                vf.create_rectangle(x, c * band, x + colw + 1, (c + 1) * band,
                                    fill=cmap((v - lo) / (hi - lo)), width=0)
        for c, name in enumerate(COLUMNS):
            vf.create_text(8, c * band + 6, text=name, anchor="nw", fill="#ffffff",
                           font=("Segoe UI", 9, "bold"))
        # camera-style overlay
        for (x, y, dx, dy) in [(6, 6, 1, 1), (VF_W - 6, 6, -1, 1), (6, VF_H - 6, 1, -1), (VF_W - 6, VF_H - 6, -1, -1)]:
            vf.create_line(x, y, x + 24 * dx, y, fill="#fff", width=2)
            vf.create_line(x, y, x, y + 24 * dy, fill="#fff", width=2)
        vf.create_text(VF_W - 12, VF_H - 12, anchor="se", fill="#fff", font=("Consolas", 9),
                       text=dt.datetime.now().strftime("%H:%M:%S"))
        if self.rec_writer:
            vf.create_oval(VF_W - 70, 12, VF_W - 60, 22, fill="#ff3b3b", width=0)
            vf.create_text(VF_W - 56, 17, anchor="w", text="REC", fill="#ff3b3b",
                           font=("Segoe UI", 9, "bold"))

        # trend chart: each channel normalized to its own recent range
        ch = self.chart
        ch.delete("all")
        for k in range(1, 4):
            y = CH_H * k / 4
            ch.create_line(0, y, CH_W, y, fill="#262c35")
        dx = CH_W / (BINS - 1)
        for c in range(nch):
            lo, hi = rs[c]
            pts = []
            for j, vals in enumerate(bins):
                if vals is None:
                    continue
                t = min(max((vals[c] - lo) / (hi - lo), 0.0), 1.0)
                pts += [j * dx, CH_H - 8 - t * (CH_H - 16)]
            if len(pts) >= 4:
                ch.create_line(*pts, fill=LINE_COLORS[c], width=2)
        ch.create_text(8, 6, anchor="nw", fill=DIM, font=("Segoe UI", 8),
                       text=f"Trend, last {WINDOW_S} s (each channel auto-scaled)")

    def close(self):
        self.stop.set()
        if self.rec_file:
            self.rec_file.close()
        self.root.destroy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="COM port (default: auto-detect FTDI)")
    ap.add_argument("--rate", type=int, default=10, help="output rate in Hz to set on connect")
    args = ap.parse_args()
    root = tk.Tk()
    root.resizable(False, False)
    Viewer(root, args.port or find_port(), args.rate)
    root.mainloop()


if __name__ == "__main__":
    main()
