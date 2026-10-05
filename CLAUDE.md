# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Add-ons for an STM controlled by Nanonis, via the `nanonis_spm` package over TCP (`localhost:6501`).

## cartographer

Tracks coarse walking (x/y/z steps, approach start/stop) issued from its own UI, and shows visited locations and user annotations (dirty, clean, flat, tip shaped, on/off sample) on a 2D map.
- The file format is history-based: any cartographer state can be rebuilt from the file alone. This is the current focus.
- A per-STM config holds step-length calibration for each direction (x±, y±, z±), which depends on steps and voltage. If missing, it's created next to the script with defaults.
- Calibration is a fixed step length (nm per step) for each direction. Events still record amplitude and frequency.
- Each history file is for one sample. It starts on the sample. Toggling "on sample" applies to the current position, and the edge is assumed halfway along the last move.
- Only moves made through the cartographer UI are tracked.
- The UI is a desktop app (PyQt + pyqtgraph) and must run without Nanonis.
- `history.py` defines the file format (bump `FORMAT_VERSION` on incompatible changes). `timeline.py` replays it. The format spec is in `cartographer/README.md`, so keep that in sync.
- Run: `python launch.py cartographer --simulate` (repo root). `launch.py` hosts all tools: each tool folder has `main.py` with `main(argv)`. `backend.py` holds the only Nanonis calls, plus a `Simulator`.
- Environment: `conda env create -f environment.yml`, then `conda activate stm-addons`.
- Tests: `cd cartographer && python -m unittest`.

Calls like `Motor_StartMove`, `Motor_FreqAmpSet` and `AutoApproach_OnOffSet` move real hardware. Never execute them without the user's explicit go-ahead.
