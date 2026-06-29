#!/usr/bin/env python3
"""fpga-isv -- a config-driven graphical panel viewer for interactive-sim.

It draws nothing itself: a JSON config supplies either a board *photo* (by URL or local path)
or a blank *panel* of a given size, plus a pixel-coordinate map of where the LEDs and buttons
sit on it. The viewer is a pure client of the interactive-sim wire protocol: it LISTENS on a
TCP port, the simulation connects to it (set INTERACTIVE_STREAM=host:port for the sim), and:

  * design-driven `flag` events light an LED overlay at its mapped position, in the configured
    "on" colour;
  * a mouse click is hit-tested against the button regions; the button under the cursor sends
    `val=1` on press and `val=0` on release (a `"toggle": true` button flips and latches).

The viewer can be opened and closed at any time, and tolerates the simulation stopping or
restarting: it keeps its listen socket open and accepts reconnections, clearing all overlays on
each new connection (the sim replays its full state on connect).

Config -- all coordinates are in ORIGINAL image pixels; `image.scale` scales the photo/panel and
every coordinate together for display:

  {
    "title": "My Board",                         # optional window title
    "image":  { "url": "...png" | "/path" | "rel/path",   # a photo (relative paths are resolved
                "cache": "name.png", "crop": [x0,y0,x1,y1], "scale": 0.72 },   # against the config dir)
    "image":  { "width": 1200, "height": 800, "bg": "#101418", "scale": 1.0 }, # ...OR a blank panel
    "leds":   { "on_color": "#ff5a36", "radius": 13, "glow": true,
                "items": [ { "name": "leds", "bit": 0, "x": 632, "y": 422 }, ... ] },
    "buttons":[ { "name": "btn_pwr", "shape": "circle", "x": 1400, "y": 343, "r": 42, "key": "p" },
                { "name": "sw0", "shape": "rect", "x": 800, "y": 250, "w": 30, "h": 30, "toggle": true } ]
  }

An LED item lights when bit `bit` of flag `name` is 1, so an N-bit bus ("leds") is N items with
different bit indices, and a 1-bit flag ("led3") is one item.

Building a map for a new photo/panel: run with --calibrate and click on it; each click prints the
original-image pixel coordinate to the console (and marks it), so you can read off the x/y for
every LED and button.

JPEG/cropped photos need Pillow (`pip install pillow`); PNG/GIF also work with stdlib Tk.

Usage:
  fpga-isv (--example ulx3s | --config board.json) [--port 7777] [--host 0.0.0.0]
           [--refresh] [--calibrate]
  fpga-isv --list-examples
"""

import argparse
import json
import os
import queue
import socket
import sys
import threading
import time
import tkinter as tk
import urllib.request

from fpga_isv import __version__
from fpga_isv.examples import example_config_path, list_examples

# -- palette for the non-photo chrome (status strip, blank panel, calibration marks) ----------
BG          = "#101418"
PANEL_BG    = "#161b20"
TEXT        = "#cfd6df"
TEXT_DIM    = "#7e8893"
OK_GREEN    = "#56d364"
WARN_AMBER  = "#e3b341"
LED_OFF_RIM = "#000000"
CAL_COLOR   = "#00e5ff"

# Heartbeats per sim/wall speed sample: the ratio is measured over the span from the window's
# first heartbeat to its last, which averages out wall-clock jitter.
RATE_WINDOW = 100


# ---------------------------------------------------------------------------
# Pure protocol helpers (no Tk / no socket -- unit-tested in tests/test_protocol.py)
# ---------------------------------------------------------------------------
def decode_message(line):
    """Parse one newline-stripped wire line (bytes) into a dict, or None if not JSON."""
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return None


def encode_control(name, val):
    """Frame a viewer -> sim control message as a newline-terminated JSON line (bytes)."""
    return (json.dumps({"name": name, "val": int(val)}) + "\n").encode("utf-8")


