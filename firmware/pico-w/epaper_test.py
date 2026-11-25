"""Test for a Good Display 4.2" black/white/red e-paper (GDEY042Z98, SSD1683, 400x300)
on a DESPI-C02 adapter board, driven by a Raspberry Pi Pico / Pico W.
Init and update sequence follow the open-source GxEPD2 driver for this panel.

Wiring (match the LABELS printed on the DESPI-C02, not the order):
  BUSY -> GP22   RES -> GP21   D/C -> GP20   CS -> GP17
  SCK  -> GP18   SDI -> GP19 (MOSI)
  3.3V -> 3V3(OUT) pin 36      GND -> any GND pin
Never connect 5 V.
"""
from machine import Pin, SPI
import time, framebuf

W, H = 400, 300
RED_ACTIVE_HIGH = True   # if the WHOLE screen turns red, set this to False and run again

spi = SPI(0, baudrate=4_000_000, polarity=0, phase=0, sck=Pin(18), mosi=Pin(19))
cs = Pin(17, Pin.OUT, value=1)
dc = Pin(20, Pin.OUT, value=0)
rst = Pin(21, Pin.OUT, value=1)
busy = Pin(22, Pin.IN)


def cmd(c, *data):
    cs(0); dc(0); spi.write(bytes([c]))
    if data:
        dc(1); spi.write(bytes(data))
    cs(1)


def wait_busy(what, timeout_ms=40000):
    t = time.ticks_ms()
    while busy():  # SSD1683: BUSY is high while working
        if time.ticks_diff(time.ticks_ms(), t) > timeout_ms:
            raise OSError("BUSY stuck high during '%s': check wiring, or the panel uses a different chip" % what)
        time.sleep_ms(20)


def set_ram():
    cmd(0x11, 0x03)                          # x and y increase
    cmd(0x44, 0, (W - 1) // 8)               # x range in bytes
    cmd(0x45, 0, 0, (H - 1) & 255, (H - 1) >> 8)
    cmd(0x4E, 0)
    cmd(0x4F, 0, 0)


def init():
    rst(0); time.sleep_ms(10); rst(1); time.sleep_ms(10)
    cmd(0x12); time.sleep_ms(10); wait_busy("software reset")
    cmd(0x01, (H - 1) & 255, (H - 1) >> 8, 0x00)   # driver output control
    cmd(0x3C, 0x05)                                  # border waveform
    cmd(0x18, 0x80)                                  # internal temperature sensor
    set_ram()


def write_plane(c, buf):
    set_ram()
    cs(0); dc(0); spi.write(bytes([c])); dc(1); spi.write(buf); cs(1)


def refresh():
    cmd(0x22, 0xF7); cmd(0x20)
    wait_busy("refresh")


NO_RED = 0 if RED_ACTIVE_HIGH else 1
RED = 1 - NO_RED
n = W * H // 8
bw, red = bytearray(n), bytearray(n)
fb = framebuf.FrameBuffer(bw, W, H, framebuf.MONO_HLSB)   # 1 = white, 0 = black
fr = framebuf.FrameBuffer(red, W, H, framebuf.MONO_HLSB)
fb.fill(1); fr.fill(NO_RED)
fb.rect(0, 0, W, H, 0); fb.rect(4, 4, W - 8, H - 8, 0)
fb.line(0, 0, W - 1, H - 1, 0)
fb.text("Desk e-paper test", 20, 20, 0)
fb.fill_rect(20, 60, 80, 80, 0)          # black square (top-left area)
fr.fill_rect(140, 60, 80, 80, RED)       # red square
fr.text("RED", 150, 160, RED)

print("initialising...")
init()
print("sending image...")
write_plane(0x24, bw)
write_plane(0x26, red)
print("refreshing (10 to 25 s, the screen will flash)...")
refresh()
cmd(0x10, 0x11)   # deep sleep, the image stays
print("done")
