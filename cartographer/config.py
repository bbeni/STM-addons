"""Per-STM configuration of the cartographer.

Intent (prompt, 2026-10-05): "there should be a config file, that is specific to
the STM, steps number and voltage matter for the physical length travelled. this
config rarely changes. it should spawn it right next to the script if it does
not exist. with sensible defaults. piezo walker can also differ in x+ x- y+ y-
z+ z-, so calibrate individually."
Follow-up (2026-10-06): defaults from the real STM (1 step z+ = 44 nm; 100 steps
z+ are undone by about 44 steps z-; x and y like z+); map colors and the folder
with the dated measurement folders are set here too. Keys missing from an older
config file take their default.
"""

import tomllib
from pathlib import Path

from history import DIRECTIONS

CONFIG_PATH = Path(__file__).parent / "config.toml"

DEFAULT_CONFIG = """\
# cartographer configuration for this STM.
# Created automatically with defaults. Edit it to match your microscope.

# Distance the tip travels per coarse step, in nm, for each direction.
# Calibrate each one: walk a known number of steps, measure the distance
# travelled (e.g. on a known feature or edge) and divide.
# Step length depends on the drive voltage: calibrate at the voltage you use.
[step_nm]
"x+" = 44.0
"x-" = 44.0
"y+" = 44.0
"y-" = 44.0
"z+" = 44.0
"z-" = 100.0  # rough: 100 steps z+ need about 44 steps z- back

[approach]
direction = "z-"  # direction the auto approach moves the tip

[nanonis]
host = "localhost"
port = 6501

[defaults]
nsteps = 100
radius_nm = 100.0  # effect size of annotations and crashes on the map

[files]
# New and opened histories start in the newest folder of data_root named like
# 2026-10-03 (year-month-day). Without one, they start in the current directory.
data_root = "E:/"

[map]
cloud_radius_nm = 2000.0  # how far around an approach the sample (or plate) is shown

# Colors: names like "gray" or hex codes like "#1f6fb4".
[colors]
sample_cloud = "#1f6fb4"  # around approaches on the sample
plate_cloud = "#9aa0a6"   # around approaches off the sample, on the sample plate
on_sample = "#1f6fb4"     # path on the sample
off_sample = "#9aa0a6"    # path off the sample
tip = "#d62728"
approach = "#e67e00"
crash = "#d62728"
plan = "#555555"          # the way back
clean = "#2ca02c"
flat = "#17a2b8"
dirty = "#8c564b"
"tip shaped" = "#9467bd"
other = "#7f7f7f"         # notes without a tag
"""


def load_config(path=CONFIG_PATH):
    path = Path(path)
    if not path.exists():
        path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    config = merged(tomllib.loads(DEFAULT_CONFIG), tomllib.loads(path.read_text(encoding="utf-8")))

    step_nm = config["step_nm"]
    missing = [d for d in DIRECTIONS if d not in step_nm]
    if missing:
        raise ValueError(f"{path}: [step_nm] is missing {missing}")
    if any(step_nm[d] <= 0 for d in DIRECTIONS):
        raise ValueError(f"{path}: step lengths must be positive")
    if config["approach"]["direction"] not in DIRECTIONS:
        raise ValueError(f"{path}: [approach] direction must be one of {DIRECTIONS}")
    return config


def merged(defaults, config):
    """config, with what it leaves out taken from defaults (section by section)."""
    result = dict(defaults)
    for key, value in config.items():
        if isinstance(value, dict) and isinstance(defaults.get(key), dict):
            value = merged(defaults[key], value)
        result[key] = value
    return result