def mask(val, width):
    """Low `width` bits of `val` (width >= 1)."""
    return val & ((1 << width) - 1)


def led_on(val, bit):
    """Whether bit `bit` of flag value `val` is set."""
    return bool((val >> bit) & 1)


# ---------------------------------------------------------------------------
# Image / panel loading (URL or local path; Pillow if available, else stdlib Tk)
# ---------------------------------------------------------------------------
def fetch_bytes(url, cache_path, refresh):
    """Return the image path, downloading url -> cache_path if needed."""
    if os.path.exists(url):                       # a local path was given
        return url
    if url.startswith("file://"):
        return url[7:]
    if cache_path and os.path.exists(cache_path) and not refresh:
        return cache_path
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=30).read()
    if cache_path:
        with open(cache_path, "wb") as f:
            f.write(data)
        return cache_path
    # no cache configured: stash bytes in a temp file next to cwd
    tmp = os.path.join(os.getcwd(), "_board_image")
    with open(tmp, "wb") as f:
        f.write(data)
    return tmp


def load_photo(path, scale, crop=None):
    """Return (PhotoImage, width, height), optionally cropped to `crop`
    [x0,y0,x1,y1] (original pixels) then scaled by `scale`. Keep a reference!"""
    try:
        from PIL import Image, ImageTk
        im = Image.open(path).convert("RGB")
        if crop:
            im = im.crop(tuple(crop))
        if scale != 1.0:
            im = im.resize((max(1, round(im.width * scale)),
                            max(1, round(im.height * scale))), Image.LANCZOS)
        photo = ImageTk.PhotoImage(im)
        return photo, im.width, im.height
    except ImportError:
        # stdlib fallback: PNG/GIF only, no crop, integer zoom/subsample
        photo = tk.PhotoImage(file=path)
        if crop:
            raise SystemExit("[fpga-isv] image.crop needs Pillow (pip install pillow)")
        if scale < 1.0:
            photo = photo.subsample(max(1, round(1.0 / scale)))
        elif scale > 1.0:
            photo = photo.zoom(round(scale))
        return photo, photo.width(), photo.height()


def make_blank(w, h, bg):
    """Return a (PhotoImage, w, h) filled with `bg` -- a from-scratch panel mockup."""
    try:
        from PIL import Image, ImageTk
        return ImageTk.PhotoImage(Image.new("RGB", (w, h), bg)), w, h
    except ImportError:
        photo = tk.PhotoImage(width=w, height=h)
        photo.put(bg, to=(0, 0, w, h))
        return photo, w, h


def build_photo(image_cfg, cfg_dir, refresh, scale, crop):
    """Resolve the config's `image` block to (PhotoImage, w, h). A Tk root must already exist.

    A blank panel (`width`+`height`, no `url`) is created at the scaled size; a photo is
    fetched (local paths resolve against the config dir), cropped, and scaled."""
    if "url" not in image_cfg and "width" in image_cfg and "height" in image_cfg:
        W = max(1, round(image_cfg["width"] * scale))
        H = max(1, round(image_cfg["height"] * scale))
        return make_blank(W, H, image_cfg.get("bg", BG))

    url = image_cfg["url"]
    # Resolve a local (non-remote) relative path against the config dir, so a bundled example
    # is self-contained no matter the working directory.
    if not url.startswith(("http://", "https://", "file://")) and not os.path.isabs(url):
        candidate = os.path.join(cfg_dir, url)
        if os.path.exists(candidate):
            url = candidate
    cache = image_cfg.get("cache")
    if cache:                                      # cache next to the config file
        cache = os.path.join(cfg_dir, cache)
    path = fetch_bytes(url, cache, refresh)
    return load_photo(path, scale, crop)


