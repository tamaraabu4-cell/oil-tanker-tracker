import asyncio          # lets Python wait for messages without freezing
import json             # turns the feed's text into Python dictionaries
import os
import time

import websockets       # the "phone line" to aisstream
from dotenv import load_dotenv

load_dotenv()                                   # reads your .env file
API_KEY = os.environ["AISSTREAM_API_KEY"]

URL = "wss://stream.aisstream.io/v0/stream"

# The patch of sea to listen to: [south-west corner], [north-east corner] as [latitude, longitude]
ENGLISH_CHANNEL = [[49.0, -6.0], [51.5, 2.0]]

# Each ship only sends its "ID card" every ~6 minutes, so listen for 10
LISTEN_SECONDS = 600


def is_tanker(ship_type):
    # In AIS, ship types 80-89 all mean "tanker"
    return 80 <= ship_type <= 89


async def main():
    seen = {}   # ships already printed, keyed by their ID number (MMSI)

    async with websockets.connect(URL) as ws:
        # Tell aisstream what we want. This must be sent within 3 seconds of connecting
        subscription = {
            "APIKey": API_KEY,
            "BoundingBoxes": [ENGLISH_CHANNEL],
            "FilterMessageTypes": ["ShipStaticData"],   # only the "ID card" messages
        }
        await ws.send(json.dumps(subscription))
        print("Listening for tankers... (press Ctrl+C to stop early)\n")

        stop_at = time.monotonic() + LISTEN_SECONDS
        while time.monotonic() < stop_at:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
            except asyncio.TimeoutError:
                continue                     # quiet patch, keep waiting

            msg = json.loads(raw)

            if "error" in msg:               # e.g. a wrong API key
                print("aisstream said:", msg["error"])
                return

            if msg.get("MessageType") != "ShipStaticData":
                continue                     # not an ID card, skip it

            data = msg["Message"]["ShipStaticData"]
            if not is_tanker(data["Type"]):
                continue                     # not a tanker, ignore it

            mmsi = msg["MetaData"]["MMSI"]
            if mmsi in seen:
                continue                     # already printed this one

            dim = data["Dimension"]          # A = bow to antenna, B = antenna to stern
            ship = {
                "name": data["Name"].strip(),
                "length_m": dim["A"] + dim["B"],
                "draught_m": data["MaximumStaticDraught"],   # despite the name, this is how deep it sits NOW
                "destination": data["Destination"].strip(),
            }
            seen[mmsi] = ship

            print(f'{ship["name"]:<24} {ship["length_m"]:>4} m long   '
                  f'draught {ship["draught_m"]:>5} m   -> {ship["destination"]}')

    print(f"\nDone. Heard from {len(seen)} tankers.")


asyncio.run(main())