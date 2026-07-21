from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime
from pathlib import Path

from xtalflow.domain.mxlive import MxLiveReadError
from xtalflow.infrastructure.mxlive_client import LegacyMxLiveReadClient
from xtalflow.settings import DEFAULT_SETTINGS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read MxLive v2 experiments and sample count (no writes)"
    )
    parser.add_argument("--url", default=DEFAULT_SETTINGS.mxlive_base_url, required=DEFAULT_SETTINGS.mxlive_base_url is None)
    parser.add_argument("--beamline", default=DEFAULT_SETTINGS.mxlive_beamline)
    parser.add_argument("--username", default=getpass.getuser())
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--ca", type=Path)
    parser.add_argument("--year", type=int, default=datetime.now().year)
    parser.add_argument(
        "--include-sample-count", action="store_true",
        help="also call the samples endpoint and print only its count",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        client = LegacyMxLiveReadClient(
            args.url, args.beamline, args.username, args.key,
            ca_bundle=args.ca,
            timeout_seconds=DEFAULT_SETTINGS.mxlive_timeout_seconds,
        )
        experiment_ids = client.experiment_ids(args.year)
        print(f"MxLive experiments ({args.year}): {len(experiment_ids)}")
        for experiment_id in experiment_ids:
            print(experiment_id)
        if args.include_sample_count:
            print(f"MxLive samples: {len(client.samples())}")
    except (MxLiveReadError, ValueError) as error:
        print(f"xtalflow-mxlive-read: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
