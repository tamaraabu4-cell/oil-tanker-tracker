import asyncio
import glob
import json
import os
import time
from datetime import datetime, timezone

import websockets
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.environ["AISSTREAM_API_KEY"]

URL = "wss://stream.aisstream.io/v0/stream"
AREA_NAME = "English Channel & southern North Sea"
AREA_BOXES = [
    [[49.0, -6.0], [51.5, 2.0]],    # English Channel
    [[51.0, 1.5], [53.5, 5.0]],     # Dover Strait up to Rotterdam & Amsterdam
]
LISTEN_SECONDS = 600
OUTPUT_FILE = "tankers.json"

# --- The rules of the sieve (kept at the top so they're easy to tweak) ---
MIN_LENGTH_M = 200                 # smaller than this = not a crude tanker
GAS_WORDS = ("GAS", "LNG", "LPG")  # names that suggest a gas carrier
FULL_AT = 80                       # 80%+ of full depth = full
EMPTY_BELOW = 60                   # under 60% = empty


def size_class(length_m):
    """Guess the size class from length, and how deep that size sits when full."""
    if length_m >= 300:
        return "VLCC", 21.0
    if length_m >= 260:
        return "Suezmax", 16.5
    return "Aframax", 14.5


# Ports crews type as names instead of codes
CITY_CODES = {
    "ROTTERDAM": "NLRTM", "AMSTERDAM": "NLAMS", "ANTWERP": "BEANR",
    "LE HAVRE": "FRLEH", "FAWLEY": "GBFAW", "PORT SAID": "EGPSD",
}
# Placeholders that mean "not filled in"
JUNK = {"", "TBA", "TBN", "NA", "N/A", "NONE", "UNKNOWN"}


def clean_destination(text):
    """Tidy crew-typed destinations so the same port is always spelled one way."""
    text = text.strip().upper()
    if ">" in text:                              # 'from>to': keep the 'to'
        text = text.split(">")[-1].strip()
    if "FOR ORDER" in text:                      # 'EU FOR ORDERS', 'FOR ORDER'...
        return "FOR ORDERS"
    if text in CITY_CODES:                       # 'ROTTERDAM' -> 'NLRTM'
        return CITY_CODES[text]
    if text in JUNK or not any(ch.isalpha() for ch in text):
        return "UNKNOWN"                         # 'TBA', '?? ???'
    squashed = text.replace(" ", "")
    if len(squashed) >= 5 and squashed[:5].isalpha() and (len(squashed) == 5 or squashed[5] in "-_/"):
        return squashed[:5]                      # 'FR LEH' -> 'FRLEH', 'NLRTM-BOTLEK' -> 'NLRTM'
    return text                                  # anything else stays as typed, e.g. 'GUYANA'


def judge(draught_m, full_draught_m):
    """Return (% full, status). A draught of 0 means 'not entered', so we can't judge."""
    if not draught_m:
        return None, "unsure"
    pct = min(round(draught_m / full_draught_m * 100), 100)  # our "full" depths are averages, so cap at 100
    if pct >= FULL_AT:
        return pct, "full"
    if pct < EMPTY_BELOW:
        return pct, "empty"
    return pct, "unsure"


def handle_message(msg, positions, id_cards):
    """Write each message into the right notebook, filed under the ship's ID number."""
    kind = msg.get("MessageType")
    meta = msg.get("MetaData", {})
    mmsi = meta.get("MMSI")
    if mmsi is None:
        return

    if kind == "PositionReport":             # "I'm here"
        report = msg["Message"]["PositionReport"]
        positions[mmsi] = {
            "lat": report["Latitude"],
            "lon": report["Longitude"],
            "speed_kn": report["Sog"],       # Sog = speed over ground, in knots
            "last_seen_utc": meta.get("time_utc", ""),
        }

    elif kind == "ShipStaticData":           # the "ID card"
        card = msg["Message"]["ShipStaticData"]
        dim = card["Dimension"]
        id_cards[mmsi] = {
            "name": card["Name"].strip(),
            "type": card["Type"],
            "length_m": dim["A"] + dim["B"],
            "draught_m": card["MaximumStaticDraught"],
            "destination": clean_destination(card["Destination"]),
        }
    # any other kind of message is simply ignored


