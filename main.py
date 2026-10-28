import gc, time, struct, json, network, ssl, machine
from umqtt.simple import MQTTClient
from picographics import PicoGraphics, DISPLAY_PICO_DISPLAY_2, PEN_RGB332
from pimoroni import Button
import secrets

W, H = 320, 240
T_CMD, T_EVT, T_STATUS = b"penpad/cmd", b"penpad/evt", b"penpad/status"

# 8-bit colour (RGB332) keeps the framebuffer at 76.8 KB so TLS has enough RAM
display = PicoGraphics(display=DISPLAY_PICO_DISPLAY_2, pen_type=PEN_RGB332)
display.set_backlight(0.8)
display.set_font("bitmap8")
fb = memoryview(display)  # raw framebuffer, 1 byte per pixel

buttons = {"A": Button(12), "B": Button(13), "X": Button(14), "Y": Button(15)}
prev = {k: False for k in buttons}
dirty = False
pong = False
booted = False   # once True, status text never overwrites the drawing
wlan = network.WLAN(network.STA_IF)


def say(msg):
    print(msg)
    if booted:
        return
    display.set_pen(0)
    display.clear()
    display.set_pen(255)
    display.text(msg, 8, 8, W - 16, 2)
    display.update()


def wifi():
    wlan.active(True)
    if not wlan.isconnected():
        say("WiFi: connecting...")
        wlan.connect(secrets.WIFI_SSID, secrets.WIFI_PASSWORD)
        for _ in range(30):
            if wlan.isconnected():
                break
            time.sleep(1)
    if not wlan.isconnected():
        wlan.disconnect()
        wlan.active(False)  # reset the radio so the next attempt starts clean
        time.sleep(1)
        raise OSError("wifi failed")


def line(color, size, x0, y0, x1, y1):
    display.set_pen(color)
    try:
        display.line(x0, y0, x1, y1, size)
    except TypeError:  # older firmware without thickness argument
        display.line(x0, y0, x1, y1)


def on_msg(topic, msg):
    """Binary protocol. byte0 = type
    1 clear : [1, colour]
    2 lines : [2, colour, size] + N * (x0,y0,x1,y1 as uint16 LE)
    3 rows  : [3, firstRow, nRows] + nRows*320 bytes of RGB332
    """
    global dirty, pong
    if topic.startswith(b"penpad/frame/"):
        # stored frame: 16 rows (5120 bytes) per retained message
        n = int(topic[13:].decode())
        if 0 <= n < H // 16 and len(msg) == 16 * W:
            fb[n * 16 * W:(n + 1) * 16 * W] = msg
            dirty = True
        return
    t = msg[0]
    if t == 5:  # "are you there?" from the web page
        pong = True
        return
    if t == 1:
        display.set_pen(msg[1])
        display.clear()
    elif t == 2:
        color, size = msg[1], msg[2]
        for i in range(3, len(msg) - 7, 8):
            x0, y0, x1, y1 = struct.unpack_from("<HHHH", msg, i)
            line(color, size, x0, y0, x1, y1)
    elif t == 3:
        r0, n = msg[1], msg[2]
        if r0 + n <= H and len(msg) >= 3 + n * W:
            fb[r0 * W:(r0 + n) * W] = memoryview(msg)[3:3 + n * W]
    dirty = True


def close_quietly(c):
    try:
        c.sock.close()
    except Exception:
        pass


def connect_mqtt():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.verify_mode = ssl.CERT_NONE  # test setup; pin the certificate later
    c = MQTTClient("pico-penpad", secrets.MQTT_HOST, port=secrets.MQTT_PORT,
                   user=secrets.MQTT_USER, password=secrets.MQTT_PASS,
                   keepalive=30, ssl=ctx)
    c.set_last_will(T_STATUS, b"offline", retain=True)
    c.set_callback(on_msg)
    try:
        c.connect()
        c.subscribe(T_CMD)
        c.subscribe(b"penpad/frame/#")  # broker replays the saved drawing now
        c.publish(T_STATUS, b"online", retain=True)
    except Exception:
        close_quietly(c)
        raise
    return c


def run():
    global dirty, pong, booted, fails
    gc.collect()
    wifi()
    say("MQTT: connecting...")
    client = connect_mqtt()
    say("Ready!\nOpen the web page\nand draw.")
    booted = True
    fails = 0  # connected fine: start counting failures from zero
    last_ping = last_draw = time.ticks_ms()
    try:
        while True:
            if not wlan.isconnected():
                raise OSError("wifi lost")
            client.check_msg()
            if pong:
                pong = False
                client.publish(b"penpad/pong", b"1")
            for k, b in buttons.items():
                s = b.read()
                if s != prev[k]:
                    prev[k] = s
                    client.publish(T_EVT, json.dumps({"btn": k, "down": s}))
            now = time.ticks_ms()
            if dirty and time.ticks_diff(now, last_draw) > 40:
                display.update()
                dirty = False
                last_draw = now
            if time.ticks_diff(now, last_ping) > 15000:
                client.ping()
                last_ping = now
                gc.collect()
            time.sleep_ms(3)
    finally:
        close_quietly(client)  # never leak sockets across reconnects


MAX_FAILS = 30  # about 20 minutes of failed attempts -> reboot as a safety net
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
