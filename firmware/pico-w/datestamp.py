"""Date stamp for the bottom-right corner of the e-paper picture (Norwegian, e.g. "Fre 20 Nov").
Draws pre-rendered letters from datefont.py onto copies of the black/white and red planes.
Time arguments are seconds since 2000-01-01 UTC, which is what MicroPython's time.time() returns."""
import time
from datefont import GLYPHS, GH, GAP

W, H = 400, 300
MARGIN_X, MARGIN_Y, PAD = 8, 6, 3     # distance of the text from the right/bottom edge, and the white border around it
DAYS = ("Man", "Tir", "Ons", "Tor", "Fre", "Lør", "Søn")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Des")


def _last_sunday(year, month):
    """01:00 UTC on the last Sunday of a 31-day month (the EU daylight saving switch time)."""
    t = time.mktime((year, month, 31, 1, 0, 0, 0, 0))
    return t - ((time.localtime(t)[6] + 1) % 7) * 86400


def local_time(utc, offset_hours=1, eu_dst=True):
    off = offset_hours * 3600
    if eu_dst:
        y = time.localtime(utc)[0]
        if _last_sunday(y, 3) <= utc < _last_sunday(y, 10):
            off += 3600
    return time.localtime(utc + off)


def date_text(utc, offset_hours=1, eu_dst=True):
    t = local_time(utc, offset_hours, eu_dst)
    return "%s %d %s" % (DAYS[t[6]], t[2], MONTHS[t[1] - 1])


def text_width(txt):
    return sum(GLYPHS[c][0] for c in txt) + GAP * (len(txt) - 1)


def stamp(bw, red, txt):
    """Clears a small white box in the corner (in both planes) and writes txt into it."""
    x1 = W - MARGIN_X
    x0 = x1 - text_width(txt)
    y1 = H - MARGIN_Y
    y0 = y1 - GH
    for y in range(max(0, y0 - PAD), min(H, y1 + PAD)):
        for x in range(max(0, x0 - PAD), min(W, x1 + PAD)):
            i = y * W + x
            m = 0x80 >> (x & 7)
            bw[i >> 3] |= m        # white
            red[i >> 3] &= ~m      # no red
    cx = x0
    for ch in txt:
        w, rows = GLYPHS[ch]
        for ry in range(GH):
            bits = rows[ry]
            if bits:
                y = y0 + ry
                for k in range(w):
                    if (bits >> (w - 1 - k)) & 1:
                        x = cx + k
                        bw[(y * W + x) >> 3] &= ~(0x80 >> (x & 7))
        cx += w + GAP
