# Oil Tanker Tracker

Which oil tankers are carrying oil right now, and which are sailing empty?

This project listens to the signals ships broadcast about themselves (AIS) and works out whether each tanker is **full (laden)**, **empty (in ballast)** or **unsure**, starting with the English Channel.

## Why it matters

A full tanker sits lower in the water than an empty one. Counting full tankers moving in and out of a region gives an early read on oil supply, weeks before official export figures are published. Commodity traders pay data firms for exactly this.

## How it works

1. **Listen:** connect to the free [aisstream.io](https://aisstream.io) feed and keep only tankers (AIS ship types 80–89).
2. **Filter:** keep crude-size ships (over 200 m) and skip gas carriers.
3. **Judge:** compare how deep each ship sits now with how deep a ship of its size sits when full:

   `% full = draught now ÷ draught when full`

   | Ship size | Rough length | Draught when full |
   |---|---|---|
   | VLCC | ~330 m | ~21 m |
   | Suezmax | ~275 m | ~16.5 m |
   | Aframax | ~245 m | ~14.5 m |

   80% or more = full · under 60% = empty · in between = unsure

4. **Show:** save a snapshot (`tankers.json`) and plot it on a map.

## Status

- [x] Connect to the live feed and list tankers in the English Channel (`listen.py`)
- [ ] Add live positions, size and gas filters, % full
- [ ] Save `tankers.json` snapshots on a timer (GitHub Actions)
- [ ] Map page
- [ ] Weekly count of full tankers vs the oil price

## Things I've learned from the data

- Most tankers in the Channel are small coastal, fuel and chemical tankers, not crude carriers.
- Gas (LNG) carriers are listed as "tankers" too, but their depth barely changes when loaded, so they need filtering out.
- Draught and destination are typed in by the crew, so the data is messy: a draught of 0 means "not entered", and one port can appear as `LE HAVRE`, `FRLEH` or `FR LEH`.

## Run it yourself

```
python -m venv .venv
source .venv/bin/activate
pip install websockets python-dotenv
```

Create a `.env` file with your free aisstream.io key:

```
AISSTREAM_API_KEY=your-key-here
```

Then:

```
python listen.py
```

It listens for 10 minutes (each ship sends its details roughly every 6 minutes) and prints every tanker it hears.

python -m http.server 8000   # then open http://localhost:8000/map.html

## Data

Ship data from [aisstream.io](https://aisstream.io) (free, in beta). Coverage comes mostly from coastal receivers, so ships far out at sea may be missing.