# ---------------------------------------------------------------------------
# Board: places LED/button overlays from the config over the photo/panel
# ---------------------------------------------------------------------------
class Board:
    def __init__(self, canvas, cfg, scale, send_control, crop_origin=(0, 0)):
        self.c = canvas
        self.s = scale
        self.ox, self.oy = crop_origin   # crop offset in original pixels
        self.send_control = send_control
        self.leds = {}            # flag name -> list of (bit, core_id, glow_id)
        self.buttons = []         # list of dict: name, region, overlay, toggle...
        self.held = None          # currently pressed momentary button
        self._keys_down = set()
        self.key_to_btn = {}

        self._build_leds(cfg.get("leds", {}))
        self._build_buttons(cfg.get("buttons", []))

    # original-image coords -> canvas coords: subtract crop origin, then scale.
    def _px(self, x):
        return (x - self.ox) * self.s

    def _py(self, y):
        return (y - self.oy) * self.s

    def _sz(self, v):           # sizes (radius/width/height) only scale
        return v * self.s

    def _build_leds(self, leds):
        on = leds.get("on_color", "#ff5a36")
        shape = leds.get("shape", "circle")     # "circle" | "rect"
        r = leds.get("radius", 13)
        dw, dh = leds.get("w", 16), leds.get("h", 26)
        glow = leds.get("glow", True)
        for item in leds.get("items", []):
            x, y = self._px(item["x"]), self._py(item["y"])
            color = item.get("color", on)
            sh = item.get("shape", shape)
            if sh == "rect":
                hw, hh = self._sz(item.get("w", dw)) / 2, self._sz(item.get("h", dh)) / 2
                box = (x - hw, y - hh, x + hw, y + hh)
                gbox = (x - hw - 4, y - hh - 4, x + hw + 4, y + hh + 4)
                mk = self.c.create_rectangle
            else:
                rr = self._sz(item.get("r", r))
                box = (x - rr, y - rr, x + rr, y + rr)
                gbox = (x - rr - 5, y - rr - 5, x + rr + 5, y + rr + 5)
                mk = self.c.create_oval
            gid = -1
            if glow:
                gid = mk(*gbox, fill=color, outline="", stipple="gray50", state="hidden")
            cid = mk(*box, fill=color, outline=LED_OFF_RIM, width=1, state="hidden")
            self.leds.setdefault(item["name"], []).append((item.get("bit", 0), cid, gid))

    def _build_buttons(self, buttons):
        # (x, y) is the button CENTRE for both shapes.
        for b in buttons:
            shape = b.get("shape", "circle")
            x, y = self._px(b["x"]), self._py(b["y"])
            if shape == "rect":
                hw, hh = self._sz(b["w"]) / 2, self._sz(b["h"]) / 2
                region = ("rect", x - hw, y - hh, x + hw, y + hh)
                ov = self.c.create_rectangle(x - hw, y - hh, x + hw, y + hh,
                                             outline=CAL_COLOR, width=1, fill=CAL_COLOR,
                                             stipple="gray25", state="hidden")
            else:
                r = self._sz(b.get("r", 36))
                region = ("circle", x, y, r)
                ov = self.c.create_oval(x - r, y - r, x + r, y + r,
                                        outline=CAL_COLOR, width=1, fill=CAL_COLOR,
                                        stipple="gray25", state="hidden")
            entry = {"name": b["name"], "region": region, "overlay": ov,
                     "toggle": b.get("toggle", False), "state": 0}
            self.buttons.append(entry)
            if b.get("key"):
                self.key_to_btn[b["key"]] = entry

    # -- hit testing --------------------------------------------------------
    @staticmethod
    def _hit(region, px, py):
        if region[0] == "rect":
            _, x0, y0, x1, y1 = region
            return x0 <= px <= x1 and y0 <= py <= y1
        _, cx, cy, r = region
        return (px - cx) ** 2 + (py - cy) ** 2 <= r * r

    def button_at(self, px, py):
        for b in self.buttons:
            if self._hit(b["region"], px, py):
                return b
        return None

    # -- interaction --------------------------------------------------------
    def press_xy(self, px, py):
        b = self.button_at(px, py)
        if b:
            self._activate(b)

    def release(self):
        if self.held:
            self.c.itemconfig(self.held["overlay"], state="hidden")
            self.send_control(self.held["name"], 0)
            self.held = None

    def _activate(self, b):
        if b["toggle"]:
            b["state"] ^= 1
            self.c.itemconfig(b["overlay"], state="normal" if b["state"] else "hidden")
            self.send_control(b["name"], b["state"])
        else:
            self.held = b
            self.c.itemconfig(b["overlay"], state="normal")
            self.send_control(b["name"], 1)

    def key_press(self, event):
        b = self.key_to_btn.get(event.keysym)
        if b and event.keysym not in self._keys_down:
            self._keys_down.add(event.keysym)
            self._activate(b)

    def key_release(self, event):
        b = self.key_to_btn.get(event.keysym)
        if b and event.keysym in self._keys_down:
            self._keys_down.discard(event.keysym)
            if b["toggle"]:
                return
            self.c.itemconfig(b["overlay"], state="hidden")
            self.send_control(b["name"], 0)
            if self.held is b:
                self.held = None

    # -- LED updates --------------------------------------------------------
    def apply_flag(self, name, val):
        for (bit, cid, gid) in self.leds.get(name, []):
            on = led_on(val, bit)
            self.c.itemconfig(cid, state="normal" if on else "hidden")
            if gid != -1:
                self.c.itemconfig(gid, state="normal" if on else "hidden")

    def all_off(self):
        for items in self.leds.values():
            for (_, cid, gid) in items:
                self.c.itemconfig(cid, state="hidden")
                if gid != -1:
                    self.c.itemconfig(gid, state="hidden")


