# Aurora: a note display

Draw on a web page (iPad with Apple Pencil, computer or phone) and the drawing appears on an e-paper display on a desk somewhere else. The display keeps the picture without power, remembers the last drawing even if it was unplugged while you drew, and joins Wi-Fi networks it has been set up with.

The page and the display talk through a free cloud MQTT broker. Nothing else is running anywhere: the page is a static site on GitHub Pages, and the broker keeps the latest drawing as retained messages.

```
  web page (GitHub Pages)  <--- WSS 8084 --->  EMQX Cloud Serverless  <--- MQTTS 8883 --->  ESP32-S3 + e-paper
  draw, paste pictures,                         stores the drawing and                      shows the drawing,
  turn the date on/off                          settings (retained)                         stamps the date
```

## What it does

- **Drawing:** black and red pens, eraser, three sizes, undo, clear. Nothing is sent while you draw; the page sends 10 seconds after you stop.
- **Pictures:** paste (Ctrl+V), pick a file or take a photo. The picture floats above the page: drag it, pinch or scroll to resize, drag the corner, then place it. Text pictures get a sharp threshold, photos get dithering (auto, text or photo mode). Black and white by default, red on request.
- **Date:** a small switch shows the date in the bottom-right corner of the display in Norwegian, for example "Fre 20 Nov". The display draws it itself and updates it at midnight.
- **Fast updates:** pictures without red refresh in about 1.3 seconds. Pictures with red take 10 to 25 seconds, with a lot of flashing. This is how three-colour e-paper works.
- **Several displays:** every account has its own topic space (`penpad/<username>/`), so each display is separate. Add as many accounts as you need.
- **Phone setup:** the display opens its own Wi-Fi network to set up Wi-Fi and account from a phone (see below), and remembers up to 10 networks.
- **Home-screen app:** the page can be added to the iPad's home screen and opens full screen with its own icon.

## Hardware

| Part | Used here |
|---|---|
| Display | Good Display GDEY042Z98, 4.2 inch, 400x300, black/white/red, SSD1683, on a DESPI-C02 adapter |
| Board | ESP32-S3 Super Mini (also tested in simulation for a Raspberry Pi Pico W, see `firmware/pico-w`) |
| Power | USB charger, 5 V |

Wiring (GPIO numbers are the numbers printed on the board; match the labels on the adapter, not the pin order):

| Adapter | ESP32-S3 Super Mini |
|---|---|
| BUSY | GPIO1 |
| RES | GPIO2 |
| D/C | GPIO4 |
| CS | GPIO5 |
| SCK | GPIO6 |
| SDI | GPIO7 |
| 3.3V | 3V3 |
| GND | GND |

Never connect 5 V to the adapter. The pins are set at the top of `epd.py`. Avoid GPIO3, 45 and 46 (boot), 19 and 20 (USB) and 43 and 44 (serial console).

## Repository layout

```
index.html, config.js, mqtt.min.js, manifest.webmanifest, icons, CNAME   the website (must stay in the root for GitHub Pages)
firmware/esp32s3/                                                          the firmware for the ESP32-S3
firmware/pico-w/                                                           older Pico W version, without the setup page
firmware/*/tools/                                                          scripts that generate the bitmap fonts (they write to the folder above)
```

`secrets.py` and `config.json` hold passwords and must never be committed. Keep `secrets.example.py` in the repository, and the real `secrets.py` only on the board. `.gitignore` should list both.

## Setting it up

### 1. The broker (EMQX Cloud Serverless)

1. Create a Serverless deployment in a region close to you.
2. Under Authentication, add one account per display. Use long, unique passwords.
3. Under Access Control, Authorization: add a rule for All Users that denies publish and subscribe on topic `#`, then for each account an allow rule on `penpad/<username>/#`. The menu names may differ slightly. Log in with each account to check.
4. Download the CA certificate from the deployment's Overview page.

### 2. The website

