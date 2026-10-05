"""History file of the cartographer: an append-only timeline of events.

Intent (prompt, 2026-10-05): "the focus in this part of the development is the
file format: history based so every state of the cartographer can be reproduced
just with the file." Include a file format version; every step is tracked so the
timeline can be stepped through forwards and backwards.

Format: JSON Lines (one JSON object per line, UTF-8).
  - line 1 is the header:  {"format": "cartographer-history", "version": 1, ...}
  - every further line is one event, numbered by "seq" (0, 1, 2, ...)
Events are only ever appended, never edited, so a crash can lose at most the
line being written, and the file reads like a lab logbook.
"""

import json
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

FORMAT_NAME = "cartographer-history"
FORMAT_VERSION = 1

DIRECTIONS = ("x+", "x-", "y+", "y-", "z+", "z-")
NSTEPS_MAX = 2**16  # Nanonis limit per Motor_StartMove


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def check_steps(direction, nsteps, nsteps_max=NSTEPS_MAX):
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}, got {direction!r}")
    if nsteps < 0 or (nsteps_max is not None and nsteps > nsteps_max):
        raise ValueError(f"nsteps out of range: {nsteps}")


# --- Event types -------------------------------------------------------------
# Each event is a small dataclass. On disk it becomes
#   {"seq": 7, "time": "...", "type": "<type>", <fields...>}
# Units are part of the field names; nsteps are plain counts.


@dataclass
class Event:
    seq: int = field(default=-1, kw_only=True)  # assigned on append
    time: str = field(default_factory=now, kw_only=True)


@dataclass
class Sample(Event):
    """Which sample this history is about. Written when the history is created;
    a later sample event corrects or extends it."""

    type = "sample"
    name: str
    description: str = ""


@dataclass
class SessionStart(Event):
    """The program was started. Carries the calibration in use, so the history
    never depends on the config file as it is today."""

    type = "session_start"
    step_nm: dict  # nm per step for each of DIRECTIONS
    simulated: bool = False  # True: no hardware moved, this session is a test


@dataclass
class Move(Event):
    """A coarse move along exactly one axis, as commanded."""

    type = "move"
    direction: str
    nsteps: int
    amplitude_V: float
    frequency_Hz: float

    def __post_init__(self):
        check_steps(self.direction, self.nsteps)


@dataclass
class MoveStop(Event):
    """The preceding move was stopped early after nsteps_done steps
    (usually estimated from elapsed time x frequency)."""

    type = "move_stop"
    nsteps_done: int


@dataclass
class ApproachStart(Event):
    type = "approach_start"


@dataclass
class ApproachStop(Event):
    type = "approach_stop"


@dataclass
class ApproachSteps(Event):
    """Asked for after every approach: how many z steps it took (Nanonis does
    not report it). nsteps is None if the user did not know; the replay then
    assumes the surface is as high as at the nearest earlier approach."""

    type = "approach_steps"
    direction: str
    nsteps: int | None

    def __post_init__(self):
        check_steps(self.direction, self.nsteps or 0, nsteps_max=None)


@dataclass
class Annotate(Event):
    """Remark about the surroundings of the current position, e.g. tags
    ["dirty"], ["clean", "flat"] or ["tip shaped"]."""

    type = "annotate"
    tags: list
    radius_nm: float
    note: str = ""


@dataclass
class Crash(Event):
    """The tip crashed somewhere along an earlier x/y move (referenced by its seq)."""

    type = "crash"
    move_seq: int
    radius_nm: float
    note: str = ""


@dataclass
class OnSample(Event):
    """The current position, and all following ones, are on (or off) the sample.
    The edge was crossed somewhere during the last move; the replay assumes halfway."""

    type = "on_sample"
    on_sample: bool


EVENT_TYPES = {cls.type: cls for cls in (
    Sample, SessionStart, Move, MoveStop, ApproachStart, ApproachStop,
    ApproachSteps, Annotate, Crash, OnSample,
)}


def event_to_dict(event):
    data = asdict(event)
    # keep seq, time and type first so lines are easy to scan by eye
    return {"seq": data.pop("seq"), "time": data.pop("time"), "type": event.type, **data}


def event_from_dict(data):
    data = dict(data)
    cls = EVENT_TYPES.get(data.pop("type", None))
    if cls is None:
        raise ValueError(f"unknown event type in {data}")
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"unknown fields {sorted(unknown)} for event {cls.type}")
    return cls(**data)


# --- File --------------------------------------------------------------------


class HistoryError(Exception):
    pass


def read_history(path):
    """Return the list of events stored in a history file."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines:
        raise HistoryError(f"{path}: empty file, header missing")

    header = json.loads(lines[0])
    if header.get("format") != FORMAT_NAME:
        raise HistoryError(f"{path}: not a {FORMAT_NAME} file")
    if header.get("version") != FORMAT_VERSION:
        raise HistoryError(
            f"{path}: format version {header.get('version')} not supported "
            f"(this program reads version {FORMAT_VERSION})")

    events = []
    for line_number, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        try:
            event = event_from_dict(json.loads(line))
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise HistoryError(f"{path}:{line_number}: {error}") from error
        if event.seq != len(events):
            raise HistoryError(f"{path}:{line_number}: expected seq {len(events)}, found {event.seq}")
        events.append(event)
    return events


class HistoryFile:
    """Open (or create) a history file and append events to it."""

    def __init__(self, path):
        self.path = Path(path)
        if self.path.exists():
            self.events = read_history(self.path)
        else:
            self.events = []
            header = {"format": FORMAT_NAME, "version": FORMAT_VERSION, "created": now()}
            self.path.write_text(json.dumps(header) + "\n", encoding="utf-8")

    def append(self, event):
        event.seq = len(self.events)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event_to_dict(event), ensure_ascii=False) + "\n")
            file.flush()
            os.fsync(file.fileno())  # the event is on disk before we carry on
        self.events.append(event)
        return event
