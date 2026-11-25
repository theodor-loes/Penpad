import gc, time, json, binascii, network, ssl, machine
from machine import Pin
from umqtt.simple import MQTTClient, MQTTException
from epd import EPD
import datestamp, store, portal
import secrets

W, H = 400, 300
ROWS = 30                    # rows per MQTT chunk
CH = ROWS * (W // 8)         # 1500 bytes per chunk
NCH = H // ROWS              # 10 chunks per plane

PIN_SETUP_BTN = 0            # the BOOT button on the board: hold it for 3 seconds to open the setup page
SETUP_HOLD_MS = 3000
SETUP_NOT_SEEN_MIN = 5       # no remembered Wi-Fi in range for this long -> open the setup page
SETUP_FAILING_MIN = 30       # a remembered Wi-Fi is in range, but no connection for this long -> open the setup page
SETUP_RETRY_MIN = 60         # after the setup page timed out, wait this long before it opens again by itself


class SetupNeeded(Exception):
    """Raised when the setup page has to open. The text (e.args[0]) says why: new, nowifi, wifipw, auth or button."""


# The account (one topic namespace per account) comes from config.json or secrets.py, see apply_account()
USER = PASS = ""
P = T_CMD = T_STATUS = T_FRAME = T_SETTINGS = b""
cfg = {}

# Optional lines in secrets.py (these defaults are Norway):
UTC_OFFSET = getattr(secrets, "UTC_OFFSET_HOURS", 1)    # hours from UTC in winter
EU_DST = getattr(secrets, "EU_DST", True)               # True: add one hour in summer (EU rules)
VERIFY_TLS = getattr(secrets, "VERIFY_TLS", True)       # False: skip the broker certificate check

bw = bytearray(b"\xff" * (CH * NCH))   # 1 = white
red = bytearray(CH * NCH)              # 1 = red
planes = {b"w": bw, b"r": red}
dirty = False        # screen content differs from what is shown
first = True         # after boot, redraw once even if the data looks unchanged
force = False        # "send now" from the page: redraw even if nothing changed
last_frame_ms = 0
last_ping = 0
client = None
wlan = network.WLAN(network.STA_IF)

date_on = False      # the page's calendar switch
time_ok = False      # True once the clock has been set from the internet
date_txt = None      # date text currently stamped on the picture (None = no date)
last_ntp = 0
last_clock = 0
_ctx = None
offline_since = None          # when the connection was first lost (None = online)
seen_ms = time.ticks_ms()     # when a remembered Wi-Fi was last in range
auth_fails = 0                # connection refused for wrong username/password, in a row
no_setup_until = 0            # the setup page does not open by itself before this time (it was just closed unused)
btn = Pin(PIN_SETUP_BTN, Pin.IN, Pin.PULL_UP)
btn_since = None


def keepalive():
    """Called while the e-paper refreshes (up to 25 s) so the broker does not drop us."""
    global last_ping
    now = time.ticks_ms()
    if client and time.ticks_diff(now, last_ping) > 20000:
        try:
            client.ping()
        except Exception:
            pass            # a dead connection is dealt with by the main loop, never in the middle of a screen refresh
        last_ping = now


epd = EPD(hook=keepalive)


def button_held():
    global btn_since
    if btn.value() == 0:
        if btn_since is None:
            btn_since = time.ticks_ms()
        return time.ticks_diff(time.ticks_ms(), btn_since) > SETUP_HOLD_MS
    btn_since = None
    return False


def check_button():
    if button_held():
        raise SetupNeeded("button")


def setup_allowed():
    return time.ticks_diff(time.ticks_ms(), no_setup_until) >= 0


def apply_account():
    global USER, PASS, P, T_CMD, T_STATUS, T_FRAME, T_SETTINGS
    USER, PASS = store.account(cfg)
    P = b"penpad/" + USER.encode() + b"/"
    T_CMD, T_STATUS, T_FRAME, T_SETTINGS = P + b"cmd", P + b"status", P + b"frame/", P + b"settings"


def wifi():
    """Joins the strongest remembered Wi-Fi in range. Opens the setup page when it makes sense."""
    global cfg, offline_since, seen_ms
    cfg = store.load()
    cands = store.wifis(cfg)
    if not store.account(cfg)[0] or not cands:
        raise SetupNeeded("new")
    if wlan.isconnected():
        return
    pending = (cfg.get("pending") or {}).get("ssid")
    if pending:      # the setup page just added this network: it has to work, or it is removed again
        ssid, _ = portal.connect_known(wlan, cands, prefer=pending, only=True, seconds=45, tick=check_button)
        if not ssid:
            store.remove_wifi(cfg, pending)
            cfg["pending"] = None
            store.save(cfg)
            raise SetupNeeded("wifipw")
        cfg["pending"] = {"ssid": None, "account": True} if cfg["pending"].get("account") else None
        store.save(cfg)
    else:
        print("WiFi: looking for known networks...")
        ssid, seen = portal.connect_known(wlan, cands, tick=check_button)
        now = time.ticks_ms()
        if seen:
            seen_ms = now
        if not ssid:
            if offline_since is None:
                offline_since = now
            if setup_allowed() and (time.ticks_diff(now, seen_ms) > SETUP_NOT_SEEN_MIN * 60000
                                    or time.ticks_diff(now, offline_since) > SETUP_FAILING_MIN * 60000):
                raise SetupNeeded("nowifi")
            wlan.active(False)    # reset the radio so the next attempt starts clean
            time.sleep(1)
            raise OSError("wifi failed")
        store.promote(cfg, ssid)
    offline_since = None
    seen_ms = time.ticks_ms()
    print("WiFi: connected to", ssid)


# ---- clock and date stamp -------------------------------------------------
def sync_time():
    global time_ok, last_ntp
    last_ntp = time.ticks_ms()
    try:
        import ntptime
        ntptime.settime()          # sets the clock to UTC
        time_ok = True
        print("clock set from the internet")
    except Exception as e:
        print("could not get the time:", e)


def refresh_date():
    """Works out what the date stamp should say right now; asks for a redraw when it changed."""
    global date_txt, dirty, last_frame_ms
    txt = datestamp.date_text(time.time(), UTC_OFFSET, EU_DST) if (date_on and time_ok) else None
    if txt != date_txt:
        date_txt = txt
        dirty = True
        last_frame_ms = time.ticks_ms()


# ---- messages from the page -----------------------------------------------
def on_msg(topic, msg):
    global dirty, last_frame_ms, force, date_on
    if topic.startswith(T_FRAME):
        kind, _, num = topic[len(T_FRAME):].partition(b"/")
        n = int(num.decode())
        buf = planes.get(kind)
        if buf is None or not 0 <= n < NCH or len(msg) != CH:
            return
        a = n * CH
        if buf[a:a + CH] != msg:
            buf[a:a + CH] = msg
            dirty = True
        if first:
            dirty = True
        last_frame_ms = time.ticks_ms()
    elif topic == T_SETTINGS:
        try:
            date_on = bool(json.loads(msg).get("date"))
        except Exception:
            return
        refresh_date()
    elif topic == T_CMD and msg[:1] == b"\x04":   # "refresh now" from the web page
        dirty = True
        force = True
        last_frame_ms = time.ticks_ms()


# ---- secure connection ----------------------------------------------------
def load_ca():
    """Reads ca.pem or ca.crt (PEM certificates, or one raw DER certificate) as a list of DER blobs."""
    data = None
    for path in ("ca.pem", "ca.crt"):
        try:
            data = open(path, "rb").read()
            break
        except OSError:
            pass
    if data is None:
        raise OSError("ca.pem / ca.crt is missing: upload the CA file from your EMQX overview page, or set VERIFY_TLS = False")
    if b"BEGIN CERTIFICATE" not in data:
        return [data]
    out = []
    for block in data.decode().split("-----BEGIN CERTIFICATE-----")[1:]:
        body = block.split("-----END CERTIFICATE-----")[0]
        out.append(binascii.a2b_base64("".join(body.split())))
    return out


def tls_context():
    global _ctx
    if _ctx is None:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if VERIFY_TLS:
            for der in load_ca():
                ctx.load_verify_locations(cadata=der)
            ctx.verify_mode = ssl.CERT_REQUIRED
        else:
            ctx.verify_mode = ssl.CERT_NONE
        _ctx = ctx
    return _ctx


def close_quietly(c):
    try:
        c.sock.close()
    except Exception:
        pass


def connect_mqtt():
    global auth_fails
    c = MQTTClient("dev-" + USER, secrets.MQTT_HOST, port=secrets.MQTT_PORT,
                   user=USER, password=PASS,
                   keepalive=60, ssl=tls_context())   # umqtt also checks that the certificate matches MQTT_HOST
    c.set_last_will(T_STATUS, b"offline", retain=True)
    c.set_callback(on_msg)
    try:
        c.connect()
        c.subscribe(T_CMD)
        c.subscribe(T_SETTINGS)
        c.subscribe(T_FRAME + b"#")   # broker replays the saved drawing and settings now
        c.publish(T_STATUS, b"online", retain=True)
    except MQTTException as e:
        close_quietly(c)
        if e.args and e.args[0] in (4, 5):      # refused: wrong username or password
            auth_fails += 1
            if cfg.get("pending") or (auth_fails >= 5 and setup_allowed()):
                auth_fails = 0
                cfg["pending"] = None
                store.save(cfg)
                raise SetupNeeded("auth")
        raise
    except Exception:
        close_quietly(c)
        raise
    auth_fails = 0
    if cfg.get("pending"):                      # new settings from the setup page are proven to work
        cfg["pending"] = None
        store.save(cfg)
    return c


def run():
    global client, last_ping, last_clock, dirty, first, fails, force
    gc.collect()
    wifi()
    apply_account()
    if not time_ok:
        sync_time()   # best effort; the date stamp stays hidden until the clock is known
    print("MQTT: connecting...")
    client = connect_mqtt()
    print("ready")
    fails = 0
    last_ping = last_clock = time.ticks_ms()
    try:
        while True:
            if not wlan.isconnected():
                raise OSError("wifi lost")
            check_button()
            client.check_msg()
            now = time.ticks_ms()
            if time.ticks_diff(now, last_clock) > 30000:      # twice a minute: clock upkeep and date rollover
                last_clock = now
                wait = 43200000 if time_ok else 300000        # re-sync every 12 h, or retry every 5 min
                if time.ticks_diff(now, last_ntp) > wait:
                    sync_time()
                refresh_date()
            if dirty and time.ticks_diff(now, last_frame_ms) > 2500:   # wait until the chunks stopped arriving
                b, r = bw, red
                if date_txt:
                    b, r = bytearray(bw), bytearray(red)
                    datestamp.stamp(b, r, date_txt)
                mode = epd.update(b, r, force)
                print("e-paper:", mode or "unchanged")
                force = False
                dirty = False
                first = False
                last_ping = time.ticks_ms()
            if time.ticks_diff(now, last_ping) > 20000:
                client.ping()
                last_ping = now
                gc.collect()
            time.sleep_ms(20)
    finally:
        close_quietly(client)
        client = None       # the screen refresh must not ping a closed connection


def open_setup(reason):
    """Shows the instructions and runs the setup page. Restarts the board when new settings were saved."""
    global first, fails, offline_since, seen_ms, no_setup_until, btn_since
    btn_since = None      # the button has to be pressed (and held) again to count
    gc.collect()
    print("setup page:", reason)
    try:
        saved = portal.portal(epd, wlan, store.load(), reason)
    except Exception as e:
        print("setup error:", e)
        fails += 1
        time.sleep(5)
        return
    if saved:
        print("saved, restarting")
        machine.reset()
    first = True          # redraw the saved drawing once the connection is back
    offline_since = None
    seen_ms = time.ticks_ms()
    no_setup_until = time.ticks_add(time.ticks_ms(), SETUP_RETRY_MIN * 60000)   # do not reopen straight away


MAX_FAILS = 30   # about 20 minutes of failed attempts -> reboot as a safety net
fails = 0
while True:
    try:
        run()
    except SetupNeeded as e:
        open_setup(e.args[0])
    except Exception as e:
        fails += 1
        print("error:", e, "(attempt %d)" % fails)
        if fails >= MAX_FAILS:
            machine.reset()
        time.sleep(5)
