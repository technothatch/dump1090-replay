"""Command-line interface for dump1090-replay."""

from __future__ import annotations

import argparse
import datetime
import os
import sys
import time
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from dump1090_replay import __version__
from dump1090_replay.errors import ReplayError
from dump1090_replay.history import HistoryWriter
from dump1090_replay.ndjson import read_ndjson
from dump1090_replay.player import play
from dump1090_replay.record import DEFAULT_HOST, resolve_url
from dump1090_replay.record import record as record_live
from dump1090_replay.server import BackgroundServer
from dump1090_replay.session import Session

EXIT_OK = 0
EXIT_ERROR = 1

__all__ = ["EXIT_ERROR", "EXIT_OK", "main"]


def _positive_float(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"'{raw}' is not a number") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {raw}")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dump1090-replay",
        description=(
            "Record dump1090 / readsb aircraft.json history and replay it faithfully "
            "- no decoder needed while developing UIs, alerts and tools."
        ),
        epilog="ADS-B data is public by design; nothing here contacts the network unless asked to.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("record", help="record a live decoder into an NDJSON file")
    rec.add_argument(
        "--url",
        metavar="URL",
        default=None,
        help=(f"decoder base URL or aircraft.json URL (env DUMP1090_URL, default {DEFAULT_HOST})"),
    )
    rec.add_argument(
        "--interval",
        type=float,
        default=1.0,
        metavar="SECS",
        help="seconds between polls (default: 1)",
    )
    rec.add_argument(
        "--out", metavar="PATH", required=True, help="NDJSON file to write (truncated)"
    )
    rec.add_argument("--timeout", type=float, default=5.0, metavar="SECS", help="HTTP timeout")
    rec.add_argument(
        "--max-polls",
        type=int,
        default=0,
        metavar="N",
        help="stop after N polls (0 = run until interrupted)",
    )

    play_p = sub.add_parser("play", help="replay an NDJSON recording")
    play_p.add_argument("file", metavar="NDJSON", help="recording produced by 'record'")
    play_p.add_argument(
        "--speed",
        type=_positive_float,
        default=1.0,
        metavar="X",
        help="replay speed multiplier (default: 1 = realtime)",
    )
    play_p.add_argument(
        "--loop", action="store_true", help="restart at the end instead of stopping"
    )
    play_p.add_argument(
        "--http",
        type=int,
        default=8090,
        metavar="PORT",
        help="port for /data/*.json (default: 8090, 0 disables HTTP)",
    )
    play_p.add_argument("--bind", default="127.0.0.1", metavar="ADDR", help="HTTP bind address")
    play_p.add_argument(
        "--realtime-file",
        metavar="DIR",
        default=None,
        help="also rewrite history_NNN.json + aircraft.json into DIR",
    )
    play_p.add_argument(
        "--interval",
        type=_positive_float,
        default=1.0,
        metavar="SECS",
        help="wall seconds between history writes (default: 1)",
    )
    play_p.add_argument(
        "--history-size",
        type=int,
        default=600,
        metavar="N",
        help="history ring buffer length (default: 600)",
    )
    play_p.add_argument("--lat", type=float, default=0.0, metavar="DEG", help="receiver latitude")
    play_p.add_argument("--lon", type=float, default=0.0, metavar="DEG", help="receiver longitude")
    play_p.add_argument(
        "--duration",
        type=float,
        default=0.0,
        metavar="SECS",
        help="stop after SECS of wall time (0 = no limit)",
    )

    info = sub.add_parser("info", help="summarise a recording")
    info.add_argument("file", metavar="NDJSON", help="recording to summarise")
    return parser


def _cmd_record(
    args: argparse.Namespace,
    *,
    opener: Callable[..., Any],
    sleep: Callable[[float], None],
    now: Callable[[], float],
) -> int:
    url = args.url or os.environ.get("DUMP1090_URL", DEFAULT_HOST)
    endpoint = resolve_url(url)
    print(f"# recording {endpoint} every {args.interval}s -> {args.out}", file=sys.stderr)
    try:
        written = record_live(
            endpoint,
            out=args.out,
            interval=args.interval,
            max_polls=args.max_polls,
            timeout=args.timeout,
            opener=opener,
            sleep=sleep,
            now=now,
        )
    except ReplayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"# wrote {written} snapshot(s) to {args.out}", file=sys.stderr)
    return EXIT_OK if written else EXIT_ERROR


