"""
Logitech G Pro (Lightspeed) battery overlay for Windows.

Talks directly to the USB Lightspeed receiver using the Logitech HID++ 2.0
protocol (the same protocol G HUB / Logitech Options uses internally) to
read the mouse's battery percentage, and shows it in a small always-on-top
overlay window.

Usage:
    python battery_overlay.py            # run the overlay
    python battery_overlay.py --debug    # list HID devices to help troubleshoot detection
"""

import sys
import time
import queue
import threading
import tkinter as tk
from tkinter import font as tkfont

try:
    import hid
except ImportError:
    print("Missing dependency. Run: pip install hidapi")
    sys.exit(1)

LOGITECH_VID = 0x046D
HIDPP_SHORT_LEN = 7
HIDPP_LONG_LEN = 20
HIDPP_USAGE_PAGE = 0xFF00

FEATURE_ROOT = 0x0000
FEATURE_UNIFIED_BATTERY = 0x1004     # newer mice (G Pro, G Pro X Superlight, etc.)
FEATURE_BATTERY_STATUS = 0x1000      # legacy fallback

POLL_INTERVAL_SECONDS = 120


# ---------------------------------------------------------------------------
# HID++ low-level helpers
# ---------------------------------------------------------------------------

def _build_report(device_index, feature_index, function, params=b""):
    use_long = len(params) > 3
    report_id = 0x11 if use_long else 0x10
    length = HIDPP_LONG_LEN if use_long else HIDPP_SHORT_LEN
    sw_id = 0x0A  # arbitrary nonzero software id, used to match our own replies
    msg = bytearray(length)
    msg[0] = report_id
    msg[1] = device_index
    msg[2] = feature_index
    msg[3] = ((function & 0x0F) << 4) | sw_id
    for i, b in enumerate(params):
        msg[4 + i] = b
    return bytes(msg), report_id, length


def hidpp_request(device, device_index, feature_index, function, params=b"", timeout_ms=400, retries=5):
    """Send a HID++ request and wait for the matching reply, ignoring unrelated notifications."""
    msg, report_id, length = _build_report(device_index, feature_index, function, params)
    device.write(msg)

    deadline = time.time() + (timeout_ms / 1000.0) * retries
    while time.time() < deadline:
        resp = device.read(length if length == HIDPP_LONG_LEN else 32, timeout_ms=timeout_ms)
        if not resp:
            continue
        # Error reply: byte2 == 0x8F (HID++2.0) or 0xFF (HID++1.0 style error on some devices)
        if resp[1] == device_index and resp[2] in (0x8F, 0xFF) and len(resp) > 3 and resp[3] >> 4 == function:
            return None  # device replied with an explicit error
        if resp[1] == device_index and resp[2] == feature_index and (resp[3] >> 4) == function:
            return resp
    return None


def get_feature_index(device, device_index, feature_id):
    params = bytes([(feature_id >> 8) & 0xFF, feature_id & 0xFF, 0x00])
    resp = hidpp_request(device, device_index, FEATURE_ROOT, 0x00, params)
    if resp is None:
        return None
    feature_index = resp[4]
    if feature_index == 0:
        return None
    return feature_index


def ping(device, device_index):
    resp = hidpp_request(device, device_index, FEATURE_ROOT, 0x01, bytes([0x00, 0x00, 0xAA]), timeout_ms=200, retries=2)
    return resp is not None


# ---------------------------------------------------------------------------
# Device discovery
# ---------------------------------------------------------------------------

def candidate_hid_paths():
    """All Logitech vendor-specific (HID++) interfaces currently plugged in."""
    paths = []
    for d in hid.enumerate(LOGITECH_VID, 0):
        if d.get("usage_page") == HIDPP_USAGE_PAGE:
            paths.append(d["path"])
    return paths


def find_battery_source():
    """
    Try every Logitech HID++ interface and every plausible device index
    (1 = receiver's first paired device slot) until one responds and exposes
    a battery feature. Returns (path, device_index, feature_id, feature_index)
    or None.
    """
    for path in candidate_hid_paths():
        try:
            dev = hid.device()
            dev.open_path(path)
            dev.set_nonblocking(False)
        except (OSError, ValueError):
            continue

        try:
            for device_index in range(1, 7):
                if not ping(dev, device_index):
                    continue
                for feature_id in (FEATURE_UNIFIED_BATTERY, FEATURE_BATTERY_STATUS):
                    fi = get_feature_index(dev, device_index, feature_id)
                    if fi:
                        dev.close()
                        return path, device_index, feature_id, fi
        finally:
            try:
                dev.close()
            except Exception:
                pass
    return None


