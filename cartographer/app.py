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
Follow-up after testing on the STM (2026-10-06): the tip is a tip shape, a crash
a star; a smile for clean or flat, a frown for dirty, a star for tip shaped; an
arrow head on each move shows its direction; dots only where an approach was
done; the length scale is written out; looking back, a straight line joins
then and now; a faint blue cloud around the approaches on the sample, a grey
one around those on the sample plate; history entries can be deleted, and an
annotation made while looking back is inserted at that point.
"""

import html
import math
import time

import pyqtgraph as pg
from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt5.QtGui import (
    QBrush, QColor, QFontDatabase, QKeySequence, QPainter, QPainterPath, QPainterPathStroker, QPen,
    QPolygonF, QTransform,
)
from PyQt5.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QGraphicsEllipseItem, QGraphicsItem,
    QGraphicsPathItem, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QShortcut,
    QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from backend import NanonisError
from history import (
    DELETABLE, NSTEPS_MAX, Annotate, ApproachStart, ApproachSteps, ApproachStop, Crash, Delete,
    Move, MoveStop, OnSample, Sample, SessionStart,
)
from timeline import Timeline, arrange, path_to, replay

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
Setting amplitude and frequency in Nanonis is fine: they are read before each move.<br><br>
<b>Corrections:</b> select a wrong remark in the timeline and press <b>Delete</b>.
To add a forgotten remark, select the point in the timeline (arrow keys) and annotate:
it is inserted there. Moves always happen now."""

TAGS = ("clean", "flat", "dirty", "tip shaped")

# Map layers, bottom to top. The tip is always drawn on top of them.
LAYERS = ("sample", "annotations", "crashes", "path", "approaches", "plan", "labels")
TIP_Z = len(LAYERS)

MIN_VIEW_nm = 2000  # fitting never zooms in closer than this
ZOOM_LIMITS_nm = (1, 1e9)  # smallest and largest visible width, far from float trouble

# A move is assumed finished after nsteps / frequency, plus this margin.
MOVE_MARGIN_s = 0.3

FAILED = object()  # returned by MainWindow.act when a hardware call failed


def timestamp(event):
    return event.time[:19].replace("T", " ")  # 2026-10-05 17:27:16


def first_line(text):
    return text.strip().splitlines()[0] if text.strip() else ""


def length_text(length_nm):
    return f"{length_nm / 1000:.3g} µm" if abs(length_nm) >= 1000 else f"{length_nm:.0f} nm"


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


def translucent(color, alpha):
    qcolor = QColor(color)
    qcolor.setAlpha(alpha)
    return qcolor


# --- Shapes ------------------------------------------------------------------
# Glyphs keep their size on screen: paths in pixels, y pointing down.


def polygon_path(points):
    path = QPainterPath()
    path.addPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
    path.closeSubpath()
    return path


def tip_path():
    # an etched STM tip: a wire tapering to its apex, which is at the position (0, 0)
    path = QPainterPath(QPointF(0, 0))
    path.cubicTo(-1, -8, -6, -16, -6, -28)
    path.lineTo(-6, -44)
    path.lineTo(6, -44)
    path.lineTo(6, -28)
    path.cubicTo(6, -16, 1, -8, 0, 0)
    return path


def star_path(radius_px):
    points = []
    for k in range(10):
        r_px = radius_px if k % 2 == 0 else 0.45 * radius_px
        angle = math.pi * k / 5
        points.append((r_px * math.sin(angle), -r_px * math.cos(angle)))
    return polygon_path(points)


def face_path(radius_px, happy):
    """A filled face with eyes and mouth cut out: smiling or frowning."""
    r = radius_px
    face = QPainterPath()
    face.addEllipse(QPointF(0, 0), r, r)
    mouth = QPainterPath()
    if happy:  # the lower part of a circle around the middle of the face
        rect = QRectF(-0.55 * r, -0.5 * r, 1.1 * r, 1.1 * r)
        mouth.arcMoveTo(rect, 205)
        mouth.arcTo(rect, 205, 130)
    else:  # the upper part of a circle below the face
        rect = QRectF(-0.5 * r, 0.3 * r, r, r)
        mouth.arcMoveTo(rect, 35)
        mouth.arcTo(rect, 35, 110)
    stroker = QPainterPathStroker()
    stroker.setWidth(0.16 * r)
    stroker.setCapStyle(Qt.RoundCap)
    cut = stroker.createStroke(mouth)
    for side in (-1, 1):
        cut.addEllipse(QPointF(side * 0.36 * r, -0.3 * r), 0.14 * r, 0.14 * r)
    return face.subtracted(cut)


