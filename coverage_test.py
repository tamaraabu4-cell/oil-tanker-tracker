"""Coverage test: can the free feed see ships off Nigeria?
Listens to one box over the whole Nigerian coast for 10 minutes and reports what it heard.
Saves nothing to the repo's tankers.json or history."""
import asyncio
import json
import time

import websockets

from collect import API_KEY, URL, handle_message

NIGERIA = [[3.5, 2.7], [6.7, 8.7]]   # Lagos and Lekki (Dangote) across to Bonny, Qua Iboe and the deepwater fields
LISTEN_SECONDS = 600


async def main():
    positions, id_cards = {}, {}
    messages = 0
    stop_at = time.monotonic() + LISTEN_SECONDS
    print("Listening off Nigeria for 10 minutes...")

    while time.monotonic() < stop_at:
        try:
            async with websockets.connect(URL) as ws:
                await ws.send(json.dumps({
                    "APIKey": API_KEY,
                    "BoundingBoxes": [NIGERIA],
                    "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
                }))
                while time.monotonic() < stop_at:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        continue
                    messages += 1
                    try:
                        handle_message(json.loads(raw), positions, id_cards)
                    except (KeyError, TypeError, AttributeError):
                        continue
        except (websockets.exceptions.WebSocketException, OSError) as problem:
            print(f"Line dropped ({problem}). Reconnecting in 5 seconds...")
            await asyncio.sleep(5)

    tankers = {m: c for m, c in id_cards.items() if 80 <= c["type"] <= 89}
    big = {m: c for m, c in tankers.items() if c["length_m"] >= 200}

    print(f"\nMessages heard:        {messages}")
    print(f"Ships with a position: {len(positions)}")
    print(f"Ships with an ID card: {len(id_cards)}")
    print(f"  of which tankers:    {len(tankers)}")
    print(f"  of which 200 m+:     {len(big)}\n")
    for mmsi, c in big.items():
        where = positions.get(mmsi)
        spot = f"{where['lat']:.2f}, {where['lon']:.2f}" if where else "no position"
        print(f"{c['name']:<24} {c['length_m']:>4} m  draught {c['draught_m']:>5}  -> {c['destination']:<12} at {spot}")

    # Where were ships heard from? (furthest south = furthest offshore, roughly)
    if positions:
        lats = sorted(p["lat"] for p in positions.values())
        print(f"\nPositions heard from latitude {lats[0]:.2f} to {lats[-1]:.2f} (the coast is at about 4.3 to 6.4)")


if __name__ == "__main__":
    asyncio.run(main())