1. Put the website files in the root of a GitHub repository and turn on GitHub Pages for it.
2. Edit `config.js` once: `window.PENPAD_BROKER = "your-address.emqxsl.com";`. The address is not secret (the page needs it); the accounts and the access rules protect the data.
3. For your own domain: four A records pointing at GitHub Pages (DNS only, not proxied) and a `CNAME` file with the domain.
4. On an iPad: open the page in Safari, Share, Add to Home Screen.

### 3. The display

1. Flash MicroPython for the ESP32-S3: download the plain `ESP32_GENERIC_S3` firmware from micropython.org, hold BOOT while plugging in USB, and flash it with Thonny (Options, Interpreter, MicroPython ESP32, install with "erase flash") or with `esptool`. Use `write_flash 0 firmware.bin`.
2. Copy to the board: `main.py`, `epd.py`, `datestamp.py`, `datefont.py`, `store.py`, `portal.py`, `screen.py`, `textfont.py`, your `secrets.py` and the CA file as `ca.crt` (or `ca.pem`). Do not copy the tools folder.
3. `secrets.py` needs only `MQTT_HOST` and `MQTT_PORT`. Optional lines: `UTC_OFFSET_HOURS = 1`, `EU_DST = True`, `VERIFY_TLS = True`. Wi-Fi and account normally come from the setup page, but `WIFI_SSID`, `WIFI_PASSWORD`, `MQTT_USER` and `MQTT_PASS` work as first-time defaults.
4. If `import umqtt.simple` fails, run once on the board: `import mip; mip.install("umqtt.simple")`.

## The phone setup page

The display opens its own Wi-Fi network, **Aurora-Setup**, when:

- it has no Wi-Fi and account yet (first start),
- no remembered Wi-Fi has been in range for 5 minutes (it was moved),
- a remembered Wi-Fi is in range but there is no connection for 30 minutes,
- the username or password is refused,
- you hold the BOOT button for 3 seconds while the board is running.

The e-paper then shows the network name, a fresh 8-digit password and the steps:

1. Join Aurora-Setup with the password shown on the display.
2. The setup page opens by itself. If not, open `192.168.4.1` in the browser.
3. Choose the Wi-Fi network (or type its name), enter its password, check the account and press Lagre.

The board restarts and tries the new settings. A wrong Wi-Fi password removes that network again and shows the steps once more; a wrong account does the same. The board remembers up to 10 networks and joins the strongest one in range. If nobody opens the page within 10 minutes it closes, and it does not reopen by itself for an hour (hold BOOT to open it at once). The saved picture comes back when the connection is back. Saved settings are in `config.json` on the board, in plain text.

## Settings you may want to change

| Where | What |
|---|---|
| `epd.py` | `FAST_BW` (fast black/white refresh), `FULL_EVERY` (full refresh after this many fast ones), the `PIN_*` wiring |
| `main.py` | `PIN_SETUP_BTN` (the BOOT button), the setup timeouts at the top |
| `index.html` | `AUTO_SEND_MS` (send delay), the texts in the `T` object, the quote on the login card |
| `tools/make_textfont.py` (esp32s3), `tools/make_datefont.py` | font, size and weight of the setup screen and the date, then regenerate and upload |

## Limits and notes

- 2.4 GHz Wi-Fi only. Hidden networks are not found by scanning, but can be typed in once.
- The status lamp on the page can show "online" for about 45 seconds after the display is unplugged. That is the broker waiting before it announces the last-will message.
- The date is drawn on top of the picture in a small white box. It stays hidden until the display has set its clock from the internet.
- Pictures with red take a full refresh; once red is on the page, every update is a full one.
- The broker stores the drawing. Treat it as private but not secret, and use strong account passwords.
- The firmware was developed with a simulator and then tried on the real board; report anything odd you find.

## Licences

`mqtt.min.js` is MQTT.js (MIT licence, `mqtt-LICENSE.txt`). The bitmap fonts are generated from Liberation Sans (SIL Open Font Licence).
