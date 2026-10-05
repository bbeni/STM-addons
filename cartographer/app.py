"""The cartographer window: sample, motor controls, annotations, timeline and map.

Intent (prompt, 2026-10-05): x, y, z coarse walking and approach start/stop are
done from this UI, which tracks them. The user sees on a 2D map where what
happened and all visited locations; no grid, just events drawn on a canvas.
The user annotates the surroundings (dirty, clean, flat, tip shaped, on/off
sample, crashes along a move), steps through the history and can walk back to
any earlier point. Instruct the user simply and clearly that only these buttons
may be used, so that all movement is tracked.
Follow-up: zoom to fit by default; dashed path lines (the tip flies over the
sample between points), half blue where the sample edge was crossed; ask for the
approach steps in a popup with "don't know"; spread overlapping labels around
their point; map layers that can be switched on and off; multi-line notes; the
sample as a prominent field; dates in all timestamps.
"""

import html
import math
import time

import pyqtgraph as pg
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QBrush, QColor, QFont, QPainterPath, QPainterPathStroker, QPen
from PyQt5.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QGraphicsEllipseItem, QGraphicsPathItem,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from backend import NanonisError
from history import (
    NSTEPS_MAX, Annotate, ApproachStart, ApproachSteps, ApproachStop, Crash, Move, MoveStop,
    OnSample, Sample, SessionStart,
)
from timeline import Timeline, path_to, replay

RULES = """\
<b>The map is only right if cartographer sees every movement.</b>
<ol>
<li>Move and approach <b>only with the buttons in this window</b>. Never with the
Nanonis motor panel or auto approach: those moves are lost and the map is wrong
from then on.</li>
<li>Stop with the <b>STOP</b> button of this window.</li>
<li>When an approach finishes, enter how many z steps it took.</li>
<li>Press <b>Left the sample</b> / <b>Back on the sample</b> right after the move that crossed the edge.</li>
<li>Annotate what you find (clean, dirty, ...) and how far around it applies.</li>
<li>If the tip crashed during the last x/y move, press <b>Crash on last x/y move</b>.</li>
</ol>
Setting amplitude and frequency in Nanonis is fine: they are read before each move."""

TAGS = ("clean", "flat", "dirty", "tip shaped")

COLORS = {
    "off_sample": "#9aa0a6",
    "on_sample": "#1f6fb4",
    "tip": "#d62728",
    "approach": "#e67e00",
    "crash": "#d62728",
    "plan": "#555555",
    "clean": "#2ca02c",
    "flat": "#17a2b8",
    "dirty": "#8c564b",
    "tip shaped": "#9467bd",
    "other": "#7f7f7f",
}

# Map layers, bottom to top. The tip is always drawn on top of them.
LAYERS = ("annotations", "crashes", "path", "approaches", "plan", "labels")
TIP_Z = len(LAYERS)

# A move is assumed finished after nsteps / frequency, plus this margin.
MOVE_MARGIN_s = 0.3

FAILED = object()  # returned by MainWindow.act when a hardware call failed


def timestamp(event):
    return event.time[:19].replace("T", " ")  # 2026-10-05 17:27:16


def first_line(text):
    return text.strip().splitlines()[0] if text.strip() else ""


def describe(event):
    """One line per event for the timeline list."""
    match event:
        case Sample():
            return f"sample: {event.name}"
        case SessionStart():
            return "session start" + (" (SIMULATED)" if event.simulated else "")
        case Move():
            return f"{event.direction} {event.nsteps} steps ({event.amplitude_V:g} V, {event.frequency_Hz:g} Hz)"
        case MoveStop():
            return f"stopped after {event.nsteps_done} steps"
        case ApproachStart():
            return "approach started"
        case ApproachStop():
            return "approach stopped"
        case ApproachSteps():
            steps = "unknown number of" if event.nsteps is None else event.nsteps
            return f"approach took {steps} steps ({event.direction})"
        case Annotate():
            return f"{', '.join(event.tags) or 'note'}, r={event.radius_nm:g} nm  {first_line(event.note)}"
        case Crash():
            return f"crash along #{event.move_seq}, r={event.radius_nm:g} nm  {first_line(event.note)}"
        case OnSample():
            return "back on the sample" if event.on_sample else "left the sample"
    return event.type


