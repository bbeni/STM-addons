"""Start the cartographer: choose the hardware and the history file, open the window.

Intent (prompt, 2026-10-05): the sample is in focus. A new history gets a
default sample name with date and time; the file is stored where the user
selects, starting next to the launcher, and no folder is created.
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

from PyQt5.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from app import MainWindow, SampleDialog
from backend import Nanonis, Simulator
from config import CONFIG_PATH, load_config
from history import HistoryFile

LAUNCHER_DIR = Path(__file__).resolve().parent.parent
FILE_FILTER = "cartographer history (*.jsonl)"


def default_file_name(sample_name, stamp):
    """Au(111) #42 -> 2026-10-05_17-52_Au_111_42.jsonl; the date is added only once."""
    slug = re.sub(r"[^A-Za-z0-9-]+", "_", sample_name).strip("_")
    return f"{slug if stamp in slug else f'{stamp}_{slug}'}.jsonl"


def create_history(path=None):
    """Ask for the sample and where to store the history, then create it."""
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    dialog = SampleDialog(name=f"sample_{stamp}", title="New sample")
    if dialog.exec() != QDialog.Accepted:
        return None
    sample = dialog.sample()

    if path is None:
        path, _ = QFileDialog.getSaveFileName(
            None, "Store the new history", str(LAUNCHER_DIR / default_file_name(sample.name, stamp)),
            FILE_FILTER, options=QFileDialog.DontConfirmOverwrite)
        if not path:
            return None
    path = Path(path)
    if path.suffix != ".jsonl":
        path = path.with_name(path.name + ".jsonl")
    if path.exists():
        QMessageBox.warning(None, "cartographer", f"{path.name} exists already.\n"
                            "Open it with “Open existing…”, or choose another name.")
        return None

    history = HistoryFile(path)
    history.append(sample)
    return history


def open_or_create_history(path_argument):
    if path_argument:
        path = Path(path_argument)
        return HistoryFile(path) if path.exists() else create_history(path)

    question = QMessageBox(QMessageBox.Question, "cartographer",
                           "Start a new sample, or continue an existing history?")
    new_button = question.addButton("New sample…", QMessageBox.AcceptRole)
    open_button = question.addButton("Open existing…", QMessageBox.ActionRole)
    question.addButton(QMessageBox.Cancel)
    question.exec()
    if question.clickedButton() == new_button:
        return create_history()
    if question.clickedButton() == open_button:
        path, _ = QFileDialog.getOpenFileName(None, "Open a history", str(LAUNCHER_DIR), FILE_FILTER)
        return HistoryFile(path) if path else None
    return None


def connect(config, simulate):
    if simulate:
        return Simulator()
    host, port = config["nanonis"]["host"], config["nanonis"]["port"]
    try:
        return Nanonis(host, port)
    except OSError as error:
        answer = QMessageBox.question(
            None, "Nanonis not reachable",
            f"Could not connect to Nanonis at {host}:{port}\n({error}).\n\n"
            "Start in simulation mode instead? No hardware will be moved.")
        return Simulator() if answer == QMessageBox.Yes else None


def main(argv, prog="cartographer"):
    parser = argparse.ArgumentParser(prog=prog, description="Track the coarse moves of the STM tip on a map.")
    parser.add_argument("history", nargs="?", help="history file (.jsonl) to open or create")
    parser.add_argument("--simulate", action="store_true", help="run without Nanonis, nothing moves")
    args = parser.parse_args(argv)

    application = QApplication(sys.argv[:1])
    config = load_config()
    print(f"config: {CONFIG_PATH}")

    backend = connect(config, args.simulate)
    if backend is None:
        return
    history = open_or_create_history(args.history)
    if history is None:
        return

    window = MainWindow(history, config, backend)
    window.show()
    application.exec()