def read_battery(source):
    path, device_index, feature_id, feature_index = source
    dev = hid.device()
    try:
        dev.open_path(path)
        resp = hidpp_request(dev, device_index, feature_index, 0x00)
        if resp is None:
            return None
        percentage = resp[4]
        if percentage > 100:
            return None
        return percentage
    finally:
        dev.close()


# ---------------------------------------------------------------------------
# Background polling worker
# ---------------------------------------------------------------------------

class BatteryWorker(threading.Thread):
    def __init__(self, result_queue, poll_interval=POLL_INTERVAL_SECONDS):
        super().__init__(daemon=True)
        self.result_queue = result_queue
        self.poll_interval = poll_interval
        self._refresh = threading.Event()
        self._stop = threading.Event()
        self._source = None

    def run(self):
        while not self._stop.is_set():
            if self._source is None:
                self.result_queue.put(("status", "Searching for mouse..."))
                self._source = find_battery_source()

            if self._source is None:
                self.result_queue.put(("error", "Mouse not found"))
            else:
                try:
                    pct = read_battery(self._source)
                except Exception:
                    pct = None
                if pct is None:
                    self.result_queue.put(("error", "No reading"))
                    self._source = None  # force rediscovery next round
                else:
                    self.result_queue.put(("ok", pct))

            self._refresh.wait(self.poll_interval)
            self._refresh.clear()

    def request_refresh(self):
        self._refresh.set()

    def stop(self):
        self._stop.set()
        self._refresh.set()


# ---------------------------------------------------------------------------
# Overlay UI
# ---------------------------------------------------------------------------

class OverlayApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-alpha", 0.88)
        except tk.TclError:
            pass

        screen_w = self.root.winfo_screenwidth()
        win_w, win_h = 150, 32
        x = (screen_w - win_w) // 2
        y = 8
        self.root.geometry(f"{win_w}x{win_h}+{x}+{y}")
        self.root.configure(bg="#1a1a1a")

        label_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.label = tk.Label(
            self.root, text="Mouse: --", fg="white", bg="#1a1a1a", font=label_font
        )
        self.label.pack(expand=True, fill="both")

        self.label.bind("<ButtonPress-1>", self._start_move)
        self.label.bind("<B1-Motion>", self._do_move)
        self.label.bind("<Button-3>", self._show_menu)
        self._offset = (0, 0)

        self.result_queue = queue.Queue()
        self.worker = BatteryWorker(self.result_queue)
        self.worker.start()

        self._poll_queue()

    def _start_move(self, event):
        self._offset = (event.x, event.y)

    def _do_move(self, event):
        ox, oy = self._offset
        x = self.root.winfo_pointerx() - ox
        y = self.root.winfo_pointery() - oy
        self.root.geometry(f"+{x}+{y}")

    def _show_menu(self, event):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Refresh now", command=self.worker.request_refresh)
        menu.add_command(label="Exit", command=self._exit)
        menu.tk_popup(event.x_root, event.y_root)

    def _exit(self):
        self.worker.stop()
        self.root.destroy()

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.result_queue.get_nowait()
                if kind == "ok":
                    self.label.config(text=f"Mouse: {payload}%", fg="white")
                elif kind == "status":
                    self.label.config(text=payload, fg="#aaaaaa")
                else:
                    self.label.config(text=f"Mouse: {payload}", fg="#ff6b6b")
        except queue.Empty:
            pass
        self.root.after(300, self._poll_queue)

    def run(self):
        self.root.mainloop()


# ---------------------------------------------------------------------------
# Debug helper
# ---------------------------------------------------------------------------

def debug_dump():
    print(f"Logitech (VID 0x{LOGITECH_VID:04X}) HID interfaces found:\n")
    for d in hid.enumerate(LOGITECH_VID, 0):
        print(
            f"  path={d['path']!r}\n"
            f"    product={d.get('product_string')!r} "
            f"interface={d.get('interface_number')} "
            f"usage_page=0x{d.get('usage_page', 0):04X} "
            f"usage=0x{d.get('usage', 0):04X}\n"
        )
    print("Attempting battery-source discovery...")
    source = find_battery_source()
    if source is None:
        print("No responsive HID++ battery feature found. "
              "Make sure the receiver is plugged in and the mouse is powered on.")
        return
    path, device_index, feature_id, feature_index = source
    print(f"Found source: path={path!r} device_index={device_index} "
          f"feature_id=0x{feature_id:04X} feature_index={feature_index}")
    pct = read_battery(source)
    print(f"Battery reading: {pct}%")


if __name__ == "__main__":
    if "--debug" in sys.argv:
        debug_dump()
    else:
        OverlayApp().run()
