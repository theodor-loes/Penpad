"""SSD1683 driver for the Good Display 4.2" black/white/red panel (GDEY042Z98) on a DESPI-C02.
Only this file knows about the panel; a different display later means swapping this file.
Wiring: BUSY GP22, RES GP21, D/C GP20, CS GP17, SCK GP18, SDI GP19, 3.3V, GND.
Plane format: 400x300, 1 bit per pixel, MSB = leftmost pixel, 50 bytes per row.
  bw plane : 1 = white, 0 = black     red plane : 1 = red
"""
from machine import Pin, SPI
import time

W, H = 400, 300


class EPD:
    def __init__(self, hook=None):
        self.spi = SPI(0, baudrate=4_000_000, polarity=0, phase=0, sck=Pin(18), mosi=Pin(19))
        self.cs = Pin(17, Pin.OUT, value=1)
        self.dc = Pin(20, Pin.OUT, value=0)
        self.rst = Pin(21, Pin.OUT, value=1)
        self.busy = Pin(22, Pin.IN)
        self.hook = hook  # called while waiting, so the network connection stays alive

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

    def show(self, bw, red):
        """Full refresh, takes 10 to 25 seconds. Wakes the panel, draws, puts it back to sleep."""
        self.rst(0); time.sleep_ms(10); self.rst(1); time.sleep_ms(10)
        self._cmd(0x12); time.sleep_ms(10); self._wait("reset")
        self._cmd(0x01, (H - 1) & 255, (H - 1) >> 8, 0x00)
        self._cmd(0x3C, 0x05)
        self._cmd(0x18, 0x80)
        self._plane(0x24, bw)
        self._plane(0x26, red)
        self._cmd(0x22, 0xF7); self._cmd(0x20)
        self._wait("refresh")
        self._cmd(0x10, 0x11)  # deep sleep, the image stays
