# Pico W version

The first version of the firmware, for a Raspberry Pi Pico W with the e-paper display. It does the same drawing,
fast black/white refresh, date stamp and certificate check as the ESP32-S3 version in `../esp32s3`,
but it has **no phone setup page** and remembers only one Wi-Fi network (the one in `secrets.py`).

Wiring (Pico GPIO numbers): BUSY GP22, RES GP21, D/C GP20, CS GP17, SCK GP18, SDI GP19, 3.3V to pin 36, GND to any GND pin.
Never connect 5 V to the adapter.

Upload to the Pico: `main.py`, `epd.py`, `datestamp.py`, `datefont.py`, the CA file (`ca.pem` here is DigiCert Global Root G2;
use the CA file from your EMQX overview page if the connection fails, named `ca.pem`) and your own `secrets.py`
made from `secrets.example.py`. Never commit `secrets.py`.

`epaper_test.py` draws a test picture (black square, red square) to check the wiring.
`tools/make_datefont.py` regenerates `datefont.py` (it writes to the folder above `tools`).
`old-pimoroni-display/` is the very first version for a Pimoroni Pico Display 2.0 (colour LCD), kept for reference.
