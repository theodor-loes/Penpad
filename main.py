import gc, time, json, binascii, network, ssl, machine
from umqtt.simple import MQTTClient
from epd import EPD
import datestamp
import secrets

W, H = 400, 300
ROWS = 30                    # rows per MQTT chunk
CH = ROWS * (W // 8)         # 1500 bytes per chunk
NCH = H // ROWS              # 10 chunks per plane

P = b"penpad/" + secrets.MQTT_USER.encode() + b"/"   # one topic namespace per account
T_CMD, T_STATUS, T_FRAME, T_SETTINGS = P + b"cmd", P + b"status", P + b"frame/", P + b"settings"

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


def keepalive():
    """Called while the e-paper refreshes (up to 25 s) so the broker does not drop us."""
    global last_ping
    now = time.ticks_ms()
    if client and time.ticks_diff(now, last_ping) > 20000:
        client.ping()
        last_ping = now


epd = EPD(hook=keepalive)


def wifi():
    wlan.active(True)
    if not wlan.isconnected():
        print("WiFi: connecting...")
        wlan.connect(secrets.WIFI_SSID, secrets.WIFI_PASSWORD)
        for _ in range(30):
            if wlan.isconnected():
                break
            time.sleep(1)
    if not wlan.isconnected():
        wlan.disconnect()
        wlan.active(False)   # reset the radio so the next attempt starts clean
        time.sleep(1)
        raise OSError("wifi failed")


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
    c = MQTTClient("dev-" + secrets.MQTT_USER, secrets.MQTT_HOST, port=secrets.MQTT_PORT,
                   user=secrets.MQTT_USER, password=secrets.MQTT_PASS,
                   keepalive=60, ssl=tls_context())   # umqtt also checks that the certificate matches MQTT_HOST
    c.set_last_will(T_STATUS, b"offline", retain=True)
    c.set_callback(on_msg)
    try:
        c.connect()
        c.subscribe(T_CMD)
        c.subscribe(T_SETTINGS)
        c.subscribe(T_FRAME + b"#")   # broker replays the saved drawing and settings now
        c.publish(T_STATUS, b"online", retain=True)
    except Exception:
        close_quietly(c)
        raise
    return c


def run():
    global client, last_ping, last_clock, dirty, first, fails, force
    gc.collect()
    wifi()
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


MAX_FAILS = 30   # about 20 minutes of failed attempts -> reboot as a safety net
fails = 0
while True:
    try:
        run()
    except Exception as e:
        fails += 1
        print("error:", e, "(attempt %d)" % fails)
        if fails >= MAX_FAILS:
            machine.reset()
        time.sleep(5)
