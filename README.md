# dump1090-replay

[![CI](https://github.com/technothatch/dump1090-replay/actions/workflows/ci.yml/badge.svg)](https://github.com/technothatch/dump1090-replay/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/technothatch/dump1090-replay/blob/main/LICENSE)
[![Latest Release](https://img.shields.io/github/v/release/technothatch/dump1090-replay)](https://github.com/technothatch/dump1090-replay/releases)
[![Issues](https://img.shields.io/github/issues/technothatch/dump1090-replay)](https://github.com/technothatch/dump1090-replay/issues)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/technothatch/dump1090-replay/pulls)
[![Code size](https://img.shields.io/github/languages/code-size/technothatch/dump1090-replay)](https://github.com/technothatch/dump1090-replay)
[![Last commit](https://img.shields.io/github/last-commit/technothatch/dump1090-replay)](https://github.com/technothatch/dump1090-replay/commits/main)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Coverage](https://img.shields.io/badge/coverage-99%25-brightgreen.svg)](https://github.com/technothatch/dump1090-replay)

Record the `aircraft.json` history published by
[dump1090](https://github.com/flightaware/dump1090) and
[readsb](https://github.com/wiedehopf/readsb), then replay it **faithfully**
whenever you want — no RTL-SDR, no receiver, no decoder. `dump1090-replay`
serves the same `/data/aircraft.json`, `/data/receiver.json` and
`/data/stats.json` documents a live decoder would, with `now`, `seen` and
`seen_pos` offsets kept coherent against a scaled virtual clock, so web UIs,
alert rules, exporters and dashboards developed against the replay work
unchanged against real traffic. One stdlib-only Python process, a shared
NDJSON format, and a bundled example you can play in ten seconds.

## Features

- **`record`** polls a live decoder and appends one NDJSON line per snapshot —
  `{"t": <epoch>, "aircraft": [...]}` — the exact convention shared with
  [dump1090-recorder](https://github.com/technothatch/dump1090-recorder) and
  [dump1090-influx](https://github.com/technothatch/dump1090-influx).
- **`play`** serves a faithful `/data/aircraft.json`: `now` is the replayed
  timeline, ages grow with it, and `now - seen` still points at the moment the
  message originally arrived. `alt_baro: "ground"`, squawks, `mlat`/`tisb`,
  rssi — everything passes through untouched.
- **`--speed N`** replay clock (10× = ten recorded seconds per wall second),
  **`--loop`** to run a capture forever, **`--duration`** for scripted runs.
- **`--realtime-file DIR`** rewrites the dump1090 `history_NNN.json` ring
  buffer plus `aircraft.json` for file-based consumers (tar1090-style
  `--write-json` directories).
- **Synthesised `receiver.json` / `stats.json`** in dump1090's real shape —
  the five `latest`/`last1min`/`last5min`/`last15min`/`total` buckets with the
  `local` subkeys (`blocks_processed`, `blocks_dropped`, `modes`, `bad`,
  `signal`, …) plus `cpu`, `cpr`, `tracks`, `messages` — since the shared
  NDJSON format only carries `t` and `aircraft`.
- **CORS enabled** on every response, so a browser UI on another port (or
  `file://`) can poll the replay server directly.
- **`info`** summarises a recording: duration, record count, unique ICAOs,
  callsigns.
- **Zero runtime dependencies** — `http.server`, `argparse`, `json`,
  `urllib.request` only. Fully typed (`py.typed`), 127 tests at 99% coverage.

## Install

```bash
pip install dump1090-replay
```

Or from source:

```bash
git clone https://github.com/technothatch/dump1090-replay.git
cd dump1090-replay
pip install -e ".[dev]"
```

Requires Python 3.11+.

## Quickstart

A 12-second capture ships with the repo, so nothing here needs a decoder.

**Look at what's in a recording:**

```console
$ dump1090-replay info examples/demo.ndjson
examples/demo.ndjson: 12 record(s) spanning 11.0s
  first 2026-10-03 12:00:00 UTC, last 2026-10-03 12:00:11 UTC
  3 aircraft, 3 callsign(s)
  hex: 3c673a, 40621d, 4ca918
```

**Replay it at 10× speed, looping, on port 8090:**

```console
$ dump1090-replay play examples/demo.ndjson --speed 10 --loop --http 8090
# examples/demo.ndjson - 12 record(s), speed 10x, loop, http://127.0.0.1:8090
```

In another terminal, poll it exactly like a decoder:

```console
$ curl -s http://127.0.0.1:8090/data/aircraft.json | python -m json.tool | head -14
{
    "now": 1791028811.41,
    "messages": 1611,
    "aircraft": [
        {
            "hex": "4ca918",
            "type": "B738",
            "flight": "RYR22GK ",
            "alt_baro": 34000,
            "alt_geom": 34200,
            "gs": 458,
            "ias": 441,
            "tas": 462,
            "mach": 0.84,
```

`now` advances on the recorded timeline (it is never wall time) and each
aircraft's `seen` grows with it, so `now - seen` still equals the original
arrival time — tools computing message ages and rates see a coherent clock.

**Record your own traffic** (this part needs a decoder):

```console
$ dump1090-replay record --url http://readsb.lan:8080 --interval 1 --out flight.ndjson
# recording http://readsb.lan:8080/data/aircraft.json every 1.0s -> flight.ndjson
^C
# interrupted
# wrote 1842 snapshot(s) to flight.ndjson
```

**Rewrite a file-based history directory** while replaying:

```bash
dump1090-replay play flight.ndjson --speed 4 --realtime-file /tmp/readsb-data
# /tmp/readsb-data/history_000.json ... history_599.json + aircraft.json
```

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success (Ctrl+C stops cleanly) |
| `1` | Bad input: missing/broken recording, unwritable output, port in use, recorder that captured nothing |
| `2` | Argument error (bad `--speed`, unknown flag, missing subcommand) |

## Configuration reference

### Command line / environment

| Flag | Environment | Meaning | Default |
| --- | --- | --- | --- |
| `--version` | — | Print version and exit | — |

### `dump1090-replay record`

| Flag | Meaning | Default |
| --- | --- | --- |
| `--url URL` | Decoder base URL or full `aircraft.json` URL (env `DUMP1090_URL`) | `http://localhost:8080` |
| `--interval SECS` | Seconds between polls | `1.0` |
| `--out PATH` | NDJSON file to write (truncated) | required |
| `--timeout SECS` | HTTP timeout per poll | `5.0` |
| `--max-polls N` | Stop after N polls (`0` = until interrupted) | `0` |

### `dump1090-replay play`

| Flag | Meaning | Default |
| --- | --- | --- |
| `FILE` | Recording produced by `record` (positional) | required |
| `--speed X` | Replay speed multiplier (`10` = 10× realtime) | `1.0` |
| `--loop` | Restart at the end instead of stopping | off |
| `--http PORT` | Port for `/data/*.json` (`0` disables HTTP) | `8090` |
| `--bind ADDR` | HTTP bind address (`0.0.0.0` to expose) | `127.0.0.1` |
| `--realtime-file DIR` | Rewrite `history_NNN.json` + `aircraft.json` into DIR | off |
| `--interval SECS` | Wall seconds between history writes / tick cadence | `1.0` |
| `--history-size N` | History ring buffer length | `600` |
| `--lat DEG` | Latitude reported in `receiver.json` | `0.0` |
| `--lon DEG` | Longitude reported in `receiver.json` | `0.0` |
| `--duration SECS` | Stop after SECS of wall time (`0` = no limit) | `0.0` |

### `dump1090-replay info`

| Flag | Meaning | Default |
| --- | --- | --- |
| `FILE` | Recording to summarise (positional) | required |

### The NDJSON format

One JSON object per line, UTF-8, LF-terminated:

```json
{"t": 1791028800.0, "aircraft": [{"hex": "4ca918", "flight": "RYR22GK ", "alt_baro": 34000, "seen": 0.5, "seen_pos": 0.7, "...": "..."}]}
```

- `t` — unix epoch seconds: the decoder's own `now` at capture time.
- `aircraft` — the raw `aircraft` array, every field preserved verbatim.
  `seen` / `seen_pos` are ages in seconds relative to `t`; `alt_baro` may be
  the string `"ground"`.

Readers also tolerate optional `messages`, `receiver` and `stats` keys, but
writers only emit `t` and `aircraft`, so any file produced here loads in
dump1090-recorder and dump1090-influx and vice versa.

## How it works

```
 record                                   play
┌──────────────────────────┐   ┌─────────────────────────────────────────────┐
│ decoder /data/aircraft.json │ │ ndjson.py ─▶ session.py (lock + clock)     │
│   │ urllib.request,      │   │    │  ReplayClock: virtual = t0 + elapsed×speed
│   ▼                      │   │    ▼                                        │
│ record.py ──▶ flight.ndjson │ │ player.py paces records: wait until clock  │
│ {"t", "aircraft"}        │   │ reaches t, set_index(), fire callbacks      │
└──────────────────────────┘   │    ├─▶ server.py  → GET /data/aircraft.json │
                               │    │   (now/seen offset by clock, CORS)     │
                               │    └─▶ history.py → history_NNN.json ring   │
                               └─────────────────────────────────────────────┘
```

- **`clock.py` owns time.** `virtual_now = first_t + (wall_elapsed - paused) ×
  speed`. Everything downstream reads that clock, which is why `now`,
  `seen`, `seen_pos` and the stats buckets never disagree.
- **`session.py` is the single shared state** between the play loop (writer)
  and HTTP handler threads (readers): current record + pass number under one
  lock. `--loop` shifts each pass by the recording's span, so ages stay
  monotonic across restarts.
- **`snapshot.py` builds the documents**: `build_aircraft_doc` shifts only the
  age fields (`seen`, `seen_pos`) by `now - record_time`; `MessageCounter`
  reconstructs dump1090's cumulative `messages` counter (monotonic, derived
  from per-aircraft counts since the shared format does not store it).
- **`player.py` is the loop**: it sleeps in short naps clipped to
  `MAX_NAP` and the next tick, so `--duration`, `stop()` and Ctrl+C stay
  responsive even inside a long inter-record gap.
- **`server.py`** is a `ThreadingHTTPServer` answering the three documents
  dump1090 clients expect, plus a text index at `/`.

## Development

```bash
git clone https://github.com/technothatch/dump1090-replay.git
cd dump1090-replay
pip install -e ".[dev]"

make lint     # ruff check + ruff format --check + mypy
make test     # pytest with coverage (fail-under 80)
make check    # everything, the same gate CI runs
make demo     # replay examples/demo.ndjson on http://127.0.0.1:8090
```

No `make`? The underlying commands are:

```bash
ruff check .
ruff format --check .
mypy src
python -m pytest -q --cov --cov-report=term-missing --cov-fail-under=80
```

The suite — 127 tests at 99% line and branch coverage — covers NDJSON
round-trips and validation errors, clock/speed/pause math, pacing and
loop/stop conditions with a fake clock, `history_NNN.json` shape and
rotation, the HTTP handler driven through a real threaded server on port 0
(`http.client`), and the CLI end to end. **No test contacts a decoder**;
fixtures are written inline.

## Related projects

- [dump1090-fa](https://github.com/flightaware/dump1090) — FlightAware's decoder
- [readsb](https://github.com/wiedehopf/readsb) — high-performance fork, JSON docs in-repo
- [tar1090](https://github.com/wiedehopf/tar1090) — web UI for readsb
- [graphs1090](https://github.com/wiedehopf/graphs1090) — statistics graphs
- [dump1090-recorder](https://github.com/technothatch/dump1090-recorder) — long-term SQLite flight recorder (same NDJSON)
- [dump1090-influx](https://github.com/technothatch/dump1090-influx) — InfluxDB/Telegraf exporter (same NDJSON)
- [dump1090-alerts](https://github.com/technothatch/dump1090-alerts) — rule engine over `aircraft.json`
- [dump1090-simulator](https://github.com/technothatch/dump1090-simulator) — synthetic traffic generator

## License

MIT © technothatch. See [LICENSE](LICENSE).
