import asyncio
import json
import os
import time
from datetime import datetime, timezone

import websockets
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.environ["AISSTREAM_API_KEY"]

URL = "wss://stream.aisstream.io/v0/stream"
AREA_NAME = "English Channel"
AREA_BOX = [[49.0, -6.0], [51.5, 2.0]]
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


def clean_destination(text):
    """Tidy crew-typed destinations: 'FR LEH' -> 'FRLEH', 'FRGLO>FRLEH' -> 'FRLEH'."""
    text = text.strip().upper()
    if ">" in text:                          # 'from>to' style: keep the 'to'
        text = text.split(">")[-1].strip()
    squashed = text.replace(" ", "")
    if len(squashed) == 5 and squashed.isalpha():   # looks like a port code
        return squashed
    return text


def judge(draught_m, full_draught_m):
    """Return (% full, status). A draught of 0 means 'not entered', so we can't judge."""
    if not draught_m:
        return None, "unsure"
    pct = round(draught_m / full_draught_m * 100)
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
        "counts": counts,
        "ships": ships,
    }


async def main():
    positions = {}     # notebook 1: where each ship is
    id_cards = {}      # notebook 2: what each ship is

    async with websockets.connect(URL) as ws:
        subscription = {
            "APIKey": API_KEY,
            "BoundingBoxes": [AREA_BOX],
            "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
        }
        await ws.send(json.dumps(subscription))
        print(f"Listening to the {AREA_NAME} for {LISTEN_SECONDS // 60} minutes...")

        stop_at = time.monotonic() + LISTEN_SECONDS
        while time.monotonic() < stop_at:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
            except asyncio.TimeoutError:
                continue

            msg = json.loads(raw)
            if "error" in msg:
                print("aisstream said:", msg["error"])
                return

            handle_message(msg, positions, id_cards)

    snapshot = build_snapshot(positions, id_cards)

    with open(OUTPUT_FILE, "w") as f:
        json.dump(snapshot, f, indent=2)

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