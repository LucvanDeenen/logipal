# G Pro Battery Overlay

A tiny always-on-top overlay that shows your Logitech G Pro mouse's battery
percentage, read directly from the Lightspeed USB receiver over the
HID++ 2.0 protocol (no G HUB required).

## Setup

1. Install Python 3.9+ if you don't have it.
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Run it:
   ```
   python battery_overlay.py
   ```

A small dark bar will appear near the top-center of your screen showing
`Mouse: 87%`. It refreshes every 2 minutes automatically.

## Controls

- **Left-click + drag**: move the overlay anywhere on screen
- **Right-click**: opens a menu with "Refresh now" and "Exit"

## Run it automatically on startup

Create a shortcut to this command and place it in your Startup folder
(`Win+R` → `shell:startup`):

```
pythonw.exe "C:\path\to\battery_overlay.py"
```

Using `pythonw.exe` instead of `python.exe` avoids popping up a console
window.

## Troubleshooting

If the overlay shows "Mouse not found" or "No reading":

1. Make sure the Lightspeed USB receiver is plugged directly into the PC
   (not through a USB hub, if possible) and the mouse is powered on.
2. Run the diagnostic mode:
   ```
   python battery_overlay.py --debug
   ```
   This lists every Logitech HID interface Windows sees and shows exactly
   where detection succeeds or fails. Paste that output if you need to dig
   further — it tells you the receiver's product string, interface number,
   and whether a battery feature was found.
3. If G HUB or Logitech Options+ is running, try closing it temporarily —
   it's not required for this script, and on rare setups two programs
   polling the same HID interface back-to-back can cause a timed-out read.
4. Older G Pro units might only expose the legacy battery feature
   (`0x1000`) instead of the newer "Unified Battery" feature (`0x1004`).
   The script already tries both automatically.

## How it works

Logitech's wireless mice/receivers speak a vendor protocol called HID++ 2.0
over a raw HID interface (this is what G HUB itself uses under the hood).
The script:

1. Finds the receiver's HID++ interface (USB VID `046D`, usage page `FF00`).
2. "Pings" device index 1 (the first paired device slot on the receiver).
3. Asks the device for its battery feature index, then queries either the
   `UNIFIED_BATTERY` (0x1004) or legacy `BATTERY_STATUS` (0x1000) feature
   for the current percentage.
4. Displays it in a small frameless, always-on-top Tkinter window.

No data leaves your machine — everything happens over local USB HID calls.
