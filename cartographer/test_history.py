import json
import tempfile
import unittest
from pathlib import Path

from history import (
    Annotate, ApproachStart, ApproachSteps, ApproachStop, Crash, HistoryError,
    HistoryFile, Move, MoveStop, OnSample, Sample, SessionStart, read_history,
)
from timeline import Timeline, path_to, replay

STEP_NM = {"x+": 100.0, "x-": 120.0, "y+": 80.0, "y-": 80.0, "z+": 50.0, "z-": 40.0}


def move(direction, nsteps):
    return Move(direction=direction, nsteps=nsteps, amplitude_V=190.0, frequency_Hz=1000.0)


def example_events():
    """A short session: walk off the sample and back on, approach, find dirt, crash."""
    return [
        SessionStart(step_nm=STEP_NM),                      # 0: starts on sample
        move("x+", 10),                                     # 1: x = 1000
        OnSample(on_sample=False),                          # 2: left during move 1
        move("y+", 5),                                      # 3: y = 400
        ApproachStart(),                                    # 4
        ApproachStop(),                                     # 5
        ApproachSteps(direction="z-", nsteps=100),          # 6: z = -4000
        Annotate(tags=["dirty"], radius_nm=100.0),          # 7
        move("z+", 20),                                     # 8: z = -3000
        move("x-", 10),                                     # 9: x = 1000 - 1200 = -200
        MoveStop(nsteps_done=5),                            # 10: x = 1000 - 600 = 400
        Crash(move_seq=9, radius_nm=200.0, note="current jumped"),  # 11
    ]


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "history.jsonl"

    def tearDown(self):
        self.tmp.cleanup()


class FileFormatTest(TempDirTest):
    def test_round_trip(self):
        history = HistoryFile(self.path)
        for event in example_events():
            history.append(event)
        self.assertEqual(read_history(self.path), history.events)

    def test_header_and_readable_lines(self):
        HistoryFile(self.path).append(move("x+", 10))
        header, line = self.path.read_text().splitlines()
        self.assertEqual(json.loads(header)["version"], 1)
        self.assertEqual(list(json.loads(line))[:3], ["seq", "time", "type"])

    def test_reopen_continues_numbering(self):
        HistoryFile(self.path).append(SessionStart(step_nm=STEP_NM))
        event = HistoryFile(self.path).append(move("x+", 1))
        self.assertEqual(event.seq, 1)

    def test_rejects_newer_version(self):
        self.path.write_text(json.dumps({"format": "cartographer-history", "version": 2}) + "\n")
        with self.assertRaisesRegex(HistoryError, "version 2"):
            read_history(self.path)

    def test_rejects_invalid_move(self):
        with self.assertRaises(ValueError):
            move("xy", 10)


class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.events = example_events()
        for seq, event in enumerate(self.events):
            event.seq = seq

    def test_final_state(self):
        state = replay(self.events)
        self.assertEqual((state.x_nm, state.y_nm, state.z_nm), (400.0, 400.0, -3000.0))
        self.assertEqual([(v.x_nm, v.y_nm) for v in state.visits],
                         [(0, 0), (1000, 0), (1000, 400), (400, 400)])
        self.assertEqual([v.on_sample for v in state.visits], [True, False, False, False])

    def test_annotation_at_position(self):
        mark, = replay(self.events).marks
        self.assertEqual((mark.x_nm, mark.y_nm, mark.tags), (1000, 400, ["dirty"]))

    def test_crash_uses_stopped_move_line(self):
        crash, = replay(self.events).crashes
        self.assertEqual((crash.start_nm, crash.end_nm), ((1000, 400), (400, 400)))

    def test_crash_must_refer_to_xy_move(self):
        self.events.append(Crash(move_seq=8, radius_nm=1.0, seq=12))  # 8 is a z move
        with self.assertRaisesRegex(ValueError, "no x/y move"):
            replay(self.events)

    def test_step_back_and_forward(self):
        timeline = Timeline(self.events)
        timeline.go_to(2)
        self.assertEqual(timeline.state.xy_nm, (1000, 0))
        self.assertTrue(timeline.state.on_sample)
        timeline.go_to(3)
        self.assertFalse(timeline.state.on_sample)
        timeline.go_to(1)
        self.assertEqual(timeline.state.xy_nm, (0, 0))

    def test_path_back_to_history_point(self):
        timeline = Timeline(self.events)
        now = timeline.state                 # (400, 400)
        timeline.go_to(2)                    # (1000, 0)
        self.assertEqual(path_to(now, timeline.state), [("x+", 6), ("y-", 5)])

    def test_sample_name(self):
        state = replay([Sample(name="Au(111)", description="sputtered 3x", seq=0)])
        self.assertEqual(state.sample_name, "Au(111)")

    def test_unknown_approach_steps_assume_closest_height(self):
        events = [
            SessionStart(step_nm=STEP_NM),
            ApproachStart(), ApproachStop(),
            ApproachSteps(direction="z-", nsteps=100),      # surface at z = -4000
            move("z+", 20),                                 # z = -3000
            move("x+", 10),
            ApproachStart(), ApproachStop(),
            ApproachSteps(direction="z-", nsteps=None),     # don't know
        ]
        for seq, event in enumerate(events):
            event.seq = seq
        state = replay(events)
        self.assertEqual(state.z_nm, -4000)
        self.assertTrue(state.approaches[-1].z_assumed)

    def test_unknown_approach_steps_without_earlier_approach(self):
        events = [SessionStart(step_nm=STEP_NM), ApproachStart(), ApproachStop(),
                  ApproachSteps(direction="z-", nsteps=None)]
        for seq, event in enumerate(events):
            event.seq = seq
        state = replay(events)
        self.assertEqual(state.z_nm, 0)
        self.assertIsNone(state.approaches[-1].z_nm)

    def test_long_path_is_split(self):
        state = replay(self.events[:1])
        target = replay(self.events[:1])
        target.x_nm = 100.0 * (2**16 + 10)
        self.assertEqual(path_to(state, target), [("x+", 2**16), ("x+", 10)])


if __name__ == "__main__":
    unittest.main()
