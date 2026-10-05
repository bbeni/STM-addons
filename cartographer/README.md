# cartographer

Keeps a map of where the STM tip has been on the sample. It records every coarse step, every approach and every remark you make, and draws them on a 2D map.

## Start

```
conda activate stm-addons
python launch.py cartographer               # from the repository root: new sample or open a history
python launch.py cartographer --simulate    # without Nanonis, nothing moves
```

Each sample gets its own history file. You choose where to store it. The suggested name is the sample name with date and time, e.g. `2026-10-05_17-52_Au_111_42.jsonl`. On first start, `cartographer/config.toml` is created with default settings. Edit it to set this STM's step lengths for each direction.

## For the operator

The map is only correct if cartographer sees every movement.

1. **Move only with the cartographer buttons.** Don't use the motor panel or the auto-approach button in Nanonis. Cartographer can't see those moves, and the map will be wrong from then on.
2. **Stop a move with the cartographer Stop button,** not in Nanonis.
3. **When an approach finishes, enter how many z steps it took.** Nanonis doesn't report this number. If you don't know, the surface is assumed to be as high as at the nearest earlier approach.
4. **Right after the move that left the sample or reached it, press the sample button.** The edge is assumed to be halfway along that move.
5. **Annotate what you find:** dirty, clean, flat, tip shaped, or a note, along with how far around it applies (radius).
6. **If the tip crashed during an x/y move,** mark that move as a crash.

## History file

Everything is stored in one history file (`*.jsonl`). Replaying the file reproduces every earlier state of the map, so you can step back and forth in time and plan the way back to any earlier point.

- The file is UTF-8 text with one JSON object per line. Lines are only ever added.
- Line 1 is a header holding the format version: `{"format": "cartographer-history", "version": 1, ...}`
- Every following line is one event: `seq` (0, 1, 2, ...), `time` (ISO 8601 date and time with time zone), `type` and its fields.
- Each field name ends in its unit (`radius_nm`, `amplitude_V`). Step counts are plain numbers.

| type | fields | meaning |
|---|---|---|
| `sample` | `name`, `description` | which sample; written when the history is created |
| `session_start` | `step_nm` per direction, `simulated` | program started; calibration in use; `true` if no hardware was moved |
| `move` | `direction` (`x+` … `z-`), `nsteps`, `amplitude_V`, `frequency_Hz` | coarse move along one axis, as commanded |
| `move_stop` | `nsteps_done` | previous move stopped early |
| `approach_start` / `approach_stop` | | auto approach on / off |
| `approach_steps` | `direction`, `nsteps` (`null` = don't know) | z steps of the last approach, entered by hand |
| `annotate` | `tags`, `radius_nm`, `note` | remark about the current position |
| `crash` | `move_seq`, `radius_nm`, `note` | the tip crashed along the x/y move `move_seq` |
| `on_sample` | `on_sample` | the current position and all later ones are on / off the sample; the edge is assumed halfway along the last move. A history starts on the sample. |

Example:

```
{"format": "cartographer-history", "version": 1, "created": "2026-10-05T17:51:02+02:00"}
{"seq": 0, "time": "2026-10-05T17:51:02+02:00", "type": "sample", "name": "Au(111) #42", "description": "sputter/anneal 3 cycles"}
{"seq": 1, "time": "2026-10-05T17:51:02+02:00", "type": "session_start", "step_nm": {"x+": 100.0, "x-": 120.0, "y+": 80.0, "y-": 80.0, "z+": 50.0, "z-": 40.0}, "simulated": false}
{"seq": 2, "time": "2026-10-05T17:51:02+02:00", "type": "move", "direction": "x+", "nsteps": 10, "amplitude_V": 190.0, "frequency_Hz": 1000.0}
{"seq": 3, "time": "2026-10-05T17:51:02+02:00", "type": "on_sample", "on_sample": false}
{"seq": 4, "time": "2026-10-05T17:51:02+02:00", "type": "move", "direction": "x-", "nsteps": 10, "amplitude_V": 190.0, "frequency_Hz": 1000.0}
{"seq": 5, "time": "2026-10-05T17:51:02+02:00", "type": "move_stop", "nsteps_done": 5}
{"seq": 6, "time": "2026-10-05T17:51:02+02:00", "type": "approach_start"}
{"seq": 7, "time": "2026-10-05T17:51:02+02:00", "type": "approach_stop"}
{"seq": 8, "time": "2026-10-05T17:51:02+02:00", "type": "approach_steps", "direction": "z-", "nsteps": null}
{"seq": 9, "time": "2026-10-05T17:51:02+02:00", "type": "crash", "move_seq": 4, "radius_nm": 200.0, "note": "current jumped"}
```

## Development

Run the tests from this folder: `python -m unittest` (in the `stm-addons` conda environment)