def arrow_path(angle_deg):
    """An arrow head pointing at angle_deg (clockwise from +x on screen)."""
    head = polygon_path([(10, 0), (-7, -8), (-3, 0), (-7, 8)])
    return QTransform().rotate(angle_deg).map(head)


def convex_hull(points):
    points = sorted(set(points))
    if len(points) <= 2:
        return points

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def half(points):
        hull = []
        for point in points:
            while len(hull) >= 2 and cross(hull[-2], hull[-1], point) <= 0:
                hull.pop()
            hull.append(point)
        return hull[:-1]

    return half(points) + half(points[::-1])


def blob(points_nm, radius_nm):
    """Everything closer than radius_nm to the convex hull of the points."""
    hull = convex_hull(points_nm)
    shape = QPainterPath()
    for x_nm, y_nm in hull:
        circle = QPainterPath()
        circle.addEllipse(QPointF(x_nm, y_nm), radius_nm, radius_nm)
        shape = shape.united(circle)
    if len(hull) >= 2:
        outline = polygon_path(hull)
        stroker = QPainterPathStroker()
        stroker.setWidth(2 * radius_nm)
        stroker.setJoinStyle(Qt.RoundJoin)
        stroker.setCapStyle(Qt.RoundCap)
        shape = shape.united(stroker.createStroke(outline)).united(outline)
    return shape


class Glyph(QGraphicsPathItem):
    """A shape of fixed screen size (path in pixels) at a map position in nm."""

    def __init__(self, path, x_nm, y_nm, color, tooltip="", outline="w"):
        super().__init__(path)
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setPos(x_nm, y_nm)
        self.setBrush(QBrush(QColor(color)))
        self.setPen(pg.mkPen(outline, width=1.2) if outline else QPen(Qt.NoPen))
        if tooltip:
            self.setToolTip(tooltip)