def mark_color(tags):
    for tag in ("dirty", "tip shaped", "clean", "flat"):  # most important first
        if tag in tags:
            return COLORS[tag]
    return COLORS["other"]


def translucent(color, alpha):
    qcolor = QColor(color)
    qcolor.setAlpha(alpha)
    return qcolor


class MapView(pg.PlotWidget):
    """The canvas: positions in nm, equal aspect, no axes or grid, a scale bar.
    Items are drawn in layers that can be hidden."""

    def __init__(self, on_visit_clicked):
        super().__init__(background="w")
        self.on_visit_clicked = on_visit_clicked
        self.hideAxis("left")
        self.hideAxis("bottom")
        self.hideButtons()
        self.setAspectLocked(True)
        self.getViewBox().setDefaultPadding(0.1)
        self.layer_items = {layer: [] for layer in LAYERS}
        self.layer_visible = {layer: True for layer in LAYERS}
        self.scale_bar = None
        self.getViewBox().sigRangeChanged.connect(self.update_scale_bar)

    def fit(self):
        """Zoom to show everything, and keep doing so until the user zooms."""
        self.getViewBox().enableAutoRange()

    def set_layer_visible(self, layer, visible):
        self.layer_visible[layer] = visible
        for item in self.layer_items[layer]:
            item.setVisible(visible)

    def add(self, layer, item):
        """Draw item in a layer; layer None (the tip) is always shown, on top."""
        if layer is None:
            item.setZValue(TIP_Z)
        else:
            item.setZValue(LAYERS.index(layer))
            item.setVisible(self.layer_visible[layer])
            self.layer_items[layer].append(item)
        self.addItem(item)

    def draw(self, state, now=None, plan=None):
        """Draw a state. When looking at the past, `now` is the live state and
        `plan` the moves back to the past position."""
        self.clear()
        self.layer_items = {layer: [] for layer in LAYERS}
        labels = []  # (x_nm, y_nm, text, color), placed together at the end

        for mark in state.marks:
            color = mark_color(mark.tags)
            self.draw_circle(mark, color)
            labels.append((mark.x_nm, mark.y_nm, ", ".join(mark.tags) or first_line(mark.note), color))
        for crash in state.crashes:
            self.draw_crash(crash)
            mid_nm = ((crash.start_nm[0] + crash.end_nm[0]) / 2, (crash.start_nm[1] + crash.end_nm[1]) / 2)
            labels.append((*mid_nm, "crash", COLORS["crash"]))
        self.draw_path(state.visits)
        self.draw_approaches(state.approaches)
        if now is not None:
            self.draw_plan(now, plan)
            self.draw_tip(now, translucent(COLORS["tip"], 90))
            labels.append((now.x_nm, now.y_nm, "now", translucent(COLORS["tip"], 140)))
        self.draw_tip(state, QColor(COLORS["tip"]))
        labels.append((state.x_nm, state.y_nm, "tip" if now is None else "then", COLORS["tip"]))
        self.draw_labels(labels)

    def draw_path(self, visits):
        # dashed: between the points the tip flew over the sample, nothing was seen
        for start, end in zip(visits, visits[1:]):
            if start.on_sample == end.on_sample:
                self.draw_segment((start.x_nm, start.y_nm), (end.x_nm, end.y_nm), end.on_sample)
            else:  # the sample edge was crossed: assume halfway
                middle_nm = ((start.x_nm + end.x_nm) / 2, (start.y_nm + end.y_nm) / 2)
                self.draw_segment((start.x_nm, start.y_nm), middle_nm, start.on_sample)
                self.draw_segment(middle_nm, (end.x_nm, end.y_nm), end.on_sample)
        points = pg.ScatterPlotItem(
            [v.x_nm for v in visits], [v.y_nm for v in visits], data=[v.seq for v in visits],
            size=7, pen=None, hoverable=True,
            brush=[pg.mkBrush(COLORS["on_sample" if v.on_sample else "off_sample"]) for v in visits],
            tip=lambda x, y, data: f"#{data}  ({x:.0f}, {y:.0f}) nm",
        )
        points.sigClicked.connect(lambda _, clicked, __: self.on_visit_clicked(clicked[0].data()))
        self.add("path", points)

    def draw_segment(self, start_nm, end_nm, on_sample):
        color = COLORS["on_sample" if on_sample else "off_sample"]
        self.add("path", pg.PlotCurveItem([start_nm[0], end_nm[0]], [start_nm[1], end_nm[1]],
                                          pen=pg.mkPen(color, width=2, style=Qt.DashLine)))

    def draw_approaches(self, approaches):
        def describe_approach(x, y, data):  # pyqtgraph passes these as keywords
            approach = data
            if approach.z_nm is None:
                return f"approach #{approach.seq}: height unknown"
            assumed = " (assumed: steps unknown)" if approach.z_assumed else ""
            return f"approach #{approach.seq}: surface at z = {approach.z_nm:.0f} nm{assumed}"

        if approaches:
            self.add("approaches", pg.ScatterPlotItem(
                [a.x_nm for a in approaches], [a.y_nm for a in approaches], data=approaches,
                symbol="d", size=13, pen=pg.mkPen("w"), brush=pg.mkBrush(COLORS["approach"]),
                hoverable=True, tip=describe_approach,
            ))

    def draw_circle(self, mark, color):
        r_nm = mark.radius_nm
        circle = QGraphicsEllipseItem(mark.x_nm - r_nm, mark.y_nm - r_nm, 2 * r_nm, 2 * r_nm)
        circle.setPen(pg.mkPen(color, width=1.5))
        circle.setBrush(QBrush(translucent(color, 50)))
        circle.setToolTip("\n".join(filter(None, [", ".join(mark.tags), mark.note])))
        self.add("annotations", circle)

    def draw_crash(self, crash):
        # a band of width 2 * radius around the line of the move
        line = QPainterPath()
        line.moveTo(*crash.start_nm)
        line.lineTo(*crash.end_nm)
        stroker = QPainterPathStroker()
        stroker.setWidth(2 * crash.radius_nm)
        stroker.setCapStyle(Qt.RoundCap)
        band = QGraphicsPathItem(stroker.createStroke(line))
        band.setPen(QPen(Qt.NoPen))
        band.setBrush(QBrush(translucent(COLORS["crash"], 60)))
        band.setToolTip(f"crash along move #{crash.move_seq}\n{crash.note}".strip())
        self.add("crashes", band)

    def draw_plan(self, now, plan):
        # the planned way back: x first, then y, as path_to plans it
        x_nm, y_nm = now.x_nm, now.y_nm
        for direction, nsteps in plan or []:
            sign = 1 if direction.endswith("+") else -1
            distance_nm = sign * nsteps * now.step_nm[direction]
            nx_nm, ny_nm = (x_nm + distance_nm, y_nm) if direction[0] == "x" else (x_nm, y_nm + distance_nm)
            self.add("plan", pg.PlotCurveItem([x_nm, nx_nm], [y_nm, ny_nm],
                                              pen=pg.mkPen(COLORS["plan"], width=1.5, style=Qt.DotLine)))
            x_nm, y_nm = nx_nm, ny_nm

    def draw_tip(self, state, color):
        self.add(None, pg.ScatterPlotItem([state.x_nm], [state.y_nm], symbol="star", size=20, tip=None,
                                           pen=pg.mkPen("w"), brush=pg.mkBrush(color)))

    def draw_labels(self, labels):
        # Labels at the same point are spread evenly around it, starting below.
        at_point = {}
        for x_nm, y_nm, text, color in labels:
            if text:
                at_point.setdefault((round(x_nm), round(y_nm)), []).append((x_nm, y_nm, text, color))
        for group in at_point.values():
            for k, (x_nm, y_nm, text, color) in enumerate(group):
                angle = -math.pi / 2 + 2 * math.pi * k / len(group)
                # the anchor is in units of the label's own size; 0.5 is centred on the point
                anchor = (0.5 - 0.7 * math.cos(angle), 0.5 + 1.3 * math.sin(angle))
                label = pg.TextItem(text, color=color, anchor=anchor)
                label.setPos(x_nm, y_nm)
                self.add("labels", label)

    def update_scale_bar(self):
        # a 1-2-5 length of about a fifth of the visible width
        (x_min_nm, x_max_nm), _ = self.getViewBox().viewRange()
        target_nm = (x_max_nm - x_min_nm) / 5
        size_nm = 1.0
        while size_nm * 10 <= target_nm:
            size_nm *= 10
        for factor in (5, 2):
            if size_nm * factor <= target_nm:
                size_nm *= factor
                break
        if self.scale_bar is not None:
            if self.scale_bar.size == size_nm:
                return
            self.scale_bar.scene().removeItem(self.scale_bar)
        self.scale_bar = pg.ScaleBar(size=size_nm, brush=pg.mkBrush("k"), pen=pg.mkPen("k"))
        self.scale_bar.text.setText(f"{size_nm / 1000:g} µm" if size_nm >= 1000 else f"{size_nm:g} nm")
        self.scale_bar.text.setColor("k")
        self.scale_bar.setParentItem(self.getViewBox())
        self.scale_bar.anchor((1, 1), (1, 1), offset=(-20, -20))


