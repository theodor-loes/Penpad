"""Joining remembered Wi-Fi networks, and the setup portal.
The portal makes the board open its own Wi-Fi network; a phone joining it gets a page (Norwegian) where
the Wi-Fi network and the display account can be set. The board then saves them and restarts."""
import gc, os, time, socket, select, network
import store

AP_NAME = "Aurora-Setup"
AP_IP = "192.168.4.1"
IDLE_S = 600           # the setup network closes after this long without any visit


# ---- finding and joining known networks ------------------------------------
def scan_list(wlan):
    """Visible networks as [(ssid, rssi, authmode)], strongest first. Hidden networks are left out."""
    wlan.active(True)
    try:
        raw = wlan.scan()
    except Exception:
        return []
    best = {}
    for t in raw:
        try:
            ssid = t[0].decode()
        except Exception:
            continue
        if ssid and (ssid not in best or t[3] > best[ssid][0]):
            best[ssid] = (t[3], t[4])
    return sorted([(s, v[0], v[1]) for s, v in best.items()], key=lambda n: -n[1])


def _try(wlan, ssid, pw, seconds, tick):
    wrong = getattr(network, "STAT_WRONG_PASSWORD", None)
    wlan.disconnect()
    wlan.connect(ssid, pw)
    for _ in range(seconds * 5):
        if wlan.isconnected():
            return True
        if wrong is not None and wlan.status() == wrong:
            break
        time.sleep_ms(200)
        if tick:
            tick()
    wlan.disconnect()
    return False


def connect_known(wlan, cands, prefer=None, only=False, seconds=75, per_try=15, tick=None):
    """Joins the strongest remembered network that is in range. cands = [(ssid, password)].
    only=True tries just `prefer`, even if the scan does not show it (a hidden network).
    Returns (ssid or None, seen) where seen says whether any remembered network was in range."""
    wlan.active(True)
    known = dict(cands)
    seen = False
    t0 = time.ticks_ms()
    while True:
        if only:
            order = [prefer] if prefer in known else []
            seen = bool(order)
        else:
            order = [s for s, _, _ in scan_list(wlan) if s in known]
            seen = seen or bool(order)
            if prefer in order:
                order.remove(prefer)
                order.insert(0, prefer)
        for ssid in order:
            print("WiFi: trying", ssid)
            if _try(wlan, ssid, known[ssid], per_try, tick):
                return ssid, True
        if only or time.ticks_diff(time.ticks_ms(), t0) > seconds * 1000:
            return None, seen
        for _ in range(15):          # rest a moment, then scan again
            time.sleep_ms(200)
            if tick:
                tick()


# ---- small web helpers ------------------------------------------------------
def urldecode(s):
    s = s.replace("+", " ")
    parts = s.split("%")
    out = bytearray(parts[0].encode())
    for p in parts[1:]:
        try:
            out.append(int(p[:2], 16))
            out += p[2:].encode()
        except ValueError:
            out += b"%" + p.encode()
    try:
        return bytes(out).decode()
    except Exception:
        return s


def parse_form(body):
    form = {}
    for pair in body.split("&"):
        k, _, v = pair.partition("=")
        if k:
            form[urldecode(k)] = urldecode(v)
    return form


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def dns_reply(data, ip):
    """Answers every name with our own address, so a phone's 'sign in to Wi-Fi' check opens the setup page."""
    if len(data) < 12 or data[2] & 0x80:
        return None
    i = 12
    while i < len(data) and data[i]:
        i += data[i] + 1
    end = i + 5                                   # the zero byte, then type and class (2 bytes each)
    if end > len(data):
        return None
    ans = (data[i + 1] << 8 | data[i + 2]) == 1   # only A records get an address
    out = data[:2] + b"\x81\x80\x00\x01" + (b"\x00\x01" if ans else b"\x00\x00") + b"\x00\x00\x00\x00" + data[12:end]
    if ans:
        out += b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04" + bytes(int(x) for x in ip.split("."))
    return out


