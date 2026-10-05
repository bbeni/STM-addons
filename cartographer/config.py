"""Per-STM configuration of the cartographer.

Intent (prompt, 2026-10-05): "there should be a config file, that is specific to
the STM, steps number and voltage matter for the physical length travelled. this
config rarely changes. it should spawn it right next to the script if it does
not exist. with sensible defaults. piezo walker can also differ in x+ x- y+ y-
z+ z-, so calibrate individually."
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
"x+" = 100.0
"x-" = 100.0
"y+" = 100.0
"y-" = 100.0
"z+" = 100.0
"z-" = 100.0

[approach]
direction = "z-"  # direction the auto approach moves the tip

[nanonis]
host = "localhost"
port = 6501

[defaults]
nsteps = 100
radius_nm = 100.0  # effect size of annotations and crashes on the map
"""


def load_config(path=CONFIG_PATH):
    path = Path(path)
    if not path.exists():
        path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    config = tomllib.loads(path.read_text(encoding="utf-8"))

    step_nm = config["step_nm"]
    missing = [d for d in DIRECTIONS if d not in step_nm]
    if missing:
        raise ValueError(f"{path}: [step_nm] is missing {missing}")
    if any(step_nm[d] <= 0 for d in DIRECTIONS):
        raise ValueError(f"{path}: step lengths must be positive")
    if config["approach"]["direction"] not in DIRECTIONS:
        raise ValueError(f"{path}: [approach] direction must be one of {DIRECTIONS}")
    return config
