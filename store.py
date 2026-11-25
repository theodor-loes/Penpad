"""Saved settings in config.json: the Wi-Fi networks the display remembers, and the account it syncs to.
secrets.py still works as the first-time default: its Wi-Fi and account are used until config.json says otherwise."""
import json, os

FILE = "config.json"
MAX_WIFI = 10      # remembered networks; the one used longest ago is forgotten first


def _secret(name):
    try:
        import secrets
        return getattr(secrets, name, "")
    except ImportError:
        return ""


def _real(v):
    """Placeholder text from the secrets.py template does not count."""
    return bool(v) and not str(v).startswith("your-") and v != "a-long-unique-password"


def load():
    try:
        with open(FILE) as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError
    except Exception:
        cfg = {}
    cfg.setdefault("wifis", [])        # [[ssid, password], ...], most recently used first
    cfg.setdefault("user", "")
    cfg.setdefault("pass", "")
    cfg.setdefault("pending", None)    # set by the setup page until the new settings have been proven to work
    return cfg


def save(cfg):
    tmp = FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f)
    try:
        os.remove(FILE)
    except OSError:
        pass
    os.rename(tmp, FILE)


def wifis(cfg):
    """All networks to try, as (ssid, password): the saved ones, then the one in secrets.py if it is new."""
    out = [(w[0], w[1]) for w in cfg["wifis"]]
    ssid, pw = _secret("WIFI_SSID"), _secret("WIFI_PASSWORD")
    if _real(ssid) and not cfg.get("no_secrets") and ssid not in [s for s, _ in out]:
        out.append((ssid, pw))
    return out


def account(cfg):
    if cfg["user"]:
        return cfg["user"], cfg["pass"]
    user, pw = _secret("MQTT_USER"), _secret("MQTT_PASS")
    if _real(pw) and user:
        return user, pw
    return "", ""


def add_wifi(cfg, ssid, pw):
    remove_wifi(cfg, ssid)
    cfg["wifis"].insert(0, [ssid, pw])
    del cfg["wifis"][MAX_WIFI:]


def remove_wifi(cfg, ssid):
    cfg["wifis"] = [w for w in cfg["wifis"] if w[0] != ssid]


def promote(cfg, ssid):
    """Called after a successful connection: remember the network and put it first."""
    if cfg["wifis"] and cfg["wifis"][0][0] == ssid:
        return
    pw = dict(wifis(cfg)).get(ssid)
    if pw is None:
        return
    add_wifi(cfg, ssid, pw)
    save(cfg)