# ---- the page ---------------------------------------------------------------
CSS = ("body{margin:0;padding:18px;font:16px Georgia,serif;color:#1b1b1f;background:#a9753f}"
       ".c{max-width:400px;margin:0 auto;background:#fbf9f3;border-radius:4px;padding:22px 22px 18px;box-shadow:0 6px 20px rgba(0,0,0,.35)}"
       "h1{margin:0;font-size:24px;font-style:italic}h2{font-size:12px;letter-spacing:.14em;text-transform:uppercase;opacity:.6;margin:22px 0 6px}"
       "label{display:block;font-size:14px;margin:12px 0 0}"
       "input,select{width:100%;box-sizing:border-box;font:inherit;font-size:16px;padding:9px 2px;border:0;border-bottom:1.5px solid #888;background:transparent;outline:0;border-radius:0}"
       "button{width:100%;margin-top:22px;font:inherit;padding:11px;border:0;border-radius:4px;background:#1b1b1f;color:#fbf9f3}"
       "button.g{background:transparent;color:#1b1b1f;border:1px solid #888;margin-top:10px;font-size:14px}"
       ".m{margin:14px 0 0;padding:9px 11px;border-radius:4px;background:#f6dcd6;color:#7a1c10}.m.ok{background:#dcebd4;color:#1e4d12}"
       ".s{font-size:13px;opacity:.7}a{color:#1b1b1f}")


class Portal:
    def __init__(self, cfg, nets):
        self.cfg, self.nets, self.saved = cfg, nets, False

    def page(self, msg="", ok=False):
        cfg = self.cfg
        user = store.account(cfg)[0]
        known = [s for s, _ in store.wifis(cfg)]
        h = ["<!doctype html><html lang=nb><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>",
             "<title>Aurora</title><style>", CSS, "</style><div class=c><h1>Aurora</h1><div class=s>Sett opp skjermen</div>"]
        if msg:
            h.append("<div class='m%s'>%s</div>" % (" ok" if ok else "", esc(msg)))
        h.append("<form method=post action=/save><h2>Wi-Fi</h2><label>Nettverk<select name=ssid>")
        if known:
            h.append("<option value=''>Behold nåværende nettverk</option>")
        for ssid, rssi, auth in self.nets:
            h.append("<option value=\"%s\">%s%s</option>" % (esc(ssid), esc(ssid), "" if auth else " (åpent)"))
        h.append("</select></label><label>… eller skriv navnet selv<input name=other autocapitalize=none autocorrect=off></label>")
        h.append("<label>Wi-Fi-passord<input name=wpw type=password autocomplete=off></label>")
        h.append("<p class=s><a href=/rescan>Søk etter nettverk på nytt</a></p>")
        h.append("<h2>Konto</h2><label>Brukernavn<input name=user value=\"%s\" autocapitalize=none autocorrect=off></label>" % esc(user))
        h.append("<label>Passord<input name=upw type=password autocomplete=off placeholder='%s'></label>" % ("(uendret)" if store.account(cfg)[1] else ""))
        h.append("<button>Lagre og koble til</button></form>")
        h.append("<p class=s>Skjermen bruker bare Wi-Fi på 2,4 GHz.</p>")
        if known:
            h.append("<p class=s>Jeg husker: %s</p><form method=post action=/forget><button class=g>Glem alle nettverk</button></form>"
                     % esc(", ".join(known)))
        h.append("</div>")
        return "".join(h)

    def done_page(self):
        return ("<!doctype html><html lang=nb><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                "<title>Aurora</title><style>%s</style><div class=c><h1>Lagret</h1><p>Skjermen starter på nytt og kobler til. "
                "Du kan lukke denne siden.</p><p class=s>Går noe galt, åpner oppsettet seg igjen med en melding på skjermen.</p></div>" % CSS)

    def handle(self, method, path, body=""):
        """Returns the HTML to send back. Sets self.saved when new settings were stored."""
        path = path.split("?")[0]
        if method == "POST" and path == "/forget":
            self.cfg["wifis"] = []
            self.cfg["no_secrets"] = True
            store.save(self.cfg)
            return self.page("Alle nettverk er glemt. Velg et nytt under.", True)
        if method == "GET" and path == "/rescan":
            self.nets = scan_list(network.WLAN(network.STA_IF))
            return self.page("Fant %d nettverk." % len(self.nets), True)
        if method == "POST" and path == "/save":
            return self.save(parse_form(body))
        return self.page()

    def save(self, f):
        cfg = self.cfg
        ssid = f.get("other", "").strip() or f.get("ssid", "")
        wpw = f.get("wpw", "")
        user = f.get("user", "").strip()
        upw = f.get("upw", "")
        cur_user, cur_pw = store.account(cfg)
        secured = any(s == ssid and a for s, _, a in self.nets) or (ssid and ssid not in [s for s, _, _ in self.nets] and wpw)
        if not ssid and not store.wifis(cfg):
            return self.page("Velg et Wi-Fi-nettverk.")
        if ssid and secured and not wpw:
            return self.page("Skriv inn Wi-Fi-passordet.")
        if ssid and wpw and len(wpw) < 8:
            return self.page("Wi-Fi-passordet må ha minst 8 tegn.")
        if not user:
            return self.page("Skriv inn brukernavnet.")
        if any(c in user for c in "/#+ "):
            return self.page("Brukernavnet kan ikke ha mellomrom, / # eller +.")
        if not upw and user == cur_user and cur_pw:
            upw = cur_pw
        if not upw:
            return self.page("Skriv inn passordet til kontoen.")
        account_changed = (user, upw) != (cur_user, cur_pw)
        if not ssid and not account_changed:
            return self.page("Ingenting er endret.", True)
        if ssid:
            store.add_wifi(cfg, ssid, wpw)
        cfg["user"], cfg["pass"] = user, upw
        cfg["pending"] = {"ssid": ssid or None, "account": account_changed}
        store.save(cfg)
        self.saved = True
        return self.done_page()