def build_snapshot(positions, id_cards):
    """Run every ship through the sieve and build what goes in tankers.json."""
    ships = []
    for mmsi, card in id_cards.items():
        if not 80 <= card["type"] <= 89:
            continue                          # not a tanker
        if card["length_m"] < MIN_LENGTH_M:
            continue                          # too small for crude
        if any(word in card["name"].upper() for word in GAS_WORDS):
            continue                          # probably a gas carrier
        if mmsi not in positions:
            continue                          # we don't know where it is

        size, full_draught = size_class(card["length_m"])
        pct, status = judge(card["draught_m"], full_draught)

        ship = {
            "mmsi": mmsi,
            "name": card["name"],
            "size_class": size,
            "length_m": card["length_m"],
            "draught_m": card["draught_m"],
            "full_draught_m": full_draught,
            "pct_full": pct,
            "status": status,
            "destination": card["destination"],
        }
        ship.update(positions[mmsi])          # add lat, lon, speed, last seen
        ships.append(ship)

    counts = {s: sum(1 for x in ships if x["status"] == s)
              for s in ("full", "empty", "unsure")}

    return {
        "updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "area": AREA_NAME,
        "boxes": AREA_BOXES,
        "counts": counts,
        "ships": ships,
    }

SUMMARY_FILE = "summary.json"


def write_summary():
    """One line per snapshot in history/, for the chart on the project page."""
    rows = []
    for path in sorted(glob.glob("history/*.json")):
        with open(path) as f:
            snap = json.load(f)
        rows.append({
            "time_utc": snap["updated_utc"],
            "area": snap.get("area", "English Channel"),
            "full": snap["counts"]["full"],
            "empty": snap["counts"]["empty"],
            "unsure": snap["counts"]["unsure"],
        })
    with open(SUMMARY_FILE, "w") as f:
        json.dump(rows, f, indent=1)

async def listen_once(positions, id_cards, stop_at):
    """One phone call to aisstream. Returns when time is up; raises if the line drops."""
    async with websockets.connect(URL) as ws:
        subscription = {
            "APIKey": API_KEY,
            "BoundingBoxes": AREA_BOXES,
            "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
        }
        await ws.send(json.dumps(subscription))

        while time.monotonic() < stop_at:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
            except asyncio.TimeoutError:
                continue                          # quiet patch, keep waiting

            msg = json.loads(raw)
            if "error" in msg:                    # e.g. a bad API key: give up properly
                raise SystemExit(f"aisstream said: {msg['error']}")

            try:
                handle_message(msg, positions, id_cards)
            except (KeyError, TypeError, AttributeError):
                continue                          # a damaged message: skip it


async def main():
    positions = {}
    id_cards = {}
    stop_at = time.monotonic() + LISTEN_SECONDS
    print(f"Listening to the {AREA_NAME} for {LISTEN_SECONDS // 60} minutes...")

    # Keep redialling until the time is up; the two notebooks survive each hang-up
    while time.monotonic() < stop_at:
        try:
            await listen_once(positions, id_cards, stop_at)
        except (websockets.exceptions.WebSocketException, OSError) as problem:
            print(f"Line dropped ({problem}). Reconnecting in 5 seconds...")
            await asyncio.sleep(5)

    if not id_cards:
        raise SystemExit("Heard nothing at all, so not saving. Is aisstream down?")

    snapshot = build_snapshot(positions, id_cards)

    with open(OUTPUT_FILE, "w") as f:
        json.dump(snapshot, f, indent=2)

    # Also keep a dated copy, e.g. history/2026-11-12T09-17.json
    os.makedirs("history", exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M")
    with open(f"history/{stamp}.json", "w") as f:
        json.dump(snapshot, f, indent=2)

    write_summary()

    print(f"\nHeard {len(id_cards)} ships' ID cards and {len(positions)} positions.")
    print(f"Crude-size tankers with a position: {len(snapshot['ships'])}")
    print(f"Full: {snapshot['counts']['full']}   Empty: {snapshot['counts']['empty']}   "
          f"Unsure: {snapshot['counts']['unsure']}\n")
    for s in snapshot["ships"]:
        pct = f"{s['pct_full']}%" if s["pct_full"] is not None else "?"
        print(f"{s['name']:<24} {s['size_class']:<8} {pct:>5}  {s['status']:<7} -> {s['destination']}")
    print(f"\nSaved to {OUTPUT_FILE}")

if __name__ == "__main__":
    asyncio.run(main())