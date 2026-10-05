"""Rebuild the cartographer's state from history events.

Intent (prompt, 2026-10-05): all steps are tracked so we can step forwards and
backwards through the history and see their effects, and compute the path back
to any history point. Annotations and crashes have an effect size (radius) for
display; a crash is tied to the x/y move during which it happened.
Follow-up: we start on the sample; when it is left (or reached), assume the edge
halfway along the last move. If the approach steps are unknown, assume the
surface is at the height of the closest earlier approach.

The state after event i is defined as the replay of events 0..i. Nothing else
is stored, so the history file is the single source of truth.
"""

from dataclasses import dataclass, field

from history import (
    Annotate, ApproachStart, ApproachSteps, ApproachStop, Crash, Move,
    MoveStop, NSTEPS_MAX, OnSample, Sample, SessionStart,
)

# direction -> (axis, sign)
AXES = {
    "x+": ("x", +1), "x-": ("x", -1),
    "y+": ("y", +1), "y-": ("y", -1),
    "z+": ("z", +1), "z-": ("z", -1),
}


@dataclass
class Visit:
    """Where an x/y move ended. If on_sample differs from the previous visit,
    the sample edge lies halfway in between."""
    seq: int
    x_nm: float
    y_nm: float
    on_sample: bool


@dataclass
class Approach:
    """Where an approach was made, and the z of the surface found there."""
    seq: int
    x_nm: float
    y_nm: float
    z_nm: float = None  # None until the steps are known or assumed
    z_assumed: bool = False  # steps were unknown: z taken from the closest approach


@dataclass
class Mark:
    """An annotation, placed at the position where it was made."""
    seq: int
    x_nm: float
    y_nm: float
    tags: list
    radius_nm: float
    note: str


@dataclass
class CrashLine:
    """A crash somewhere along the line of one x/y move."""
    seq: int
    move_seq: int
    start_nm: tuple
    end_nm: tuple
    radius_nm: float
    note: str


@dataclass
class State:
    sample_name: str = ""
    sample_description: str = ""
    x_nm: float = 0.0
    y_nm: float = 0.0
    z_nm: float = 0.0
    step_nm: dict = None  # calibration of the current session
    on_sample: bool = True  # a history starts on the sample
    approaching: bool = False
    visits: list = field(default_factory=list)
    approaches: list = field(default_factory=list)
    marks: list = field(default_factory=list)
    crashes: list = field(default_factory=list)
    # (start, end) of every x/y move by seq, so crashes and stops can refer back
    move_lines: dict = field(default_factory=dict)
    last_move: Move = None

    @property
    def xy_nm(self):
        return (self.x_nm, self.y_nm)


def replay(events):
    state = State()
    for event in events:
        apply(state, event)
    return state


def apply(state, event):
    """Advance state by one event (in place)."""
    match event:
        case Sample():
            state.sample_name = event.name
            state.sample_description = event.description

        case SessionStart():
            state.step_nm = dict(event.step_nm)
            if not state.visits:
                state.visits.append(Visit(event.seq, state.x_nm, state.y_nm, state.on_sample))

        case Move():
            require_session(state, event)
            start_nm = state.xy_nm
            shift(state, event.direction, event.nsteps)
            state.last_move = event
            if AXES[event.direction][0] != "z":  # z moves don't change the map position
                state.move_lines[event.seq] = (start_nm, state.xy_nm)
                state.visits.append(Visit(event.seq, state.x_nm, state.y_nm, state.on_sample))

        case MoveStop():
            # Undo the steps that were commanded but not done. The visit and
            # line of the move are corrected, not added.
            move = state.last_move
            if move is None:
                raise ValueError(f"seq {event.seq}: move_stop without a preceding move")
            shift(state, move.direction, event.nsteps_done - move.nsteps)
            state.last_move = None  # a move can only be stopped once
            if move.seq in state.move_lines:
                start_nm, _ = state.move_lines[move.seq]
                state.move_lines[move.seq] = (start_nm, state.xy_nm)
                visit = state.visits[-1]
                visit.x_nm, visit.y_nm = state.xy_nm

        case ApproachStart():
            state.approaching = True
            state.approaches.append(Approach(event.seq, state.x_nm, state.y_nm))

        case ApproachStop():
            state.approaching = False

        case ApproachSteps():
            require_session(state, event)
            if not state.approaches:
                raise ValueError(f"seq {event.seq}: approach_steps without an approach")
            approach = state.approaches[-1]
            if event.nsteps is not None:
                shift(state, event.direction, event.nsteps)
                approach.z_nm = state.z_nm
            else:
                closest = closest_known_approach(state.approaches[:-1], approach)
                if closest is not None:
                    state.z_nm = approach.z_nm = closest.z_nm
                    approach.z_assumed = True

        case Annotate():
            state.marks.append(Mark(event.seq, state.x_nm, state.y_nm,
                                    list(event.tags), event.radius_nm, event.note))

        case Crash():
            if event.move_seq not in state.move_lines:
                raise ValueError(f"seq {event.seq}: crash refers to {event.move_seq}, which is no x/y move")
            start_nm, end_nm = state.move_lines[event.move_seq]
            state.crashes.append(CrashLine(event.seq, event.move_seq, start_nm, end_nm,
                                           event.radius_nm, event.note))

        case OnSample():
            state.on_sample = event.on_sample
            if state.visits:
                state.visits[-1].on_sample = event.on_sample  # where we are now

        case _:
            raise ValueError(f"cannot replay event {event!r}")


def closest_known_approach(approaches, here):
    known = [a for a in approaches if a.z_nm is not None]
    if not known:
        return None
    return min(known, key=lambda a: (a.x_nm - here.x_nm) ** 2 + (a.y_nm - here.y_nm) ** 2)


def require_session(state, event):
    if state.step_nm is None:
        raise ValueError(f"seq {event.seq}: {event.type} before any session_start (no calibration)")


def shift(state, direction, nsteps):
    axis, sign = AXES[direction]
    distance_nm = sign * nsteps * state.step_nm[direction]
    setattr(state, f"{axis}_nm", getattr(state, f"{axis}_nm") + distance_nm)


class Timeline:
    """Step through a list of events. index = number of events applied,
    from 0 (nothing happened yet) to len(events) (now)."""

    def __init__(self, events):
        self.events = events
        self.index = len(events)

    @property
    def state(self):
        return replay(self.events[:self.index])

    def go_to(self, index):
        self.index = min(max(0, index), len(self.events))


def path_to(current, target):
    """Moves (direction, nsteps) that bring the tip from the current state's
    x/y position back to the target state's, x first, then y.

    Uses the current calibration. z is left out on purpose: getting close to
    the sample again is the job of the auto approach, not of coarse steps.
    """
    moves = []
    for axis in ("x", "y"):
        delta_nm = getattr(target, f"{axis}_nm") - getattr(current, f"{axis}_nm")
        direction = f"{axis}+" if delta_nm > 0 else f"{axis}-"
        nsteps = round(abs(delta_nm) / current.step_nm[direction])
        while nsteps > 0:  # one Motor_StartMove takes at most NSTEPS_MAX steps
            moves.append((direction, min(nsteps, NSTEPS_MAX)))
            nsteps -= NSTEPS_MAX
    return moves
