"""SSD1683 driver for the Good Display 4.2" black/white/red panel (GDEY042Z98) on a DESPI-C02.
Only this file knows about the panel; a different display later means swapping this file.
Wiring: BUSY GP22, RES GP21, D/C GP20, CS GP17, SCK GP18, SDI GP19, 3.3V, GND.
Plane format: 400x300, 1 bit per pixel, MSB = leftmost pixel, 50 bytes per row.
  bw plane : 1 = white, 0 = black     red plane : 1 = red
"""
from machine import Pin, SPI
import time

W, H = 400, 300

# ---- settings -------------------------------------------------------------
FAST_BW = True    # True : updates that involve no red use the fast black/white refresh (about 1.3 s).
                  # False: every update is a full refresh (10 to 25 s), exactly as before.
FULL_EVERY = 10   # after this many fast updates in a row, do one full refresh to clear ghosting
# ESP32-S3 Super Mini wiring: GPIO numbers (the numbers printed on the board, not pin positions)
PIN_BUSY = 1
PIN_RES = 2
PIN_DC = 4
PIN_CS = 5
PIN_SCK = 6
PIN_SDI = 7       # the adapter's SDI (MOSI) pin
# ---------------------------------------------------------------------------


class EPD:
    def __init__(self, hook=None):
        self.spi = SPI(1, baudrate=4_000_000, polarity=0, phase=0, sck=Pin(PIN_SCK), mosi=Pin(PIN_SDI))
        self.cs = Pin(PIN_CS, Pin.OUT, value=1)
        self.dc = Pin(PIN_DC, Pin.OUT, value=0)
        self.rst = Pin(PIN_RES, Pin.OUT, value=1)
        self.busy = Pin(PIN_BUSY, Pin.IN)
        self.hook = hook  # called while waiting, so the network connection stays alive
        self.shown_bw = None      # copy of what is on the screen; None = unknown (after boot or an error)
        self.shown_red = None
        self.shown_has_red = False
        self.fast_count = 0

    def _cmd(self, c, *data):
        self.cs(0); self.dc(0); self.spi.write(bytes([c]))
        if data:
            self.dc(1); self.spi.write(bytes(data))
        self.cs(1)

    def _wait(self, what, timeout_ms=40000):
        t = time.ticks_ms()
        while self.busy():
            if self.hook:
                self.hook()
            if time.ticks_diff(time.ticks_ms(), t) > timeout_ms:
                raise OSError("e-paper BUSY stuck during " + what)
            time.sleep_ms(50)

    def _ram(self):
        self._cmd(0x11, 0x03)
        self._cmd(0x44, 0, (W - 1) // 8)
        self._cmd(0x45, 0, 0, (H - 1) & 255, (H - 1) >> 8)
        self._cmd(0x4E, 0)
        self._cmd(0x4F, 0, 0)

    def _plane(self, c, buf):
        self._ram()
        self.cs(0); self.dc(0); self.spi.write(bytes([c]))
        self.dc(1); self.spi.write(buf); self.cs(1)

    def _wake(self):
        self.rst(0); time.sleep_ms(10); self.rst(1); time.sleep_ms(10)
        self._cmd(0x12); time.sleep_ms(10); self._wait("reset")
        self._cmd(0x01, (H - 1) & 255, (H - 1) >> 8, 0x00)
        self._cmd(0x3C, 0x05)
        self._cmd(0x18, 0x80)

    def update(self, bw, red, force=False):
        """Show the picture. Returns "fast", "full", or None when nothing needed to change.
        Fast refresh is used only when neither the new picture nor the one on the screen has red,
        the screen content is known, and FULL_EVERY fast updates have not been reached yet."""
        has_red = any(red)
        if (not force and self.shown_bw is not None and bw == self.shown_bw and red == self.shown_red):
            return None
        fast = (FAST_BW and self.shown_bw is not None and not has_red and not self.shown_has_red
                and self.fast_count < FULL_EVERY)
        old_bw = self.shown_bw
        self.shown_bw = None   # unknown until this update has finished, so a failure forces a full refresh next time
        self._wake()
        if fast:
            self._plane(0x26, old_bw)   # RAM 0x26 holds the "previous" picture for the black/white update
            self._plane(0x24, bw)       # RAM 0x24 holds the new picture
            self._cmd(0x22, 0xDC); self._cmd(0x20)
            self._wait("fast refresh")
            self.fast_count += 1
        else:
            self._plane(0x24, bw)
            self._plane(0x26, red)
            self._cmd(0x22, 0xF7); self._cmd(0x20)
            self._wait("refresh")
            self.fast_count = 0
        self._cmd(0x10, 0x11)  # deep sleep, the image stays
        self.shown_bw = bytearray(bw)
        self.shown_red = bytearray(red)
        self.shown_has_red = has_red
        return "fast" if fast else "full"