# ---- the server -------------------------------------------------------------
def _read_request(cl):
    data = b""
    while b"\r\n\r\n" not in data and len(data) < 4096:
        chunk = cl.recv(512)
        if not chunk:
            break
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("utf-8", "ignore").split("\r\n")
    parts = lines[0].split(" ")
    clen = 0
    for ln in lines[1:]:
        if ln.lower().startswith("content-length:"):
            clen = min(int(ln.split(":")[1]), 2048)
    while len(body) < clen:
        chunk = cl.recv(512)
        if not chunk:
            break
        body += chunk
    return (parts[0], parts[1] if len(parts) > 1 else "/", body.decode("utf-8", "ignore"))


def _send(cl, html):
    data = html.encode()
    head = ("HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nCache-Control: no-store\r\n"
            "Connection: close\r\nContent-Length: %d\r\n\r\n" % len(data)).encode()
    buf = memoryview(head + data)
    while len(buf):
        n = cl.send(buf)
        buf = buf[n:]


def _password():
    return "%08d" % (int.from_bytes(os.urandom(4), "big") % 100000000)


def portal(epd, wlan, cfg, reason, idle_s=IDLE_S):
    """Shows the instructions on the e-paper, opens the setup network and serves the page.
    Returns True when new settings were saved (the caller restarts the board), False after idle_s with no visit."""
    import screen
    pw = _password()
    state = Portal(cfg, scan_list(wlan))
    bw, red = screen.setup_screen(reason, AP_NAME, pw, AP_IP)
    epd.update(bw, red)
    del bw, red
    gc.collect()
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=AP_NAME, password=pw, authmode=network.AUTH_WPA2_PSK)
    ap.ifconfig((AP_IP, "255.255.255.0", AP_IP, AP_IP))
    web = socket.socket()
    web.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    web.bind(("0.0.0.0", 80))
    web.listen(4)
    dns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dns.bind(("0.0.0.0", 53))
    poller = select.poll()
    poller.register(web, select.POLLIN)
    poller.register(dns, select.POLLIN)
    print("setup: join", AP_NAME, "password", pw, "then open", AP_IP)
    last = time.ticks_ms()
    try:
        while not state.saved:
            if time.ticks_diff(time.ticks_ms(), last) > idle_s * 1000:
                return False
            for obj, _ in poller.poll(250):
                last = time.ticks_ms()
                if obj is dns:
                    try:
                        data, addr = dns.recvfrom(512)
                        reply = dns_reply(data, AP_IP)
                        if reply:
                            dns.sendto(reply, addr)
                    except Exception as e:
                        print("dns:", e)
                else:
                    cl, _a = web.accept()
                    try:
                        cl.settimeout(3)
                        method, path, body = _read_request(cl)
                        _send(cl, state.handle(method, path, body))
                    except Exception as e:
                        print("http:", e)
                    finally:
                        cl.close()
        time.sleep(2)            # let the last page reach the phone before the board restarts
        return True
    finally:
        for s in (web, dns):
            try:
                s.close()
            except Exception:
                pass
        ap.active(False)
