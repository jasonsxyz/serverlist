import asyncio
import os
import threading
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, request, send_from_directory
from mcstatus import JavaServer
from mcstatus.motd.components import WebColor

DOMAIN = ".mc.progsoc.org"
NAMES = [
    "Creeper", "Zombie", "Skeleton", "Spider", "Enderman", "Witch", "Slime", "Blaze",
    "Ghast", "Piglin", "Hoglin", "Drowned", "Phantom", "Pillager", "Ravager", "Vindicator",
    "Evoker", "Warden", "Wither", "Enderdragon", "Cow", "Pig", "Sheep", "Chicken",
    "Horse", "Wolf", "Cat", "Fox", "Axolotl", "Villager", "Golem", "Bee",
    "Panda", "Turtle", "Dolphin", "Squid", "Bat", "Llama", "Parrot", "Allay",
    "Cod",
]
BY_LOWER = {n.lower(): n for n in NAMES}
TIMEOUT = 4
CACHE_TTL = 5  # seconds (the website polls every 5s)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)

_cache = {}  # name -> (monotonic timestamp, iso checked time, entry)
_lock = threading.Lock()

MC_COLORS = {
    "0": "#000000", "1": "#0000aa", "2": "#00aa00", "3": "#00aaaa",
    "4": "#aa0000", "5": "#aa00aa", "6": "#ffaa00", "7": "#aaaaaa",
    "8": "#555555", "9": "#5555ff", "a": "#55ff55", "b": "#55ffff",
    "c": "#ff5555", "d": "#ff55ff", "e": "#ffff55", "f": "#ffffff",
}
MC_STYLES = {"l": "b", "o": "i", "n": "u", "m": "s", "k": "k"}  # bold, italic, underline, strike, obfuscated


def motd_lines(motd):
    """Turn a parsed MOTD into lines of styled segments:
    [[{"t": "text", "c": "#ffaa00", "b": true, ...}, ...], ...]"""
    lines, line = [], []
    color, styles = None, set()
    for tok in motd.parsed:
        if isinstance(tok, str):
            for i, part in enumerate(tok.split("\n")):
                if i:
                    lines.append(line)
                    line = []
                if part:
                    seg = {"t": part}
                    if color:
                        seg["c"] = color
                    for k in styles:
                        seg[k] = True
                    line.append(seg)
            continue
        if isinstance(tok, WebColor):
            color, styles = tok.hex, set()
            continue
        code = getattr(tok, "value", None)
        kind = type(tok).__name__
        if "Color" in kind and code in MC_COLORS:
            color, styles = MC_COLORS[code], set()  # a colour code resets styles, like the game
        elif code == "r":
            color, styles = None, set()
        elif code in MC_STYLES:
            styles.add(MC_STYLES[code])
    lines.append(line)
    while lines and not any(seg["t"].strip() for seg in lines[-1]):
        lines.pop()
    return lines


@app.after_request
def add_cors(resp):
    # lets the page be opened from a file or another origin and still call the API
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


async def check(name):
    host = name.lower() + DOMAIN
    entry = {"name": name, "host": host, "online": False}
    try:
        server = await JavaServer.async_lookup(host, timeout=TIMEOUT)
        status = await asyncio.wait_for(server.async_status(), TIMEOUT + 1)
    except Exception:
        return entry
    icon = (
        status.icon
        if isinstance(status.icon, str) and status.icon.startswith("data:image/")
        else None
    )
    try:
        motd = status.motd.to_plain()
        lines = motd_lines(status.motd)
    except Exception:
        motd, lines = None, None
    entry.update(
        online=True,
        players=status.players.online,
        max=status.players.max,
        version=status.version.name,
        motd=motd,
        motd_lines=lines,
        icon=icon,
    )
    return entry


async def check_many(names):
    return await asyncio.gather(*(check(n) for n in names))


def get_entries(names, force=False):
    """Return cached entries for names, refreshing any that are stale."""
    with _lock:
        now = time.monotonic()
        stale = [
            n for n in names
            if force or n not in _cache or now - _cache[n][0] > CACHE_TTL
        ]
        if stale:
            results = asyncio.run(check_many(stale))
            checked = datetime.now(timezone.utc).isoformat(timespec="seconds")
            now = time.monotonic()
            for entry in results:
                _cache[entry["name"]] = (now, checked, entry)
        return [(_cache[n][1], _cache[n][2]) for n in names]


def truthy(value):
    return str(value).lower() in ("1", "true", "yes", "on")


def strip_icon(entry):
    return {k: v for k, v in entry.items() if k != "icon"}


@app.get("/api/servers")
def list_servers():
    """List all servers. Query params: online=true, icons=false, refresh=true"""
    pairs = get_entries(NAMES, force=truthy(request.args.get("refresh", "")))
    servers = [e for _, e in pairs]
    if truthy(request.args.get("online", "")):
        servers = [e for e in servers if e["online"]]
    if request.args.get("icons", "true").lower() in ("0", "false", "no", "off"):
        servers = [strip_icon(e) for e in servers]
    checked = min(c for c, _ in pairs)
    return jsonify({"checked": checked, "servers": servers})


@app.get("/api/servers/<name>")
def get_server(name):
    """Status for a single server by name (case-insensitive)."""
    canonical = BY_LOWER.get(name.lower())
    if canonical is None:
        return jsonify({"error": f"unknown server '{name}'"}), 404
    [(checked, entry)] = get_entries(
        [canonical], force=truthy(request.args.get("refresh", ""))
    )
    return jsonify({"checked": checked, "server": entry})


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


# --- the website (index.html, assets/, fonts/) ---

@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/assets/<path:filename>")
def assets(filename):
    return send_from_directory(os.path.join(BASE_DIR, "assets"), filename)


@app.get("/fonts/<path:filename>")
def fonts(filename):
    return send_from_directory(os.path.join(BASE_DIR, "fonts"), filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)