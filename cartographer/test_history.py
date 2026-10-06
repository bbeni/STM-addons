import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from config import DEFAULT_CONFIG, load_config
from history import (
    Annotate, ApproachStart, ApproachSteps, ApproachStop, Crash, Delete, HistoryError,
    HistoryFile, Move, MoveStop, OnSample, Sample, SessionStart, read_history,
)
from timeline import Timeline, arrange, path_to, replay

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
        self.assertEqual(json.loads(header)["version"], 2)
        self.assertEqual(list(json.loads(line))[:3], ["seq", "time", "type"])

    def test_reopen_continues_numbering(self):
        HistoryFile(self.path).append(SessionStart(step_nm=STEP_NM))
        event = HistoryFile(self.path).append(move("x+", 1))
        self.assertEqual(event.seq, 1)

    def test_rejects_newer_version(self):
        self.path.write_text(json.dumps({"format": "cartographer-history", "version": 3}) + "\n")
        with self.assertRaisesRegex(HistoryError, "version 3"):
            read_history(self.path)

    def test_reads_version_1(self):
        self.path.write_text(
            json.dumps({"format": "cartographer-history", "version": 1}) + "\n"
            + '{"seq": 0, "time": "2026-10-05T17:51:02+02:00", "type": "sample", "name": "Au(111)", "description": ""}\n')
        self.assertEqual(read_history(self.path)[0].name, "Au(111)")

    def test_after_seq_written_only_when_set(self):
        history = HistoryFile(self.path)
        history.append(Annotate(tags=["dirty"], radius_nm=1.0))
        history.append(Annotate(tags=["clean"], radius_nm=1.0, after_seq=0))
        _, plain, inserted = self.path.read_text().splitlines()
        self.assertNotIn("after_seq", json.loads(plain))
        self.assertEqual(list(json.loads(inserted))[:4], ["seq", "time", "type", "after_seq"])
        self.assertEqual(read_history(self.path), history.events)

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
        target.x_nm = 100.0 * (65535 + 10)  # Nanonis takes at most 65535 (uint16) steps per move
        self.assertEqual(path_to(state, target), [("x+", 65535), ("x+", 10)])

    def test_approach_done_and_on_sample(self):
        events = [SessionStart(step_nm=STEP_NM), move("x+", 10), ApproachStart(),
                  ApproachStop(), OnSample(on_sample=False)]  # found plate, then said so
        for seq, event in enumerate(events):
            event.seq = seq
        self.assertFalse(replay(events[:3]).approaches[0].done)
        approach, = replay(events).approaches
        self.assertTrue(approach.done)
        self.assertFalse(approach.on_sample)


class ArrangeTest(unittest.TestCase):
    """Deleting and inserting: the file only grows, the timeline is arranged."""

    def setUp(self):
        self.events = example_events()

    def add(self, event):
        event.seq = len(self.events)
        self.events.append(event)

    def number(self):
        for seq, event in enumerate(self.events):
            event.seq = seq

    def test_plain_history_is_unchanged(self):
        self.number()
        self.assertEqual(arrange(self.events), self.events)

    def test_delete_annotation(self):
        self.number()
        self.add(Delete(target_seq=7))
        timeline = arrange(self.events)
        self.assertNotIn(7, [e.seq for e in timeline])
        self.assertEqual(replay(timeline).marks, [])

    def test_moves_cannot_be_deleted(self):
        self.number()
        self.add(Delete(target_seq=1))
        with self.assertRaisesRegex(ValueError, "cannot be deleted"):
            arrange(self.events)

    def test_insert_annotation_in_the_past(self):
        self.number()
        self.add(Annotate(tags=["clean"], radius_nm=50.0, after_seq=1))  # right after the first move
        timeline = arrange(self.events)
        self.assertEqual([e.seq for e in timeline[:4]], [0, 1, 12, 2])
        clean = [m for m in replay(timeline).marks if m.tags == ["clean"]][0]
        self.assertEqual((clean.x_nm, clean.y_nm), (1000, 0))

    def test_insert_on_sample_in_the_past(self):
        self.number()
        self.add(Delete(target_seq=2))  # "left the sample" was pressed one move too early
        self.add(OnSample(on_sample=False, after_seq=3))
        visits = replay(arrange(self.events)).visits
        self.assertEqual([v.on_sample for v in visits], [True, True, False, False])

    def test_moves_cannot_be_inserted(self):
        self.number()
        self.add(move("x+", 1))
        self.events[-1].after_seq = 1
        with self.assertRaisesRegex(ValueError, "cannot be inserted"):
            arrange(self.events)

    def test_insert_after_deleted_event_fails(self):
        self.number()
        self.add(Delete(target_seq=7))
        self.add(Annotate(tags=["flat"], radius_nm=1.0, after_seq=7))
        with self.assertRaisesRegex(ValueError, "not in the timeline"):
            arrange(self.events)


class ConfigTest(TempDirTest):
    def test_missing_keys_take_defaults(self):
        config_path = Path(self.tmp.name) / "config.toml"
        config_path.write_text('[step_nm]\n"x+" = 1.0\n"x-" = 1.0\n"y+" = 1.0\n"y-" = 1.0\n'
                               '"z+" = 1.0\n"z-" = 1.0\n')
        config = load_config(config_path)
        self.assertEqual(config["step_nm"]["z-"], 1.0)
        self.assertEqual(config["colors"], tomllib.loads(DEFAULT_CONFIG)["colors"])
        self.assertEqual(config["approach"]["direction"], "z-")


class StartFolderTest(TempDirTest):
    def test_newest_dated_folder(self):
        from main import start_folder
        root = Path(self.tmp.name)
        for name in ("2026-09-30", "2026-10-03", "2026-10-03 old", "notes"):
            (root / name).mkdir()
        (root / "2026-12-01").write_text("a file, not a folder")
        self.assertEqual(start_folder(root), root / "2026-10-03")

    def test_falls_back_to_current_directory(self):
        from main import start_folder
        self.assertEqual(start_folder(Path(self.tmp.name) / "missing"), Path.cwd())
        self.assertEqual(start_folder(self.tmp.name), Path.cwd())

    def test_default_file_name(self):
        from main import default_file_name
        self.assertEqual(default_file_name("Au(111)", "20261006_1520"), "20261006_1520_Au_111.jsonl")


if __name__ == "__main__":
    unittest.main()