class SampleDialog(QDialog):
    """Name and description of the sample."""

    def __init__(self, parent=None, name="", description="", title="Sample"):
        super().__init__(parent, windowTitle=title)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Sample name"))
        self.name_edit = QLineEdit(name, placeholderText="e.g. Au(111) #42")
        layout.addWidget(self.name_edit)
        layout.addWidget(QLabel("Description (preparation, ...)"))
        self.description_edit = QPlainTextEdit(description)
        self.description_edit.setFixedHeight(90)
        layout.addWidget(self.description_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
                                   accepted=self.accept, rejected=self.reject)
        layout.addWidget(buttons)
        self.name_edit.textChanged.connect(
            lambda text: buttons.button(QDialogButtonBox.Ok).setEnabled(bool(text.strip())))
        buttons.button(QDialogButtonBox.Ok).setEnabled(bool(name.strip()))

    def sample(self):
        return Sample(name=self.name_edit.text().strip(),
                      description=self.description_edit.toPlainText().strip())


class ApproachStepsDialog(QDialog):
    """Asked when an approach finished: how many z steps did it take?"""

    def __init__(self, parent, direction):
        super().__init__(parent, windowTitle="Approach finished")
        self.nsteps = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"The approach finished.<br>How many <b>{direction}</b> steps did it take?"))
        self.spin = QSpinBox(minimum=0, maximum=10**7, suffix=" steps")
        layout.addWidget(self.spin)
        layout.addWidget(QLabel("<small>Don't know: the surface is assumed as high as at the "
                                "nearest earlier approach.</small>", wordWrap=True))
        buttons = QDialogButtonBox()
        buttons.addButton("Save", QDialogButtonBox.AcceptRole)
        buttons.addButton("Don't know", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.spin.setFocus()
        self.spin.selectAll()

    def accept(self):
        self.nsteps = self.spin.value()
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self, history, config, backend):
        super().__init__()
        self.history = history
        self.config = config
        self.backend = backend
        self.timeline = Timeline(history.events)
        self.move = None  # (Move event, start time) while a move runs
        self.queue = []  # moves still to do on the way back
        self.move_timer = QTimer(self, singleShot=True, timeout=self.move_finished)
        self.approach_timer = QTimer(self, interval=500, timeout=self.poll_approach)

        simulated = backend.name == "Simulation"
        history.append(SessionStart(step_nm=dict(config["step_nm"]), simulated=simulated))

        self.map = MapView(on_visit_clicked=lambda seq: self.view(seq + 1))
        self.events_list = QListWidget(currentRowChanged=lambda row: self.view(row + 1) if row >= 0 else None)
        self.build_layout(simulated)
        self.map.scene().sigMouseMoved.connect(self.show_cursor_position)
        self.show_cursor_position(None)
        self.refresh()

    # --- layout --------------------------------------------------------------

    def build_layout(self, simulated):
        left = QSplitter(Qt.Vertical)
        left.addWidget(self.map_box())
        left.addWidget(self.timeline_box())
        left.setSizes([600, 220])

        controls = QWidget()
        column = QVBoxLayout(controls)
        if simulated:
            banner = QLabel("SIMULATION – no hardware is moved")
            banner.setStyleSheet("background:#ffb000; font-weight:bold; padding:6px;")
            column.addWidget(banner)
        column.addWidget(self.sample_box())
        rules = QLabel("Move and approach <b>only with these buttons</b>, never in Nanonis. "
                       "<a href='#'>Why?</a>", wordWrap=True)
        rules.linkActivated.connect(lambda _: QMessageBox.information(self, "How to use cartographer", RULES))
        column.addWidget(rules)
        column.addWidget(self.status_box())
        column.addWidget(self.move_box())
        column.addWidget(self.approach_box())
        column.addWidget(self.annotate_box())
        column.addStretch()
        scroll = QScrollArea(widgetResizable=True)
        scroll.setWidget(controls)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(controls.sizeHint().width() + scroll.verticalScrollBar().sizeHint().width())

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(scroll)
        splitter.setSizes([900, 360])
        self.setCentralWidget(splitter)
        self.resize(1280, 860)

    def map_box(self):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("Show:"))
        for layer in LAYERS:
            checkbox = QCheckBox(layer, checked=True)
            checkbox.toggled.connect(lambda on, layer=layer: self.map.set_layer_visible(layer, on))
            row.addWidget(checkbox)
        row.addStretch()
        row.addWidget(QPushButton("Fit", clicked=self.map.fit))
        layout.addLayout(row)
        layout.addWidget(self.map)
        return box

    def sample_box(self):
        box = QGroupBox("Sample")
        layout = QHBoxLayout(box)
        self.sample_label = QLabel(wordWrap=True)
        layout.addWidget(self.sample_label, stretch=1)
        layout.addWidget(QPushButton("Edit…", clicked=self.edit_sample))
        return box

    def status_box(self):
        box = QGroupBox("Tip")
        layout = QVBoxLayout(box)
        self.position_label = QLabel()
        self.position_label.setFont(QFont("Menlo", 12))
        self.on_sample_label = QLabel()
        self.on_sample_button = QPushButton(clicked=self.toggle_on_sample)
        layout.addWidget(self.position_label)
        layout.addWidget(self.on_sample_label)
        layout.addWidget(self.on_sample_button)
        return box

    def move_box(self):
        box = QGroupBox("Coarse move (one axis at a time)")
        grid = QGridLayout(box)
        self.move_buttons = {}
        # a cross for x/y, a column for z
        for direction, row, col in (("y+", 0, 1), ("x-", 1, 0), ("x+", 1, 2), ("y-", 2, 1),
                                    ("z+", 0, 3), ("z-", 2, 3)):
            button = QPushButton(direction.upper(), clicked=lambda _, d=direction: self.start_move(d))
            button.setMinimumHeight(36)
            grid.addWidget(button, row, col)
            self.move_buttons[direction] = button
        grid.addWidget(QLabel("z+ retracts", alignment=Qt.AlignCenter), 1, 3)
        self.nsteps_spin = QSpinBox(minimum=1, maximum=NSTEPS_MAX, value=self.config["defaults"]["nsteps"],
                                    suffix=" steps")
        grid.addWidget(self.nsteps_spin, 3, 0, 1, 4)
        self.stop_button = QPushButton("STOP", clicked=self.stop)
        self.stop_button.setStyleSheet("background:#d62728; color:white; font-weight:bold; padding:8px;")
        grid.addWidget(self.stop_button, 4, 0, 1, 4)
        return box

    def approach_box(self):
        box = QGroupBox("Auto approach")
        layout = QHBoxLayout(box)
        self.approach_button = QPushButton("Start approach", clicked=self.start_approach)
        layout.addWidget(self.approach_button)
        return box

    def annotate_box(self):
        box = QGroupBox("Annotate the current position")
        layout = QVBoxLayout(box)
        tags_row = QHBoxLayout()
        self.tag_boxes = {tag: QCheckBox(tag) for tag in TAGS}
        for checkbox in self.tag_boxes.values():
            tags_row.addWidget(checkbox)
        layout.addLayout(tags_row)
        self.note_edit = QPlainTextEdit(placeholderText="note (optional, several lines possible)")
        self.note_edit.setFixedHeight(70)
        layout.addWidget(self.note_edit)
        self.radius_spin = QDoubleSpinBox(minimum=1, maximum=1e6, decimals=0, suffix=" nm radius",
                                          value=self.config["defaults"]["radius_nm"])
        layout.addWidget(self.radius_spin)
        buttons = QHBoxLayout()
        buttons.addWidget(QPushButton("Annotate here", clicked=self.annotate))
        self.crash_button = QPushButton("Crash on last x/y move", clicked=self.mark_crash)
        self.crash_button.setStyleSheet("color:#d62728;")
        buttons.addWidget(self.crash_button)
        layout.addLayout(buttons)
        return box

    def timeline_box(self):
        box = QGroupBox("Timeline")
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        row.addWidget(QPushButton("◀", clicked=lambda: self.view(self.timeline.index - 1)))
        row.addWidget(QPushButton("▶", clicked=lambda: self.view(self.timeline.index + 1)))
        row.addWidget(QPushButton("Now", clicked=lambda: self.view(len(self.history.events))))
        self.viewing_label = QLabel()
        row.addWidget(self.viewing_label, stretch=1)
        self.go_back_button = QPushButton("Walk back here…", clicked=self.walk_back)
        row.addWidget(self.go_back_button)
        layout.addLayout(row)
        layout.addWidget(self.events_list)
        return box

    # --- recording -----------------------------------------------------------

    def record(self, event):
        """Append an event to the history file and show the live state."""
        self.history.append(event)
        self.timeline.go_to(len(self.history.events))
        self.refresh()
        return event

    def live_state(self):
        return replay(self.history.events)

    def refresh(self):
        live = self.live_state()
        viewing_past = self.timeline.index < len(self.history.events)

        if viewing_past:
            then = self.timeline.state
            self.map.draw(then, now=live, plan=path_to(live, then))
            seq = self.timeline.index - 1
            when = timestamp(self.history.events[seq]) if seq >= 0 else ""
            self.viewing_label.setText(f"<b>Looking back</b> at #{seq}  {when}")
        else:
            self.map.draw(live)
            self.viewing_label.setText("Live")

        sample = live.sample_name or "unknown sample"
        self.setWindowTitle(f"cartographer – {sample} – {self.history.path.name} – {self.backend.name}")
        description = html.escape(live.sample_description).replace("\n", "<br>")
        self.sample_label.setText(f"<span style='font-size:15pt; font-weight:bold'>{html.escape(sample)}</span>"
                                  f"<br><small>{description}</small>")
        self.position_label.setText(
            f"x = {live.x_nm:9.0f} nm\ny = {live.y_nm:9.0f} nm\nz = {live.z_nm:9.0f} nm")
        color = COLORS["on_sample" if live.on_sample else "off_sample"]
        self.on_sample_label.setText(f"<b style='color:{color}'>{'ON' if live.on_sample else 'OFF'} the sample</b>")
        self.on_sample_button.setText("Last move left the sample" if live.on_sample
                                      else "Last move reached the sample")

        busy = self.move is not None or live.approaching
        for button in self.move_buttons.values():
            button.setEnabled(not busy)
        self.approach_button.setEnabled(self.move is None)
        self.approach_button.setText("Stop approach" if live.approaching else "Start approach")
        self.crash_button.setEnabled(bool(live.move_lines))
        self.go_back_button.setEnabled(viewing_past and not busy)

        self.update_events_list()

    def update_events_list(self):
        events = self.history.events
        while self.events_list.count() < len(events):
            event = events[self.events_list.count()]
            self.events_list.addItem(f"#{event.seq:<4} {timestamp(event)}   {describe(event)}")
        self.events_list.blockSignals(True)
        self.events_list.setCurrentRow(self.timeline.index - 1)
        self.events_list.blockSignals(False)
        self.events_list.scrollToItem(self.events_list.currentItem())

    def view(self, index):
        self.timeline.go_to(index)
        self.refresh()

    def show_cursor_position(self, scene_position):
        message = f"{self.backend.name}  ·  {self.history.path}"
        if scene_position is not None:
            point = self.map.getViewBox().mapSceneToView(scene_position)
            message += f"  ·  cursor ({point.x():.0f}, {point.y():.0f}) nm"
        self.statusBar().showMessage(message)

    def act(self, action, *args):
        """Run a hardware call. On a Nanonis error, show it and return FAILED."""
        try:
            return action(*args)
        except (NanonisError, OSError) as error:
            QMessageBox.critical(self, "Nanonis", str(error))
            return FAILED

    # --- sample --------------------------------------------------------------

    def edit_sample(self):
        live = self.live_state()
        dialog = SampleDialog(self, live.sample_name, live.sample_description)
        if dialog.exec() == QDialog.Accepted:
            self.record(dialog.sample())

    # --- moving --------------------------------------------------------------

    def start_move(self, direction, nsteps=None):
        nsteps = nsteps or self.nsteps_spin.value()
        freq_amp = self.act(self.backend.freq_amp, direction)
        if freq_amp is FAILED or self.act(self.backend.start_move, direction, nsteps) is FAILED:
            self.queue.clear()
            return
        frequency_Hz, amplitude_V = freq_amp
        self.move = (Move(direction=direction, nsteps=nsteps, amplitude_V=amplitude_V,
                          frequency_Hz=frequency_Hz), time.monotonic())
        self.record(self.move[0])
        self.move_timer.start(int(1000 * (nsteps / frequency_Hz + MOVE_MARGIN_s)))

    def move_finished(self):
        self.move = None
        self.refresh()
        if self.queue:
            self.start_move(*self.queue.pop(0))

    def stop(self):
        """STOP stops everything: the running move, the way back, the approach."""
        self.queue.clear()
        if self.move is not None:
            self.move_timer.stop()
            self.act(self.backend.stop_move)
            move, start_s = self.move
            self.move = None
            elapsed_s = time.monotonic() - start_s
            self.record(MoveStop(nsteps_done=min(move.nsteps, round(elapsed_s * move.frequency_Hz))))
        if self.live_state().approaching:
            self.stop_approach()

    def walk_back(self):
        live, then = self.live_state(), self.timeline.state
        plan = path_to(live, then)
        if not plan:
            QMessageBox.information(self, "Walk back", "Already there.")
            return
        steps_text = ", then ".join(f"{d.upper()} {n} steps" for d, n in plan)
        answer = QMessageBox.question(
            self, "Walk back",
            f"Walk back to #{self.timeline.index - 1}:\n\n{steps_text}\n\nIs the tip retracted?")
        if answer == QMessageBox.Yes:
            self.queue = list(plan)
            self.start_move(*self.queue.pop(0))

    # --- approach ------------------------------------------------------------

    def start_approach(self):
        if self.live_state().approaching:
            self.stop_approach()
            return
        if self.act(self.backend.set_approach, True) is not FAILED:
            self.record(ApproachStart())
            self.approach_timer.start()

    def stop_approach(self):
        self.approach_timer.stop()
        self.act(self.backend.set_approach, False)
        self.approach_finished()

    def poll_approach(self):
        try:
            running = self.backend.approach_running()
        except (NanonisError, OSError):
            return  # try again at the next poll
        if not running:
            self.approach_timer.stop()
            self.approach_finished()

    def approach_finished(self):
        self.record(ApproachStop())
        direction = self.config["approach"]["direction"]
        nsteps = self.ask_approach_steps(direction)
        self.record(ApproachSteps(direction=direction, nsteps=nsteps))

    def ask_approach_steps(self, direction):
        """Number of steps, or None for "don't know" (also when the popup is closed)."""
        dialog = ApproachStepsDialog(self, direction)
        dialog.exec()
        return dialog.nsteps

    # --- annotations ---------------------------------------------------------

    def toggle_on_sample(self):
        self.record(OnSample(on_sample=not self.live_state().on_sample))

    def annotate(self):
        tags = [tag for tag, checkbox in self.tag_boxes.items() if checkbox.isChecked()]
        note = self.note_edit.toPlainText().strip()
        if not tags and not note:
            QMessageBox.information(self, "Annotate", "Tick a tag or write a note first.")
            return
        self.record(Annotate(tags=tags, radius_nm=self.radius_spin.value(), note=note))
        for checkbox in self.tag_boxes.values():
            checkbox.setChecked(False)
        self.note_edit.clear()

    def mark_crash(self):
        live = self.live_state()
        if not live.move_lines:
            return
        move_seq = max(live.move_lines)  # the last x/y move
        self.record(Crash(move_seq=move_seq, radius_nm=self.radius_spin.value(),
                          note=self.note_edit.toPlainText().strip()))
        self.note_edit.clear()

    def closeEvent(self, event):
        # leave nothing running that the history would not know the end of
        self.stop()
        super().closeEvent(event)