# ---------------------------------------------------------------------------
# App: window + networking + event pump
# ---------------------------------------------------------------------------
class App:
    def __init__(self, cfg_path, host, port, refresh=False, calibrate=False):
        with open(cfg_path) as f:
            cfg = json.load(f)
        self.cfg = cfg
        self.host, self.port = host, port
        self.calibrate = calibrate
        self.q = queue.Queue()
        self.conn = None
        self.conn_lock = threading.Lock()
        self.registry = {}
        self.stop = False
        # sim-time vs wall-clock tracking: the (sim us, wall s) of the first heartbeat in the
        # current window, a count of heartbeats since, and the ratio over the last full window.
        self.hb_anchor = None
        self.hb_count = 0
        self.rate = None

        img = cfg.get("image", {})
        scale = img.get("scale", 1.0)
        crop = img.get("crop")                     # [x0,y0,x1,y1] orig pixels
        self.crop_origin = (crop[0], crop[1]) if crop else (0, 0)
        cfg_dir = os.path.dirname(os.path.abspath(cfg_path))

        title = cfg.get("title") or os.path.splitext(os.path.basename(cfg_path))[0]
        self.root = tk.Tk()
        self.root.title(f"fpga-isv · {title}" + ("  [CALIBRATE]" if calibrate else ""))
        self.root.configure(bg=BG)

        self.photo, w, h = build_photo(img, cfg_dir, refresh, scale, crop)

        self.canvas = tk.Canvas(self.root, width=w, height=h, bg=BG, highlightthickness=0)
        self.canvas.pack(side="top", fill="both", expand=True)
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")

        self.board = Board(self.canvas, cfg, scale, self.send_control, self.crop_origin)
        self.scale = scale

        strip = tk.Frame(self.root, bg=PANEL_BG)
        strip.pack(side="bottom", fill="x")
        self.status = tk.Label(strip, text="", bg=PANEL_BG, fg=TEXT,
                               font=("Consolas", 10), anchor="w", padx=10)
        self.status.pack(side="left", fill="x", expand=True)
        self.log = tk.Label(strip, text="", bg=PANEL_BG, fg=TEXT_DIM,
                            font=("Consolas", 10), anchor="e", padx=10)
        self.log.pack(side="right")
        # Simulation clock, fed by the per-message timetag + the heartbeat, and the sim/wall
        # speed (sim time advanced per second of wall-clock).
        self.clock = tk.Label(strip, text="t = —", bg=PANEL_BG, fg=TEXT,
                              font=("Consolas", 10), anchor="e", padx=10)
        self.clock.pack(side="right")
        self.rate_lbl = tk.Label(strip, text="sim/real = —", bg=PANEL_BG, fg=TEXT_DIM,
                                 font=("Consolas", 10), anchor="e", padx=10)
        self.rate_lbl.pack(side="right")

        if calibrate:
            self.canvas.bind("<Button-1>", self._calibrate_click)
            self._set_status("calibrate", "click the photo to print "
                             "original-image pixel coordinates", CAL_COLOR)
        else:
            self.canvas.bind("<ButtonPress-1>", lambda e: self.board.press_xy(e.x, e.y))
            self.canvas.bind("<ButtonRelease-1>", lambda _e: self.board.release())
            self.root.bind("<KeyPress>", self.board.key_press)
            self.root.bind("<KeyRelease>", self.board.key_release)
            self._set_status("listening", f"{host}:{port} — waiting for "
                             f"simulation (INTERACTIVE_STREAM={host}:{port})")
            threading.Thread(target=self.accept_loop, daemon=True).start()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(16, self.drain)

    # -- calibration --------------------------------------------------------
    def _calibrate_click(self, e):
        # canvas -> original-image pixels: unscale, then add crop origin
        ox = round(e.x / self.scale) + self.crop_origin[0]
        oy = round(e.y / self.scale) + self.crop_origin[1]
        print(f"x={ox}\ty={oy}")
        self.canvas.create_line(e.x - 8, e.y, e.x + 8, e.y, fill=CAL_COLOR)
        self.canvas.create_line(e.x, e.y - 8, e.x, e.y + 8, fill=CAL_COLOR)
        self.canvas.create_text(e.x + 10, e.y, text=f"{ox},{oy}", anchor="w",
                                fill=CAL_COLOR, font=("Consolas", 9))
        self.log.config(text=f"{ox}, {oy}")

    # -- networking ---------------------------------------------------------
    def accept_loop(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((self.host, self.port))
        except OSError as e:
            self.q.put(("fatal", str(e)))
            return
        srv.listen(1)
        srv.settimeout(0.5)
        while not self.stop:
            try:
                conn, peer = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self.conn_lock:
                self.conn = conn
            self.q.put(("connected", f"{peer[0]}:{peer[1]}"))
            self.read_loop(conn)
            with self.conn_lock:
                self.conn = None
            self.q.put(("disconnected", None))
        srv.close()

    def read_loop(self, conn):
        buf = b""
        while not self.stop:
            try:
                chunk = conn.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                msg = decode_message(line)
                if msg is None:
                    continue
                msg["_rx"] = time.monotonic()   # wall-clock arrival of this message
                self.q.put(("msg", msg))

    def send_control(self, name, val):
        with self.conn_lock:
            conn = self.conn
        if conn is None:
            return
        try:
            conn.sendall(encode_control(name, val))
        except OSError:
            pass

    # -- event pump ---------------------------------------------------------
    def drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                self.handle(kind, payload)
        except queue.Empty:
            pass
        if not self.stop:
            self.root.after(16, self.drain)

    def handle(self, kind, payload):
        if kind == "fatal":
            self._set_status("error", payload, WARN_AMBER)
        elif kind == "connected":
            # The sim replays its full state (reg + last flag values) on connect, so start clean:
            # forget the previous run and clear all overlays.
            self.registry.clear()
            self.board.all_off()
            self.clock.config(text="t = —")
            self.rate_lbl.config(text="sim/real = —")
            self.hb_anchor = None
            self.hb_count = 0
            self.rate = None
            self._set_status("connected", f"simulation @ {payload}", OK_GREEN)
        elif kind == "disconnected":
            self._set_status("listening", "simulation disconnected — waiting "
                             "for a new run", WARN_AMBER)
            self.board.all_off()
        elif kind == "msg":
            self.handle_msg(payload)

    def _set_clock(self, msg):
        # Every sim->viewer message carries "t" (us); show it as ms.
        t = msg.get("t")
        if t is not None:
            self.clock.config(text=f"t = {t / 1000.0:.3f} ms")

    def _update_rate(self, msg):
        # Sim/wall speed, measured over a whole window of heartbeats: the ratio is the sim-time
        # span divided by the wall-clock span from the window's first heartbeat to its last
        # (RATE_WINDOW apart). Averaging over the span -- not adjacent beats -- smooths jitter.
        t, wall = msg.get("t"), msg.get("_rx")
        if t is None or wall is None:
            return
        if self.hb_anchor is None:
            self.hb_anchor = (t, wall)
            self.hb_count = 0
            return
        self.hb_count += 1
        if self.hb_count >= RATE_WINDOW - 1:        # heartbeat #(WINDOW-1) since #0
            sim0, wall0 = self.hb_anchor
            dsim = (t - sim0) / 1e6                  # sim seconds across the window
            dwall = wall - wall0                     # wall seconds across the window
            if dsim > 0 and dwall > 0:
                self.rate = dsim / dwall             # sim seconds per wall second
                self.rate_lbl.config(text=f"sim/real = {self.rate:.1f}")
            self.hb_anchor = (t, wall)              # start the next window here
            self.hb_count = 0

    def handle_msg(self, msg):
        ev = msg.get("ev")
        name = msg.get("name", "?")
        self._set_clock(msg)
        if ev == "reg":
            self.registry[name] = msg
            self.log.config(text=f"reg {name} ({msg.get('kind')}, {msg.get('width')}b)")
        elif ev == "flag":
            val = int(msg.get("val", 0))
            width = self.registry.get(name, {}).get("width", 1)
            self.board.apply_flag(name, val)
            self.log.config(text=f"{name} = 0x{mask(val, width):x}")
        elif ev == "close":
            self.log.config(text=f"close {name}")
        elif ev == "time":
            self._update_rate(msg)

    def _set_status(self, state, detail, color=TEXT):
        self.status.config(text=f"● {state}: {detail}", fg=color)

    def on_close(self):
        self.stop = True
        with self.conn_lock:
            if self.conn:
                try:
                    self.conn.close()
                except OSError:
                    pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    ap = argparse.ArgumentParser(
        prog="fpga-isv",
        description="fpga-isv (Interactive Sim Viewer): a config-driven graphical panel "
                    "viewer for interactive-sim.")
    ap.add_argument("--version", action="version", version=f"fpga-isv {__version__}")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--config", help="board/panel config JSON")
    src.add_argument("--example", help="load a bundled example by name (see --list-examples)")
    ap.add_argument("--list-examples", action="store_true",
                    help="list bundled example names and exit")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=7777)
    ap.add_argument("--refresh", action="store_true",
                    help="re-download the board image even if cached")
    ap.add_argument("--calibrate", action="store_true",
                    help="click the photo to print pixel coordinates "
                         "(for building/adjusting the map)")
    args = ap.parse_args()

    if args.list_examples:
        for name in list_examples():
            print(name)
        return

    if bool(args.config) == bool(args.example):
        ap.error("exactly one of --config or --example is required "
                 "(see --list-examples)")

    try:
        cfg_path = args.config or example_config_path(args.example)
    except ValueError as e:
        ap.error(str(e))

    App(cfg_path, args.host, args.port, args.refresh, args.calibrate).run()


if __name__ == "__main__":
    main()
