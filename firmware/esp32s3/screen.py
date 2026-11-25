"""Draws the setup instructions (Norwegian) into the two e-paper planes. Only imported when setup is needed."""
import textfont

W, H = 400, 300
REASONS = {
    "new": "Velkommen! Skjermen trenger Wi-Fi og en konto.",
    "nowifi": "Fant ikke noe Wi-Fi jeg kjenner.",
    "wifipw": "Kunne ikke koble til. Sjekk navn og passord.",
    "auth": "Feil brukernavn eller passord.",
    "button": "Oppsett startet med knappen.",
}
STEP1 = "1. Koble mobilen til dette Wi-Fi-et:"
STEP2 = "2. Siden åpner seg selv. Hvis ikke, skriv 192.168.4.1 i nettleseren."
STEP3 = "3. Velg Wi-Fi og trykk Lagre."


def width(txt, font):
    glyphs, gap = font[2], font[1]
    return sum(glyphs.get(c, glyphs["?"])[0] for c in txt) + gap * (len(txt) - 1)


def draw(bw, red, x, y, txt, font, color):
    """color 'k' = black, 'r' = red. Returns the x where the text ended."""
    gh, gap, glyphs = font
    for ch in txt:
        w, rows = glyphs.get(ch, glyphs["?"])
        for ry in range(gh):
            bits = rows[ry]
            if bits:
                py = y + ry
                if not 0 <= py < H:
                    continue
                for k in range(w):
                    if (bits >> (w - 1 - k)) & 1:
                        px = x + k
                        if 0 <= px < W:
                            i = (py * W + px) >> 3
                            m = 0x80 >> (px & 7)
                            if color == "r":
                                red[i] |= m
                            else:
                                bw[i] &= ~m
        x += w + gap
    return x


def wrap(txt, font, maxw):
    lines, cur = [], ""
    for word in txt.split(" "):
        t = word if not cur else cur + " " + word
        if cur and width(t, font) > maxw:
            lines.append(cur)
            cur = word
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


def setup_screen(reason, ap_name, ap_pw, ip="192.168.4.1"):
    bw = bytearray(b"\xff" * (W * H // 8))
    red = bytearray(W * H // 8)
    big, body = textfont.BIG, textfont.BODY
    X, MAXW = 14, W - 28
    y = 8
    draw(bw, red, X, y, "Sett opp skjermen", big, "r")
    y += big[0] + 3
    note = REASONS.get(reason)
    if note:
        for line in wrap(note, body, MAXW):
            draw(bw, red, X, y, line, body, "k")
            y += body[0] + 1
    y += 7
    for line in wrap(STEP1, body, MAXW):
        draw(bw, red, X, y, line, body, "k")
        y += body[0] + 1
    draw(bw, red, X + 22, y, ap_name, big, "r")
    y += big[0] + 1
    x = draw(bw, red, X + 22, y + 9, "Passord ", body, "k")
    draw(bw, red, x, y, ap_pw, big, "k")
    y += big[0] + 8
    for step in (STEP2, STEP3):
        for line in wrap(step, body, MAXW):
            draw(bw, red, X, y, line, body, "k")
            y += body[0] + 1
        y += 3
    return bw, red