class ScaleBar(QWidget):
    """The length scale, written out, in the bottom right corner of the map."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.resize(260, 56)
        self.length_px = 0
        self.text = ""

    def set_scale(self, nm_per_px):
        if not (nm_per_px > 0 and math.isfinite(nm_per_px)):
            return
        # a 1-2-5 length of at most 140 pixels
        target_nm = 140 * nm_per_px
        size_nm = 10 ** math.floor(math.log10(target_nm))
        for factor in (5, 2):
            if size_nm * factor <= target_nm:
                size_nm *= factor
                break
        self.length_px = size_nm / nm_per_px
        self.text = length_text(size_nm)
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        right_px, bottom_px = self.width() - 2, self.height() - 4
        left_px = right_px - self.length_px
        painter.fillRect(QRectF(left_px, bottom_px - 6, self.length_px, 6), Qt.black)
        text_px = painter.fontMetrics().horizontalAdvance(self.text)
        x_px = min(left_px + (self.length_px - text_px) / 2, right_px - text_px)
        painter.setPen(Qt.black)
        painter.drawText(QPointF(x_px, bottom_px - 12), self.text)


class MapView(pg.PlotWidget):
    """The canvas: positions in nm, equal aspect, no axes or grid, a scale bar.
    Items are drawn in layers that can be hidden."""

    def __init__(self, config, on_approach_clicked):
        super().__init__(background="w")
        self.colors = config["colors"]
        self.cloud_radius_nm = config["map"]["cloud_radius_nm"]
        self.on_approach_clicked = on_approach_clicked
        self.setAntialiasing(True)
        self.hideAxis("left")
        self.hideAxis("bottom")
        self.hideButtons()
        self.setAspectLocked(True)
        box = self.getViewBox()
        # Fitting is done here, not by pyqtgraph: its auto range shrinks a single
        # point to a window of 0.01 nm, and zooming further ends in NaN ranges.
        box.disableAutoRange()
        box.setLimits(minXRange=ZOOM_LIMITS_nm[0], maxXRange=ZOOM_LIMITS_nm[1],
                      minYRange=ZOOM_LIMITS_nm[0], maxYRange=ZOOM_LIMITS_nm[1])
        box.sigRangeChangedManually.connect(self.stop_fitting)
        box.sigRangeChanged.connect(self.update_scale_bar)
        self.auto_fit = True  # fit after every change, until the user zooms or pans
        self.extent = []  # (x_nm, y_nm, radius_nm) that fitting keeps in view
        self.layer_items = {layer: [] for layer in LAYERS}
        self.layer_visible = {layer: True for layer in LAYERS}
        self.scale_bar = ScaleBar(self)

    def fit(self):
        """Zoom to show everything, and keep doing so until the user zooms."""
        self.auto_fit = True
        self.fit_view()

    def stop_fitting(self, *_):
        self.auto_fit = False

    def fit_view(self):
        if not self.extent:
            return
        x_min_nm = min(x - r for x, _, r in self.extent)
        x_max_nm = max(x + r for x, _, r in self.extent)
        y_min_nm = min(y - r for _, y, r in self.extent)
        y_max_nm = max(y + r for _, y, r in self.extent)
        x_mid_nm, y_mid_nm = (x_min_nm + x_max_nm) / 2, (y_min_nm + y_max_nm) / 2
        half_x_nm = max(x_max_nm - x_min_nm, MIN_VIEW_nm) / 2
        half_y_nm = max(y_max_nm - y_min_nm, MIN_VIEW_nm) / 2
        self.getViewBox().setRange(xRange=(x_mid_nm - half_x_nm, x_mid_nm + half_x_nm),
                                   yRange=(y_mid_nm - half_y_nm, y_mid_nm + half_y_nm), padding=0.12)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "scale_bar", None) is None:  # called while pyqtgraph sets up
            return
        self.scale_bar.move(self.width() - self.scale_bar.width() - 16,
                            self.height() - self.scale_bar.height() - 12)
        if self.auto_fit:
            self.fit_view()
        self.update_scale_bar()

    def update_scale_bar(self, *_):
        self.scale_bar.set_scale(self.getViewBox().viewPixelSize()[0])

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
        self.addItem(item, ignoreBounds=True)

    def draw(self, state, now=None, plan=None):
        """Draw a state. When looking at the past, `now` is the live state and
        `plan` the moves back to the past position."""
        self.clear()
        self.layer_items = {layer: [] for layer in LAYERS}
        self.extent = [(state.x_nm, state.y_nm, 0)]
        self.glyph_slots = {}  # glyphs already placed next to a point
        labels = []  # (x_nm, y_nm, text, color), placed together at the end

        self.draw_clouds(state.approaches)
        for mark in state.marks:
            self.draw_mark(mark)
            labels.append((mark.x_nm, mark.y_nm, ", ".join(mark.tags) or first_line(mark.note),
                           self.mark_color(mark.tags)))
        for crash in state.crashes:
            labels.append((*self.draw_crash(crash), "crash", self.colors["crash"]))
        self.draw_path(state.visits)
        self.draw_approaches(state.approaches)
        if now is not None:
            labels.append(self.draw_plan(state, now, plan))
            self.draw_tip(now, translucent(self.colors["tip"], 110))
            labels.append((now.x_nm, now.y_nm, "now", translucent(self.colors["tip"], 160)))
        self.draw_tip(state, QColor(self.colors["tip"]))
        labels.append((state.x_nm, state.y_nm, "tip" if now is None else "then", self.colors["tip"]))
        self.draw_labels(labels)
        if self.auto_fit:
            self.fit_view()

    def mark_color(self, tags):
        for tag in ("dirty", "tip shaped", "clean", "flat"):  # most important first
            if tag in tags:
                return self.colors[tag]
        return self.colors["other"]

    def path_color(self, on_sample):
        return self.colors["on_sample" if on_sample else "off_sample"]

    def draw_clouds(self, approaches):
        # Intent: "display the sample underneath, by generating a faint blue cloud
        # that updates its shape with every approach that is ON SAMPLE. if off
        # sample a faint light greyish tint: we are on the sample plate."
        # The cloud covers the hull of the approaches; nested copies fade it out.
        for on_sample, color in ((False, self.colors["plate_cloud"]), (True, self.colors["sample_cloud"])):
            points_nm = [(a.x_nm, a.y_nm) for a in approaches if a.done and a.on_sample == on_sample]
            if not points_nm:
                continue
            for fraction in (1.0, 0.7, 0.4):
                cloud = QGraphicsPathItem(blob(points_nm, fraction * self.cloud_radius_nm))
                cloud.setPen(QPen(Qt.NoPen))
                cloud.setBrush(QBrush(translucent(color, 22)))
                self.add("sample", cloud)

    def draw_path(self, visits):
        # dashed: between the points the tip flew over the sample, nothing was seen
        for start, end in zip(visits, visits[1:]):
            start_nm, end_nm = (start.x_nm, start.y_nm), (end.x_nm, end.y_nm)
            middle_nm = ((start.x_nm + end.x_nm) / 2, (start.y_nm + end.y_nm) / 2)
            if start.on_sample == end.on_sample:
                self.draw_segment(start_nm, end_nm, end.on_sample)
            else:  # the sample edge was crossed: assume halfway
                self.draw_segment(start_nm, middle_nm, start.on_sample)
                self.draw_segment(middle_nm, end_nm, end.on_sample)
            if start_nm != end_nm:
                # on screen y points down, so the angle of the move is mirrored
                angle_deg = math.degrees(math.atan2(-(end.y_nm - start.y_nm), end.x_nm - start.x_nm))
                self.add("path", Glyph(arrow_path(angle_deg), *middle_nm, self.path_color(end.on_sample),
                                       outline=None))
        self.extent += [(v.x_nm, v.y_nm, 0) for v in visits]

    def draw_segment(self, start_nm, end_nm, on_sample):
        self.add("path", pg.PlotCurveItem([start_nm[0], end_nm[0]], [start_nm[1], end_nm[1]],
                                          pen=pg.mkPen(self.path_color(on_sample), width=2.5, style=Qt.DashLine)))

    def draw_approaches(self, approaches):
        # Intent: "only add a dot (visually on the path) if approach is done"
        def describe_approach(x, y, data):  # pyqtgraph passes these as keywords
            approach = data
            where = "on the sample" if approach.on_sample else "off the sample"
            if approach.z_nm is None:
                return f"approach #{approach.seq} {where}: height unknown"
            assumed = " (assumed: steps unknown)" if approach.z_assumed else ""
            return f"approach #{approach.seq} {where}: surface at z = {approach.z_nm:.0f} nm{assumed}"

        done = [a for a in approaches if a.done]
        if done:
            points = pg.ScatterPlotItem(
                [a.x_nm for a in done], [a.y_nm for a in done], data=done,
                symbol="o", size=13, pen=pg.mkPen("w", width=1.5), brush=pg.mkBrush(self.colors["approach"]),
                hoverable=True, tip=describe_approach,
            )
            points.sigClicked.connect(lambda _, clicked, __: self.on_approach_clicked(clicked[0].data().seq))
            self.add("approaches", points)
        self.extent += [(a.x_nm, a.y_nm, 0) for a in done]

    def draw_mark(self, mark):
        color = self.mark_color(mark.tags)
        tooltip = "\n".join(filter(None, [", ".join(mark.tags), mark.note]))
        r_nm = mark.radius_nm
        circle = QGraphicsEllipseItem(mark.x_nm - r_nm, mark.y_nm - r_nm, 2 * r_nm, 2 * r_nm)
        circle.setPen(pg.mkPen(color, width=1.5))
        circle.setBrush(QBrush(translucent(color, 50)))
        circle.setToolTip(tooltip)
        self.add("annotations", circle)
        self.extent.append((mark.x_nm, mark.y_nm, r_nm))

        # Intent: "if flat and or clean annotate a smile. if dirty a frown. if tip shaped a star."
        glyphs = []
        if "clean" in mark.tags or "flat" in mark.tags:
            glyphs.append((face_path(12, happy=True), self.colors["clean" if "clean" in mark.tags else "flat"]))
        if "dirty" in mark.tags:
            glyphs.append((face_path(12, happy=False), self.colors["dirty"]))
        if "tip shaped" in mark.tags:
            glyphs.append((star_path(13), self.colors["tip shaped"]))
        if not glyphs:  # a note only
            dot = QPainterPath()
            dot.addEllipse(QPointF(0, 0), 5, 5)
            glyphs.append((dot, self.colors["other"]))
        # side by side, right of the point, where the tip and approach dot leave room;
        # marks made at the same point continue the row
        point = (round(mark.x_nm), round(mark.y_nm))
        first = self.glyph_slots.get(point, 0)
        self.glyph_slots[point] = first + len(glyphs)
        for k, (path, glyph_color) in enumerate(glyphs, start=first):
            self.add("annotations", Glyph(path.translated(24 + 28 * k, 0), mark.x_nm, mark.y_nm,
                                          glyph_color, tooltip))

    def draw_crash(self, crash):
        """A band of width 2 * radius around the line of the move, a star in its
        middle. Returns the middle, for the label."""
        line = QPainterPath()
        line.moveTo(*crash.start_nm)
        line.lineTo(*crash.end_nm)
        stroker = QPainterPathStroker()
        stroker.setWidth(2 * crash.radius_nm)
        stroker.setCapStyle(Qt.RoundCap)
        band = QGraphicsPathItem(stroker.createStroke(line))
        band.setPen(QPen(Qt.NoPen))
        band.setBrush(QBrush(translucent(self.colors["crash"], 60)))
        tooltip = f"crash along move #{crash.move_seq}\n{crash.note}".strip()
        band.setToolTip(tooltip)
        self.add("crashes", band)
        middle_nm = ((crash.start_nm[0] + crash.end_nm[0]) / 2, (crash.start_nm[1] + crash.end_nm[1]) / 2)
        # above the line, clear of the arrow head in the middle of the move
        self.add("crashes", Glyph(star_path(14).translated(0, -20), *middle_nm, self.colors["crash"], tooltip))
        self.extent += [(*crash.start_nm, crash.radius_nm), (*crash.end_nm, crash.radius_nm)]
        return middle_nm

    def draw_plan(self, then, now, plan):
        """The planned way back, x first and then y as path_to plans it, and the
        straight line from now to then. Returns the label of the distance."""
        pen = pg.mkPen(self.colors["plan"], width=1.5, style=Qt.DotLine)
        x_nm, y_nm = now.x_nm, now.y_nm
        for direction, nsteps in plan or []:
            sign = 1 if direction.endswith("+") else -1
            distance_nm = sign * nsteps * now.step_nm[direction]
            nx_nm, ny_nm = (x_nm + distance_nm, y_nm) if direction[0] == "x" else (x_nm, y_nm + distance_nm)
            self.add("plan", pg.PlotCurveItem([x_nm, nx_nm], [y_nm, ny_nm], pen=pen))
            x_nm, y_nm = nx_nm, ny_nm

        # Intent: "also draw the line connecting then and now directly"
        self.add("plan", pg.PlotCurveItem([now.x_nm, then.x_nm], [now.y_nm, then.y_nm],
                                          pen=pg.mkPen(self.colors["plan"], width=2, style=Qt.DashLine)))
        self.extent.append((now.x_nm, now.y_nm, 0))
        distance_nm = math.dist(now.xy_nm, then.xy_nm)
        middle_nm = ((now.x_nm + then.x_nm) / 2, (now.y_nm + then.y_nm) / 2)
        return (*middle_nm, length_text(distance_nm) if distance_nm else "", self.colors["plan"])

    def draw_tip(self, state, color):
        self.add(None, Glyph(tip_path(), state.x_nm, state.y_nm, color))

    def draw_labels(self, labels):
        # Labels at the same point are spread evenly around it, starting below.
        at_point = {}
        for x_nm, y_nm, text, color in labels:
            if text:
                at_point.setdefault((round(x_nm), round(y_nm)), []).append((x_nm, y_nm, text, color))
        font = self.font()
        font.setBold(True)
        for group in at_point.values():
            for k, (x_nm, y_nm, text, color) in enumerate(group):
                angle = -math.pi / 2 + 2 * math.pi * k / len(group)
                # the anchor is in units of the label's own size; 0.5 is centred on the point
                anchor = (0.5 - 0.7 * math.cos(angle), 0.5 + 1.3 * math.sin(angle))
                label = pg.TextItem(text, color=color, anchor=anchor)
                label.setFont(font)
                label.setPos(x_nm, y_nm)
                self.add("labels", label)


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
        self.description_edit.setFixedHeight(110)
        layout.addWidget(self.description_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
                                   accepted=self.accept, rejected=self.reject)
        layout.addWidget(buttons)
        self.name_edit.textChanged.connect(
            lambda text: buttons.button(QDialogButtonBox.Ok).setEnabled(bool(text.strip())))
        buttons.button(QDialogButtonBox.Ok).setEnabled(bool(name.strip()))
        self.resize(460, self.sizeHint().height())

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
        self.move = None  # (Move event, start time) while a move runs
        self.queue = []  # moves still to do on the way back
        self.move_timer = QTimer(self, singleShot=True, timeout=self.move_finished)
        self.approach_timer = QTimer(self, interval=500, timeout=self.poll_approach)

        simulated = backend.name == "Simulation"
        # before the timeline is made, so that it starts at now
        history.append(SessionStart(step_nm=dict(config["step_nm"]), simulated=simulated))
        self.events = arrange(history.events)  # the timeline as shown and replayed
        self.timeline = Timeline(self.events)

        self.map = MapView(config, on_approach_clicked=self.view_seq)
        self.events_list = QListWidget(currentRowChanged=lambda row: self.view(row + 1) if row >= 0 else None)
        for keys in (QKeySequence.Delete, QKeySequence(Qt.Key_Backspace)):
            QShortcut(keys, self.events_list, activated=self.delete_selected, context=Qt.WidgetShortcut)
        self.build_layout(simulated)
        self.map.scene().sigMouseMoved.connect(self.show_cursor_position)
        self.show_cursor_position(None)
        self.fill_events_list()
        self.refresh()

    # --- layout --------------------------------------------------------------

    def build_layout(self, simulated):
        left = QSplitter(Qt.Vertical)
        left.addWidget(self.map_box())
        left.addWidget(self.timeline_box())
        left.setSizes([680, 260])

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
        splitter.setSizes([1000, 420])
        self.setCentralWidget(splitter)
        self.resize(1440, 960)

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
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPointSizeF(self.font().pointSizeF() + 1)
        self.position_label.setFont(font)
        self.on_sample_label = QLabel()
        layout.addWidget(self.position_label)
        layout.addWidget(self.on_sample_label)
        return box

    def move_box(self):
        box = QGroupBox("Coarse move (one axis at a time)")
        grid = QGridLayout(box)
        self.move_buttons = {}
        # a cross for x/y, a column for z
        for direction, row, col in (("y+", 0, 1), ("x-", 1, 0), ("x+", 1, 2), ("y-", 2, 1),
                                    ("z+", 0, 3), ("z-", 2, 3)):
            button = QPushButton(direction.upper(), clicked=lambda _, d=direction: self.start_move(d))
            button.setMinimumHeight(44)
            grid.addWidget(button, row, col)
            self.move_buttons[direction] = button
        grid.addWidget(QLabel("z+ retracts", alignment=Qt.AlignCenter), 1, 3)
        self.nsteps_spin = QSpinBox(minimum=1, maximum=NSTEPS_MAX, value=self.config["defaults"]["nsteps"],
                                    suffix=" steps")
        grid.addWidget(self.nsteps_spin, 3, 0, 1, 4)
        self.stop_button = QPushButton("STOP", clicked=self.stop)
        self.stop_button.setStyleSheet("background:#d62728; color:white; font-weight:bold; padding:10px;")
        grid.addWidget(self.stop_button, 4, 0, 1, 4)
        return box

    def approach_box(self):
        box = QGroupBox("Auto approach")
        layout = QHBoxLayout(box)
        self.approach_button = QPushButton("Start approach", clicked=self.start_approach)
        self.approach_button.setMinimumHeight(40)
        layout.addWidget(self.approach_button)
        return box

    def annotate_box(self):
        self.annotate_group = QGroupBox()
        layout = QVBoxLayout(self.annotate_group)
        self.annotate_hint = QLabel(wordWrap=True)
        layout.addWidget(self.annotate_hint)
        self.on_sample_button = QPushButton(clicked=self.toggle_on_sample)
        layout.addWidget(self.on_sample_button)
        tags_row = QHBoxLayout()
        self.tag_boxes = {tag: QCheckBox(tag) for tag in TAGS}
        for checkbox in self.tag_boxes.values():
            tags_row.addWidget(checkbox)
        layout.addLayout(tags_row)
        self.note_edit = QPlainTextEdit(placeholderText="note (optional, several lines possible)")
        self.note_edit.setFixedHeight(80)
        layout.addWidget(self.note_edit)
        self.radius_spin = QDoubleSpinBox(minimum=1, maximum=1e6, decimals=0, suffix=" nm radius",
                                          value=self.config["defaults"]["radius_nm"])
        layout.addWidget(self.radius_spin)
        buttons = QHBoxLayout()
        buttons.addWidget(QPushButton("Annotate", clicked=self.annotate))
        self.crash_button = QPushButton("Crash on last x/y move", clicked=self.mark_crash)
        self.crash_button.setStyleSheet("color:#d62728;")
        buttons.addWidget(self.crash_button)
        layout.addLayout(buttons)
        return self.annotate_group

    def timeline_box(self):
        box = QGroupBox("Timeline  (arrow keys step through it; Delete removes a wrong remark)")
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        row.addWidget(QPushButton("◀", clicked=lambda: self.view(self.timeline.index - 1)))
        row.addWidget(QPushButton("▶", clicked=lambda: self.view(self.timeline.index + 1)))
        row.addWidget(QPushButton("Now", clicked=lambda: self.view(len(self.events))))
        self.viewing_label = QLabel()
        row.addWidget(self.viewing_label, stretch=1)
        self.delete_button = QPushButton("Delete", clicked=self.delete_selected)
        row.addWidget(self.delete_button)
        self.go_back_button = QPushButton("Walk back here…", clicked=self.walk_back)
        row.addWidget(self.go_back_button)
        layout.addLayout(row)
        layout.addWidget(self.events_list)
        return box

    # --- recording -----------------------------------------------------------

    def viewing_past(self):
        return self.timeline.index < len(self.events)

    def record(self, event, here=False):
        """Append an event to the history file. here=False: it happens now, and
        the view returns to now. here=True: it belongs to the point being viewed;
        looking back, it is inserted there and the view stays with it."""
        if here and self.viewing_past():
            event.after_seq = self.events[max(self.timeline.index, 1) - 1].seq
        try:
            replay(arrange(self.history.events + [event]))  # would the history still make sense?
        except ValueError as error:
            QMessageBox.warning(self, "cartographer", f"Not recorded: {error}")
            return None
        self.history.append(event)
        self.events = self.timeline.events = arrange(self.history.events)
        if event.after_seq is None:
            self.timeline.go_to(len(self.events))
        else:
            self.timeline.go_to(1 + next(i for i, e in enumerate(self.events) if e is event))
        self.fill_events_list()
        self.refresh()
        return event

    def live_state(self):
        return replay(self.events)

    def refresh(self):
        live = self.live_state()
        viewed = self.timeline.state

        if self.viewing_past():
            self.map.draw(viewed, now=live, plan=path_to(live, viewed))
            seq = self.selected_event().seq if self.timeline.index else None
            when = timestamp(self.selected_event()) if seq is not None else ""
            self.viewing_label.setText(f"<b>Looking back</b> at #{seq}  {when}")
        else:
            self.map.draw(live)
            self.viewing_label.setText("Live")

        sample = live.sample_name or "unknown sample"
        self.setWindowTitle(f"cartographer – {sample} – {self.history.path.name} – {self.backend.name}")
        description = html.escape(live.sample_description).replace("\n", "<br>")
        self.sample_label.setText(f"<span style='font-size:16pt; font-weight:bold'>{html.escape(sample)}</span>"
                                  f"<br><small>{description}</small>")
        self.position_label.setText(
            f"x = {live.x_nm:9.0f} nm\ny = {live.y_nm:9.0f} nm\nz = {live.z_nm:9.0f} nm")
        color = self.map.path_color(live.on_sample)
        self.on_sample_label.setText(f"<b style='color:{color}'>{'ON' if live.on_sample else 'OFF'} the sample</b>")

        # remarks go to the point being viewed
        if self.viewing_past():
            self.annotate_group.setTitle("Annotate the point looked back at")
            self.annotate_hint.setText("<b>Looking back:</b> a remark is inserted at the selected point "
                                       "of the timeline. Press <b>Now</b> to annotate the current position.")
        else:
            self.annotate_group.setTitle("Annotate the current position")
            self.annotate_hint.setText("")
        self.annotate_hint.setVisible(self.viewing_past())
        self.on_sample_button.setText("Last move left the sample" if viewed.on_sample
                                      else "Last move reached the sample")
        self.crash_button.setEnabled(bool(viewed.move_lines))

        busy = self.move is not None or live.approaching
        for button in self.move_buttons.values():
            button.setEnabled(not busy)
        self.approach_button.setEnabled(self.move is None)
        self.approach_button.setText("Stop approach" if live.approaching else "Start approach")
        self.go_back_button.setEnabled(self.viewing_past() and not busy)
        self.delete_button.setEnabled(isinstance(self.selected_event(), DELETABLE))

        self.events_list.blockSignals(True)
        self.events_list.setCurrentRow(self.timeline.index - 1)
        self.events_list.blockSignals(False)
        if self.events_list.currentItem() is not None:
            self.events_list.scrollToItem(self.events_list.currentItem())

    def fill_events_list(self):
        self.events_list.blockSignals(True)
        self.events_list.clear()
        for event in self.events:
            later = "   (added later)" if event.after_seq is not None else ""
            item = QListWidgetItem(f"#{event.seq:<4} {timestamp(event)}   {describe(event)}{later}")
            if not isinstance(event, DELETABLE):
                item.setToolTip("what the motors did cannot be deleted")
            self.events_list.addItem(item)
        self.events_list.blockSignals(False)

    def selected_event(self):
        """The last event of the point being viewed, or None at the very start."""
        return self.events[self.timeline.index - 1] if self.timeline.index else None

    def view(self, index):
        self.timeline.go_to(index)
        self.refresh()

    def view_seq(self, seq):
        """View the point right after event seq."""
        self.view(1 + next(i for i, e in enumerate(self.events) if e.seq == seq))

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

    # --- corrections ---------------------------------------------------------

    def delete_selected(self):
        """Intent (2026-10-06): "make it that history elements can be deleted
        (if wrongly annotated for example)". The file keeps the deleted line."""
        event = self.selected_event()
        if event is None:
            return
        if not isinstance(event, DELETABLE):
            QMessageBox.information(self, "Delete", "Only remarks can be deleted (annotations, crashes, "
                                    "on/off the sample, approach steps).\nWhat the motors did stays.")
            return
        answer = QMessageBox.question(self, "Delete", f"Delete #{event.seq}: {describe(event)}?")
        if answer != QMessageBox.Yes:
            return
        index = self.timeline.index
        if self.record(Delete(target_seq=event.seq)) is not None:
            self.view(index - 1)  # the point just before the deleted event

    # --- sample --------------------------------------------------------------

    def edit_sample(self):
        live = self.live_state()
        dialog = SampleDialog(self, live.sample_name, live.sample_description)
        if dialog.exec() == QDialog.Accepted:
            self.record(dialog.sample())

    # --- moving --------------------------------------------------------------

    def start_move(self, direction, nsteps=None):
        nsteps = nsteps or self.nsteps_spin.value()
        freq_amp = self.act(self.backend.freq_amp)
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
            f"Walk back to #{self.selected_event().seq}:\n\n{steps_text}\n\nIs the tip retracted?")
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
    # Made while looking back, they are inserted at the point being viewed.

    def toggle_on_sample(self):
        self.record(OnSample(on_sample=not self.timeline.state.on_sample), here=True)

    def annotate(self):
        tags = [tag for tag, checkbox in self.tag_boxes.items() if checkbox.isChecked()]
        note = self.note_edit.toPlainText().strip()
        if not tags and not note:
            QMessageBox.information(self, "Annotate", "Tick a tag or write a note first.")
            return
        if self.record(Annotate(tags=tags, radius_nm=self.radius_spin.value(), note=note), here=True):
            for checkbox in self.tag_boxes.values():
                checkbox.setChecked(False)
            self.note_edit.clear()

    def mark_crash(self):
        viewed = self.timeline.state
        if not viewed.move_lines:
            return
        move_seq = max(viewed.move_lines)  # the last x/y move
        if self.record(Crash(move_seq=move_seq, radius_nm=self.radius_spin.value(),
                             note=self.note_edit.toPlainText().strip()), here=True):
            self.note_edit.clear()

    def closeEvent(self, event):
        # leave nothing running that the history would not know the end of
        self.stop()
        super().closeEvent(event)
