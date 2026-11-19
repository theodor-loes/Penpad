import gc, time, network, ssl, machine
from umqtt.simple import MQTTClient
from epd import EPD
import secrets

W, H = 400, 300
ROWS = 30                    # rows per MQTT chunk
CH = ROWS * (W // 8)         # 1500 bytes per chunk
NCH = H // ROWS              # 10 chunks per plane

P = b"penpad/" + secrets.MQTT_USER.encode() + b"/"   # one topic namespace per account
T_CMD, T_STATUS, T_FRAME = P + b"cmd", P + b"status", P + b"frame/"

bw = bytearray(b"\xff" * (CH * NCH))   # 1 = white
red = bytearray(CH * NCH)              # 1 = red
planes = {b"w": bw, b"r": red}
dirty = False        # screen content differs from what is shown
first = True         # after boot, redraw once even if the data looks unchanged
last_frame_ms = 0
last_ping = 0
client = None
wlan = network.WLAN(network.STA_IF)


def keepalive():
    """Called while the e-paper refreshes (10 to 25 s) so the broker does not drop us."""
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


def on_msg(topic, msg):
    global dirty, last_frame_ms
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
    elif topic == T_CMD and msg[:1] == b"\x04":   # "refresh now" from the web page
        dirty = True
        last_frame_ms = time.ticks_ms()


def close_quietly(c):
    try:
        c.sock.close()
    except Exception:
        pass


def connect_mqtt():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.verify_mode = ssl.CERT_NONE  # test setup; pin the certificate later
    c = MQTTClient("dev-" + secrets.MQTT_USER, secrets.MQTT_HOST, port=secrets.MQTT_PORT,
                   user=secrets.MQTT_USER, password=secrets.MQTT_PASS,
                   keepalive=60, ssl=ctx)
    c.set_last_will(T_STATUS, b"offline", retain=True)
    c.set_callback(on_msg)
    try:
        c.connect()
        c.subscribe(T_CMD)
        c.subscribe(T_FRAME + b"#")   # broker replays the saved drawing now
        c.publish(T_STATUS, b"online", retain=True)
    except Exception:
        close_quietly(c)
        raise
    return c


def run():
    global client, last_ping, dirty, first, fails
    gc.collect()
    wifi()
    print("MQTT: connecting...")
    client = connect_mqtt()
    print("ready")
    fails = 0
    last_ping = time.ticks_ms()
    try:
        while True:
            if not wlan.isconnected():
                raise OSError("wifi lost")
            client.check_msg()
            now = time.ticks_ms()
            if dirty and time.ticks_diff(now, last_frame_ms) > 2500:   # wait until the chunks stopped arriving
                print("refreshing e-paper...")
                epd.show(bw, red)
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
