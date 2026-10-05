"""Launcher for the STM add-ons.

Intent (prompt, 2026-10-05): "use 'launch.py cartographer' as it should later
host also other tools."

    python launch.py cartographer [--simulate] [history.jsonl]

Each tool is a folder next to this file with a main.py that has main(argv).
"""

import argparse
import importlib
import sys
from pathlib import Path

TOOLS = {
    "cartographer": "track the coarse moves of the STM tip on a map",
}


def main():
    parser = argparse.ArgumentParser(
        description="STM add-ons",
        epilog="tools:\n" + "\n".join(f"  {name:14} {text}" for name, text in TOOLS.items()),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tool", choices=TOOLS)
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="passed on to the tool")
    args = parser.parse_args()

    # tool modules import each other by plain name, from their own folder
    sys.path.insert(0, str(Path(__file__).parent / args.tool))
    importlib.import_module("main").main(args.arguments, prog=f"launch.py {args.tool}")


if __name__ == "__main__":
    main()
