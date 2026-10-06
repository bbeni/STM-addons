# cartographer

Keeps a map of where the STM tip has been on the sample. It records every coarse step, every approach and every remark you make, and draws them on a 2D map.

## Start

```
conda activate stm-addons
python launch.py cartographer               # from the repository root: new sample or open a history
python launch.py cartographer --simulate    # without Nanonis, nothing moves
```

Each sample gets its own history file. You choose where to store it. The suggested name is date, time and sample name, e.g. `20261006_1520_Au_111.jsonl`. Storing and opening start in the newest folder named like `2026-10-03` in `E:/`, or in the current directory if there is none.

On first start, `cartographer/config.toml` is created with default settings. Edit it to set this STM's step lengths for each direction, the folder that holds the dated folders (`data_root`), the size of the sample cloud and the map colors. Settings missing from an older config file take their default.

## For the operator

The map is only correct if cartographer sees every movement.

1. **Move only with the cartographer buttons.** Don't use the motor panel or the auto-approach button in Nanonis. Cartographer can't see those moves, and the map will be wrong from then on.
2. **Stop a move with the cartographer Stop button,** not in Nanonis.
3. **When an approach finishes, enter how many z steps it took.** Nanonis doesn't report this number. If you don't know, the surface is assumed to be as high as at the nearest earlier approach.
4. **Right after the move that left the sample or reached it, press the sample button.** The edge is assumed to be halfway along that move.
5. **Annotate what you find:** dirty, clean, flat, tip shaped, or a note, along with how far around it applies (radius).
6. **If the tip crashed during an x/y move,** mark that move as a crash.
7. **To correct a remark,** select it in the timeline and press Delete. **To add a forgotten one,** go to its point in the timeline (arrow keys) and annotate. It is inserted there. Moves always happen now and can't be deleted.

On the map, the dashed path has an arrow in the middle of each move. Dots mark the approaches. A faint blue cloud spreads around the approaches on the sample, a grey one around those on the sample plate. Annotations show a smile (clean or flat), a frown (dirty) or a star (tip shaped). A crash is a red star.

## History file

Everything is stored in one history file (`*.jsonl`). Replaying the file reproduces every earlier state of the map, so you can step back and forth in time and plan the way back to any earlier point.

- The file is UTF-8 text with one JSON object per line. Lines are only ever added.
- Line 1 is a header holding the format version the file was created with: `{"format": "cartographer-history", "version": 2, ...}`. Version 1 files are read and continued as they are, because version 2 only adds `delete` and `after_seq`.
- Every following line is one event: `seq` (0, 1, 2, ...), `time` (ISO 8601 date and time with time zone), `type` and its fields.
- Each field name ends in its unit (`radius_nm`, `amplitude_V`). Step counts are plain numbers.
- The timeline is the events in file order, with two corrections. An event with `after_seq` was recorded later but belongs right after event `after_seq`; only `annotate`, `crash` and `on_sample` can be inserted like this. A `delete` removes its target from the timeline; only `annotate`, `crash`, `on_sample` and `approach_steps` can be deleted. The deleted line stays in the file.

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
| `on_sample` | `on_sample` | the current position and all later ones are on / off the sample; the edge is assumed halfway along the last move. Approaches made at the current position count as on / off the sample too. A history starts on the sample. |
| `delete` | `target_seq` | removes event `target_seq` from the timeline (a wrong remark) |

Example:

```
{"format": "cartographer-history", "version": 2, "created": "2026-10-05T17:51:02+02:00"}
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
{"seq": 10, "time": "2026-10-05T17:53:40+02:00", "type": "annotate", "after_seq": 2, "tags": ["clean"], "radius_nm": 100.0, "note": ""}
{"seq": 11, "time": "2026-10-05T17:54:12+02:00", "type": "delete", "target_seq": 9}
```

## Development

Run the tests from this folder: `python -m unittest` (in the `stm-addons` conda environment)