def _cmd_play(
    args: argparse.Namespace,
    *,
    sleep: Callable[[float], None],
    mono: Callable[[], float],
) -> int:
    try:
        records = read_ndjson(args.file)
    except ReplayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    session = Session(
        records,
        speed=args.speed,
        lat=args.lat,
        lon=args.lon,
        refresh=args.interval,
        mono=mono,
    )
    writer: HistoryWriter | None = None
    if args.realtime_file:
        try:
            writer = HistoryWriter(args.realtime_file, size=args.history_size)
        except ReplayError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_ERROR

    server: BackgroundServer | None = None
    if args.http:
        server = BackgroundServer(session, bind=args.bind, port=args.http)
        try:
            port = server.start()
        except OSError as exc:
            print(f"error: cannot listen on {args.bind}:{args.http}: {exc}", file=sys.stderr)
            return EXIT_ERROR
        location = f"http://{args.bind}:{port}"
    else:
        location = "http disabled"

    flags = ", ".join(
        part
        for part in (
            f"speed {args.speed:g}x",
            "loop" if args.loop else None,
            f"{location}" if args.http else None,
            f"history -> {args.realtime_file}" if writer else None,
        )
        if part
    )
    print(f"# {args.file} - {len(records)} record(s), {flags}", file=sys.stderr)

    def on_tick() -> None:
        if writer is not None:
            writer.write(session.aircraft_doc())

    try:
        try:
            result = play(
                session,
                loop=args.loop,
                duration=args.duration,
                tick_interval=args.interval,
                on_tick=on_tick,
                sleep=sleep,
                mono=mono,
            )
        finally:
            if writer is not None:
                writer.write(session.aircraft_doc())
    except ReplayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\n# stopped", file=sys.stderr)
        return EXIT_OK
    finally:
        if server is not None:
            server.stop()

    print(
        f"# served {result.records_served} record(s), {result.passes} pass(es), "
        f"{result.elapsed:.1f}s wall",
        file=sys.stderr,
    )
    return EXIT_OK


def _cmd_info(args: argparse.Namespace) -> int:
    try:
        records = read_ndjson(args.file)
    except ReplayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    span = records[-1].t - records[0].t
    hexes: set[str] = set()
    callsigns: set[str] = set()
    for record in records:
        for entry in record.aircraft:
            hex_value = entry.get("hex")
            if isinstance(hex_value, str):
                hexes.add(hex_value.lower())
            flight = entry.get("flight")
            if isinstance(flight, str) and flight.strip():
                callsigns.add(flight.strip())
    print(f"{args.file}: {len(records)} record(s) spanning {span:.1f}s")
    print(f"  first {_fmt_ts(records[0].t)}, last {_fmt_ts(records[-1].t)}")
    print(f"  {len(hexes)} aircraft, {len(callsigns)} callsign(s)")
    if hexes:
        print(f"  hex: {', '.join(sorted(hexes))}")
    return EXIT_OK


def _fmt_ts(epoch: float) -> str:
    moment = datetime.datetime.fromtimestamp(epoch, tz=datetime.UTC)
    return moment.strftime("%Y-%m-%d %H:%M:%S UTC")


def main(
    argv: Sequence[str] | None = None,
    *,
    opener: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    mono: Callable[[], float] = time.monotonic,
    now: Callable[[], float] = time.time,
) -> int:
    """Entry point. Returns a process exit code.

    Args:
        argv: Argument vector (defaults to ``sys.argv[1:]``).
        opener: Injectable HTTP opener for ``record`` (tests).
        sleep: Injectable sleep (tests).
        mono: Injectable monotonic clock (tests).
        now: Injectable wall clock for ``record`` fallback timestamps (tests).
    """
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    http_opener = opener if opener is not None else urllib.request.urlopen

    if args.command == "record":
        return _cmd_record(args, opener=http_opener, sleep=sleep, now=now)
    if args.command == "play":
        return _cmd_play(args, sleep=sleep, mono=mono)
    return _cmd_info(args)


if __name__ == "__main__":
    sys.exit(main())
