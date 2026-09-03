# SoleCAN — user guide

SoleCAN is a small box that plugs into your Solectrac e25G's diagnostic port
and shows you what the tractor is actually doing: battery state of charge,
pack voltage and current, motor RPM and power, charger status, cell voltages,
temperatures, and any faults the battery or motor controller is reporting.

It only listens. The firmware prevents it from sending any signals on the CAN
bus.

To view the dashboard, you can either use:

1. Any browser over WiFi
2. The Android app over Bluetooth

## 1. Plug it in

The tractor's diagnostic port is the OBD-II connector (the trapezoid-shaped
16-pin socket) under the hood. Push the plug in until it seats.

The device is powered by the tractor, and is on whenever the 12V system is
live. On my tractor the 12V system is live during in key off, but not when the
shutoff switch is turned off.

A green LED is illuminated whenever the device is on and a blue LED flashes
with CAN traffic. The device draws around 0.04 A when active and 0.001 A when
asleep, which is entered after a period of CAN inactivity.

## 2. Connect your phone or laptop

### Over WiFi

The device broadcasts its own WiFi hotspot. Connect with:

| | |
|---|---|
| Network | `tractor` |
| Password | `electricity` |

Your phone will warn that this network has no internet — that's expected, stay
connected to it.

Then open a browser to:

**http://tractor.local**

If that doesn't load (some Android phones and older devices don't resolve
`.local` names), use **http://192.168.4.1** instead.

On a phone you may need to enable airplane mode so your cell connection doesn't
try to resolve the address.

### Over Bluetooth (the Android app)

The app shows the same dashboard without joining the tractor's WiFi, so your
phone keeps its internet connection, there's no airplane-mode dance, and it
connects a bit faster.

1. Download `app-debug.apk` from the
   [Releases page](https://github.com/schmmd/soleCAN/releases) onto the phone.
2. Open it. Android will ask you to allow installing from your browser or file
   manager — this is the normal sideloading prompt for an app that isn't from
   the Play Store. Allow it, then install.
3. Open **Tractor Dashboard**. Grant the Bluetooth permission it asks for (on Android
   11 and older it asks for Location instead — Android requires that for any
   Bluetooth scan; the app doesn't use your location).
4. The Android app scans and connects on its own. A status line at the top
   shows progress and disappears once connected.

There's no pairing step and nothing to choose — turn on the tractor, open the
app, and it finds the device.

## 3. The other pages (WiFi only)

The links at the bottom of the dashboard page go to the other pages below. **In
the Android app those pages aren't reachable** — the app mirrors the dashboard
only. For logs, WiFi setup, or pulling recordings, connect over WiFi.

| Page | What it's for |
|---|---|
| `http://tractor.local/logs` | The device's own log — boot messages, WiFi and CAN events. |
| `http://tractor.local/wifi` | Put the device on your shop's WiFi so you can reach it without switching networks. |
| `http://tractor.local/json` | The same data as the dashboard, as JSON, for scripting or logging. |
| `http://tractor.local/usb` | Switches what the USB port does. |

### Putting it on your own WiFi

Open `/wifi`, enter your network's name and password plus the hotspot password
(`electricity` by default), and save. It takes effect immediately, no reboot.

The `tractor` hotspot always stays on, so if you mistype something you can
still get back in and fix it. Note the name is case- and space-sensitive, and
the device does not check the network exists before saving — a typo just means
it never connects, and you re-enter it.

The setting survives power cycles and firmware updates.

## 4. Recording a session

There are two ways to record raw CAN traffic for later analysis.

### MicroSD card

If your device has a microSD card slot and a FAT-formatted card is in it **at
power-on**, it records the whole session automatically — every CAN frame plus a
once-per-second snapshot of the dashboard.

- Each power-on becomes its own folder on the card (`s00001/`, `s00002/`, …).
- When the card gets close to full, the oldest session is deleted to make room.
- A card put in *after* the device booted is ignored — power-cycle to use it.

To retrieve a recording, either pop the card into a computer, or open
`http://tractor.local/sd` over WiFi — it lists what's on the card, shows
whether recording is running, and lets you download each session.

### CAN Capture Android app

[CAN Capture](https://github.com/schmmd/can-capture) records the live CAN
stream over the tractor's WiFi and saves it on your phone as a Vector ASCII
(`.asc`) file, which SavvyCAN and most CAN tools can open. It works on any
unit, with or without an SD card.

1. Install the APK from the
   [Releases page](https://github.com/schmmd/can-capture/releases) (the same
   sideloading prompt as the dashboard app).
2. Join the `tractor` WiFi network.
3. In the app's **Settings**, enter host `tractor.local` (or `192.168.4.1`),
   port `28600`, and bus `can0`.
4. **Record → Start**. Stop when you're done and give the capture a name.

Captures appear on the **Captures** tab and can be shared with the Android
share sheet (Drive, Gmail, Files, …). Keep the app in the foreground while
recording — Android may kill it in the background and end the capture.

## 5. What the lights mean

**Two small LEDs (yellow + blue), plus a green power light:**

| | |
|---|---|
| Green on | The device has power. |
| Blue blinking | CAN data is arriving — everything's working. |
| Blue off | No data recently. Normal with the tractor off. |
| Yellow slow blink | Still starting up / waiting on WiFi. |
| Yellow off | Network is up. Normal. |
| Yellow flicker | Hydraulic controller data arriving (if Kelly connected and enabled). |

## Troubleshooting

**The `tractor` network doesn't appear.** The device has no power, or it's
asleep. Check the OBD-II plug is fully seated. After about 10 minutes of a
silent bus (tractor off) the device sleeps to save battery and drops the
hotspot; it wakes on its own when CAN traffic resumes or power is cut and
then restored.

**Page loads but every value is blank or dashes.** The device is running but
seeing no CAN traffic. Turn the key on. If the tractor is on and values stay
blank, check `/logs` and the LEDs — a blue LED that never blinks means the CAN
wiring isn't right.

**I changed the WiFi and now can't find the device.** Join the `tractor`
hotspot — it's always on regardless of what you set — and fix it at `/wifi`.

**The app says "scanning" and never connects.** Check the tractor is on (the
device sleeps with the bus quiet), that Bluetooth is on, and that you granted
the permission it asked for — if you denied it, Android won't ask again; turn
it on under Settings → Apps → Tractor Dashboard → Permissions.
