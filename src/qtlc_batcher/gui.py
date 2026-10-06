"""PyQt5 batch GUI: queue of plate photos, editable lane/band rectangles, live integrals and calibration.

Workflow: open a folder -> "Process all" (automatic rotation, lanes, bands, integration) -> click each plate,
check the overlay (rectangles + red = integrated pixels) and the flags, drag/resize rectangles where needed
(the plate recomputes) -> export one Excel workbook.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

from . import imaging
from .layout import Layout
from .pipeline import Params, PlateResult, analyse_plate
from .platemap import load_image_plate_map, load_platemap
from .results import STANDARD_CONC, export_workbook, plate_tables, quality_flags

BAND_COLORS = [QtGui.QColor(255, 140, 0), QtGui.QColor(40, 190, 70), QtGui.QColor(30, 150, 255)]
LANE_HEADERS = ["S500", "S125", "S31.25"] + [f"{i}" for i in range(1, 7)]
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


@dataclass
class Entry:
    path: Path
    plate: str = ""
    res: PlateResult | None = None
    layout: Layout | None = None          # user-edited layout overrides detection when set
    turns: int | None = None              # rotation actually applied (None until first analysis)
    error: str = ""
    flags: list = field(default_factory=list)
    cal: object = None
    df: object = None
    edited: bool = False
    reviewed: bool = False

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        if self.res is None:
            return "pending"
        return ("reviewed" if self.reviewed else "flags" if self.flags else "ok") + (" *" if self.edited else "")


class AnalysisWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(int, int, str)
    finished_one = QtCore.pyqtSignal(int, object, str)    # row, PlateResult | None, error text

    def __init__(self, jobs, params: Params):
        super().__init__()
        self.jobs, self.params = jobs, params            # jobs: [(row, path, layout, turns)]
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        for i, (row, path, layout, turns) in enumerate(self.jobs):
            if self._stop:
                break
            self.progress.emit(i, len(self.jobs), Path(path).name)
            try:
                p = replace(self.params, rotate=None if turns is None else 90 * turns)
                self.finished_one.emit(row, analyse_plate(path, p, layout), "")
            except Exception as e:                       # keep the batch going
                self.finished_one.emit(row, None, str(e))
        self.progress.emit(len(self.jobs), len(self.jobs), "")


class RectItem(QtWidgets.QGraphicsRectItem):
    """A band rectangle that can be moved and resized from its edges; reports the new scene rect on release."""
    TOL = 9.0

    def __init__(self, rect, lane, band, color, on_changed):
        super().__init__(rect)
        self.lane, self.band, self.on_changed = lane, band, on_changed
        pen = QtGui.QPen(color, 2); pen.setCosmetic(True)
        self.setPen(pen)
        self.setFlags(self.ItemIsSelectable | self.ItemIsMovable)
        self.setAcceptHoverEvents(True)
        self._edges = ""

    def _hit(self, pos):
        r, s = self.rect(), self._scale()
        tol = self.TOL / s
        e = ""
        if abs(pos.x() - r.left()) < tol: e += "l"
        elif abs(pos.x() - r.right()) < tol: e += "r"
        if abs(pos.y() - r.top()) < tol: e += "t"
        elif abs(pos.y() - r.bottom()) < tol: e += "b"
        return e

    def _scale(self):
        views = self.scene().views() if self.scene() else []
        return views[0].transform().m11() if views else 1.0

    def hoverMoveEvent(self, ev):
        e = self._hit(ev.pos())
        cursors = {"l": QtCore.Qt.SizeHorCursor, "r": QtCore.Qt.SizeHorCursor, "t": QtCore.Qt.SizeVerCursor,
                   "b": QtCore.Qt.SizeVerCursor, "lt": QtCore.Qt.SizeFDiagCursor, "rb": QtCore.Qt.SizeFDiagCursor,
                   "rt": QtCore.Qt.SizeBDiagCursor, "lb": QtCore.Qt.SizeBDiagCursor}
        self.setCursor(cursors.get(e, QtCore.Qt.SizeAllCursor))
        super().hoverMoveEvent(ev)

    def mousePressEvent(self, ev):
        self._edges = self._hit(ev.pos())
        if not self._edges:
            super().mousePressEvent(ev)
        else:
            ev.accept()

    def mouseMoveEvent(self, ev):
        if not self._edges:
            return super().mouseMoveEvent(ev)
        r, p = QtCore.QRectF(self.rect()), ev.pos()
        if "l" in self._edges: r.setLeft(min(p.x(), r.right() - 3))
        if "r" in self._edges: r.setRight(max(p.x(), r.left() + 3))
        if "t" in self._edges: r.setTop(min(p.y(), r.bottom() - 3))
        if "b" in self._edges: r.setBottom(max(p.y(), r.top() + 3))
        self.setRect(r)

    def mouseReleaseEvent(self, ev):
        moved = self.pos() != QtCore.QPointF(0, 0) or bool(self._edges)
        if self.pos() != QtCore.QPointF(0, 0):
            self.setRect(self.rect().translated(self.pos())); self.setPos(0, 0)
        self._edges = ""
        super().mouseReleaseEvent(ev)
        if moved:
            self.on_changed(self.lane, self.band, self.rect())


class ImageView(QtWidgets.QGraphicsView):
    rectChanged = QtCore.pyqtSignal(int, int, float, float, float, float)

    def __init__(self):
        super().__init__()
        self.setScene(QtWidgets.QGraphicsScene(self))
        self.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.SmoothPixmapTransform)
        self.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QtGui.QColor(35, 35, 38))
        self._items = []

    def clear_all(self):
        self.scene().clear(); self._items = []

    def show_entry(self, rgb: np.ndarray, res: PlateResult | None, show_mask: bool, show_rects: bool, processed: bool):
        self.clear_all()
        h, w = rgb.shape[:2]
        if processed and res is not None:
            x0, y0, x1, y1 = res.crop
            gray = np.zeros((h, w), np.uint8)
            gray[y0:y1, x0:x1] = np.clip(res.processed.astype(float) * 2.0, 0, 255).astype(np.uint8)
            img = QtGui.QImage(gray.data, w, h, w, QtGui.QImage.Format_Grayscale8).copy()
        else:
            img = QtGui.QImage(np.ascontiguousarray(rgb).data, w, h, 3 * w, QtGui.QImage.Format_RGB888).copy()
        self.scene().addPixmap(QtGui.QPixmap.fromImage(img))
        self.scene().setSceneRect(0, 0, w, h)
        if res is None:
            return
        if show_mask:
            x0, y0, x1, y1 = res.crop
            ov = np.zeros((y1 - y0, x1 - x0, 4), np.uint8)
            for (k, b), m in res.masks.items():
                xa, _, ya, _ = res.layout.rects[k, b]
                ix, iy = int(xa - x0), int(ya - y0)
                if ix < 0 or iy < 0 or m.size == 0:
                    continue
                sub = ov[iy:iy + m.shape[0], ix:ix + m.shape[1]]
                sub[m[:sub.shape[0], :sub.shape[1]]] = (255, 0, 0, 150)
            qi = QtGui.QImage(ov.data, ov.shape[1], ov.shape[0], 4 * ov.shape[1], QtGui.QImage.Format_RGBA8888).copy()
            item = self.scene().addPixmap(QtGui.QPixmap.fromImage(qi)); item.setPos(x0, y0)
        if show_rects:
            font = QtGui.QFont(); font.setPointSize(11)
            for k in range(res.layout.n_lanes):
                for b in range(res.layout.rects.shape[1]):
                    xa, xb, ya, yb = res.layout.rects[k, b]
                    it = RectItem(QtCore.QRectF(xa, ya, xb - xa, yb - ya), k, b, BAND_COLORS[b % 3], self._changed)
                    self.scene().addItem(it); self._items.append(it)
                xa, xb, ya, _ = res.layout.rects[k, 0]
                t = self.scene().addText(LANE_HEADERS[k] if k < len(LANE_HEADERS) else f"L{k + 1}", font)
                t.setDefaultTextColor(QtGui.QColor(255, 255, 0)); t.setPos(xa, ya - 55); t.setScale(2.0)

    def _changed(self, lane, band, rect: QtCore.QRectF):
        self.rectChanged.emit(lane, band, rect.left(), rect.right(), rect.top(), rect.bottom())

    def fit_layout(self, res: PlateResult | None):
        if res is None:
            self.fitInView(self.scene().sceneRect(), QtCore.Qt.KeepAspectRatio); return
        x0, y0, x1, y1 = res.crop
        self.fitInView(QtCore.QRectF(x0, y0 - 60, x1 - x0, y1 - y0 + 60), QtCore.Qt.KeepAspectRatio)

    def wheelEvent(self, ev):
        self.scale(1.25 if ev.angleDelta().y() > 0 else 0.8, 1.25 if ev.angleDelta().y() > 0 else 0.8)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("qTLC batcher")
        self.resize(1500, 900)
        self.entries: list[Entry] = []
        self.platemap = None
        self.worker: AnalysisWorker | None = None
        self._rgb_cache: tuple[str, int, np.ndarray] | None = None
        self._build_ui()

    # ---------- UI ----------
    def _build_ui(self):
        tb = self.addToolBar("main"); tb.setMovable(False)
        def act(text, slot, tip=""):
            a = QtWidgets.QAction(text, self); a.triggered.connect(slot); a.setToolTip(tip); tb.addAction(a); return a
        act("Open folder…", self.open_folder, "Load a folder of plate photos")
        act("Plate map…", self.load_platemap_dialog, "Workbook with the TLC sheet (plant, MeOH, flower mass)")
        act("Image→plate…", self.load_image_plates, "CSV/Excel with columns image,plate")
        tb.addSeparator()
        act("Process all", self.process_all, "Detect and integrate every pending plate")
        act("Re-process all", self.reprocess_all, "Recompute every plate with the CURRENT parameters (keeps rectangles)")
        act("Recompute", self.recompute_current, "Re-integrate this plate with the current rectangles/parameters")
        act("Re-detect layout", self.redetect_current, "Discard manual rectangle edits for this plate")
        act("Mark reviewed", self.mark_reviewed)
        tb.addSeparator()
        act("Export Excel…", self.export_excel)
        act("Save session…", self.save_session); act("Load session…", self.load_session)

        self.list = QtWidgets.QTableWidget(0, 3)
        self.list.setHorizontalHeaderLabels(["Image", "Plate", "Status"])
        self.list.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.list.horizontalHeader().setStretchLastSection(True)
        self.list.currentCellChanged.connect(lambda r, *_: self.select_row(r))
        self.list.itemChanged.connect(self._plate_edited)
        dock = QtWidgets.QDockWidget("Plates", self); dock.setWidget(self.list); dock.setFeatures(dock.NoDockWidgetFeatures)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock); dock.setMinimumWidth(330)

        self.view = ImageView(); self.view.rectChanged.connect(self.rect_edited)
        center = QtWidgets.QWidget(); lay = QtWidgets.QVBoxLayout(center); lay.setContentsMargins(0, 0, 0, 0)
        opts = QtWidgets.QHBoxLayout()
        self.cb_rects = QtWidgets.QCheckBox("Rectangles"); self.cb_rects.setChecked(True)
        self.cb_mask = QtWidgets.QCheckBox("Integrated pixels (red)"); self.cb_mask.setChecked(True)
        self.cb_proc = QtWidgets.QCheckBox("Show processed (inverted + background-corrected)")
        for cb in (self.cb_rects, self.cb_mask, self.cb_proc):
            cb.toggled.connect(self.refresh_view); opts.addWidget(cb)
        btn_fit = QtWidgets.QPushButton("Fit"); btn_fit.clicked.connect(lambda: self.view.fit_layout(self.current_res()))
        opts.addStretch(1); opts.addWidget(btn_fit)
        lay.addLayout(opts); lay.addWidget(self.view, 1)
        self.setCentralWidget(center)

        side = QtWidgets.QWidget(); sl = QtWidgets.QVBoxLayout(side)
        form = QtWidgets.QFormLayout()
        self.sp_prom = QtWidgets.QDoubleSpinBox(); self.sp_prom.setRange(0, 100); self.sp_prom.setSingleStep(0.5); self.sp_prom.setValue(5.0)
        self.sp_div = QtWidgets.QDoubleSpinBox(); self.sp_div.setRange(1, 10); self.sp_div.setSingleStep(0.5); self.sp_div.setValue(2.0)
        self.sp_ker = QtWidgets.QSpinBox(); self.sp_ker.setRange(0, 400); self.sp_ker.setSpecialValueText("auto")
        self.cb_wrap = QtWidgets.QCheckBox("Reproduce web-app uint8 overflow (parity)"); self.cb_wrap.setChecked(True)
        self.co_rot = QtWidgets.QComboBox(); self.co_rot.addItems(["Auto", "0°", "90°", "180°", "270°"])
        form.addRow("MinPeakProm", self.sp_prom); form.addRow("Divisor", self.sp_div)
        form.addRow("Background kernel (px)", self.sp_ker); form.addRow("Rotation (CCW)", self.co_rot)
        sl.addLayout(form); sl.addWidget(self.cb_wrap)
        self.sp_prom.valueChanged.connect(self._params_changed); self.sp_div.valueChanged.connect(self._params_changed)
        self.sp_ker.valueChanged.connect(self._params_changed); self.cb_wrap.toggled.connect(self._params_changed)
        self.co_rot.currentIndexChanged.connect(self._rotation_changed)

        sl.addWidget(QtWidgets.QLabel("<b>Integrated intensity</b> (rows = bands, top to bottom)"))
        self.tbl = QtWidgets.QTableWidget(3, 9); self.tbl.setHorizontalHeaderLabels(LANE_HEADERS)
        self.tbl.setVerticalHeaderLabels(["Band 1", "Band 2", "Band 3"]); self.tbl.setMaximumHeight(130)
        sl.addWidget(self.tbl)
        sl.addWidget(QtWidgets.QLabel("<b>Calibrated samples</b> (units of the standards)"))
        self.tbl_cal = QtWidgets.QTableWidget(3, 6); self.tbl_cal.setHorizontalHeaderLabels([f"{i}" for i in range(1, 7)])
        self.tbl_cal.setVerticalHeaderLabels(["Band 1", "Band 2", "Band 3"]); self.tbl_cal.setMaximumHeight(130)
        sl.addWidget(self.tbl_cal)
        self.lbl_r2 = QtWidgets.QLabel(""); sl.addWidget(self.lbl_r2)
        sl.addWidget(QtWidgets.QLabel("<b>Flags</b>"))
        self.txt_flags = QtWidgets.QPlainTextEdit(); self.txt_flags.setReadOnly(True); sl.addWidget(self.txt_flags, 1)
        d2 = QtWidgets.QDockWidget("Parameters & results", self); d2.setWidget(side); d2.setFeatures(d2.NoDockWidgetFeatures)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, d2); d2.setMinimumWidth(520)

        self.progress = QtWidgets.QProgressBar(); self.progress.setMaximumWidth(260); self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().showMessage("Open a folder of plate photos to begin.")

    # ---------- state helpers ----------
    def params(self) -> Params:
        rot = [None, 0, 90, 180, 270][self.co_rot.currentIndex()]
        return Params(min_peak_prom=self.sp_prom.value(), divisor=self.sp_div.value(), rotate=rot,
                      bg_kernel=self.sp_ker.value() or None, uint8_wrap=self.cb_wrap.isChecked())

    def cur(self) -> Entry | None:
        r = self.list.currentRow()
        return self.entries[r] if 0 <= r < len(self.entries) else None

    def current_res(self):
        e = self.cur(); return e.res if e else None

    def _row_of(self, e: Entry) -> int:
        return self.entries.index(e)

    def _refresh_row(self, row: int):
        e = self.entries[row]
        self.list.blockSignals(True)
        self.list.item(row, 0).setText(e.path.name); self.list.item(row, 1).setText(e.plate)
        it = self.list.item(row, 2); it.setText(e.status)
        it.setForeground({"error": QtGui.QColor("#d33"), "flags": QtGui.QColor("#c80"), "pending": QtGui.QColor("#888")}
                         .get(e.status.split()[0], QtGui.QColor("#2a2")))
        self.list.blockSignals(False)

    # ---------- loading ----------
    def open_folder(self, folder=None):
        folder = folder or QtWidgets.QFileDialog.getExistingDirectory(self, "Folder with plate photos")
        if not folder:
            return
        self.load_folder(Path(folder))

    def load_folder(self, folder: Path):
        files = sorted(p for p in folder.iterdir() if p.suffix.lower() in PHOTO_EXT)
        self.entries = [Entry(p) for p in files]
        self.list.blockSignals(True); self.list.setRowCount(len(self.entries))
        for r, e in enumerate(self.entries):
            for c in range(3):
                it = QtWidgets.QTableWidgetItem(); it.setFlags(it.flags() & ~QtCore.Qt.ItemIsEditable if c != 1 else it.flags())
                self.list.setItem(r, c, it)
        self.list.blockSignals(False)
        for r in range(len(self.entries)):
            self._refresh_row(r)
        self.statusBar().showMessage(f"{len(self.entries)} photos in {folder}")

    def load_platemap_dialog(self):
        f, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Workbook with the TLC sheet", "", "Excel (*.xlsx)")
        if f:
            self.set_platemap(f)

    def set_platemap(self, path):
        self.platemap = load_platemap(path)
        self.statusBar().showMessage(f"plate map: {len(self.platemap)} lanes, {self.platemap.plate.nunique()} plates")
        for e in self.entries:
            if e.res is not None:
                self._rebuild_tables(e)

    def load_image_plates(self):
        f, _ = QtWidgets.QFileDialog.getOpenFileName(self, "CSV/Excel with columns image,plate", "", "Tables (*.csv *.xlsx)")
        if f:
            self.set_image_plates(f)

    def set_image_plates(self, path):
        m = load_image_plate_map(path)
        for r, e in enumerate(self.entries):
            e.plate = m.get(e.path.name) or m.get(e.path.stem) or m.get(e.path.stem.replace("IMG_", "")) or e.plate
            self._refresh_row(r)

    def _plate_edited(self, item):
        if item.column() == 1 and 0 <= item.row() < len(self.entries):
            e = self.entries[item.row()]; e.plate = item.text().strip()
            if e.res is not None:
                self._rebuild_tables(e)

    # ---------- processing ----------
    def process_all(self):
        jobs = [(r, str(e.path), e.layout, e.turns) for r, e in enumerate(self.entries) if e.res is None and not e.error]
        self._run_jobs(jobs)

    def reprocess_all(self):
        """Recompute every plate with the current parameters, keeping rotation and (edited or detected) rectangles."""
        jobs = [(r, str(e.path), e.layout or (e.res.layout if e.res else None), e.turns)
                for r, e in enumerate(self.entries) if e.res is not None or not e.error]
        self._run_jobs(jobs)

    def params_mismatch(self) -> list[str]:
        """Names of plates computed with parameters different from the ones currently set in the panel."""
        p = self.params()
        key = lambda q: (q.min_peak_prom, q.divisor, q.uint8_wrap, q.bg_kernel)
        return [e.path.name for e in self.entries if e.res is not None and
                (key(e.res.params) != key(p) if p.bg_kernel is not None else key(e.res.params)[:3] != key(p)[:3])]

    def _run_jobs(self, jobs):
        if not jobs or (self.worker and self.worker.isRunning()):
            return
        self.worker = AnalysisWorker(jobs, self.params())
        self.worker.progress.connect(self._progress); self.worker.finished_one.connect(self._one_done)
        self.progress.setRange(0, len(jobs)); self.progress.show(); self.worker.start()

    def _progress(self, i, n, name):
        self.progress.setValue(i)
        self.statusBar().showMessage(f"processing {name} ({i + 1}/{n})" if name else "done")
        if not name:
            self.progress.hide()

    def _one_done(self, row, res, err):
        e = self.entries[row]
        if res is None:
            e.error, e.res = err, None
        else:
            e.error, e.res, e.turns = "", res, res.turns
            self._rebuild_tables(e)
        self._refresh_row(row)
        if row == self.list.currentRow():
            self.show_current()

    def _rebuild_tables(self, e: Entry):
        e.df, e.cal = plate_tables(e.res, e.plate or None, self.platemap)
        e.flags = quality_flags(e.res, e.cal, e.res.turns)
        if e.edited:
            e.flags.append("rectangles were edited manually")
        self._refresh_row(self._row_of(e))

    def recompute_current(self):
        e = self.cur()
        if not e:
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            p = replace(self.params(), rotate=None if e.turns is None else 90 * e.turns)
            e.res = analyse_plate(e.path, p, e.layout or (e.res.layout if e.res else None))
            e.turns, e.error = e.res.turns, ""
            self._rebuild_tables(e)
        except Exception as ex:
            e.error = str(ex)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._refresh_row(self._row_of(e)); self.show_current(keep_view=True)

    def redetect_current(self):
        e = self.cur()
        if e:
            e.layout, e.edited, e.reviewed, e.res = None, False, False, None
            self._run_jobs([(self._row_of(e), str(e.path), None, e.turns)])

    def mark_reviewed(self):
        e = self.cur()
        if e and e.res is not None:
            e.reviewed = not e.reviewed; self._refresh_row(self._row_of(e))

    def _params_changed(self, *_):
        if self.cur() and self.cur().res is not None:
            self.recompute_current()

    def _rotation_changed(self, *_):
        e = self.cur()
        if e and e.res is not None:
            idx = self.co_rot.currentIndex()
            e.turns = None if idx == 0 else (idx - 1)
            e.layout, e.edited, e.res = None, False, None
            self._run_jobs([(self._row_of(e), str(e.path), None, e.turns)])

    def rect_edited(self, lane, band, x0, x1, y0, y1):
        e = self.cur()
        if not e or e.res is None:
            return
        rects = e.res.layout.rects.copy(); rects[lane, band] = (x0, x1, y0, y1)
        e.layout = replace(e.res.layout, rects=rects); e.edited = True; e.reviewed = False
        self.recompute_current()

    # ---------- display ----------
    def select_row(self, row):
        self.show_current()

    def _rgb_for(self, e: Entry):
        key = (str(e.path), e.res.turns if e.res else 0)
        if self._rgb_cache and self._rgb_cache[0] == key:
            return self._rgb_cache[1]
        rgb = imaging.load_rgb(e.path)
        if e.res and e.res.turns:
            rgb = np.ascontiguousarray(np.rot90(rgb, e.res.turns))
        self._rgb_cache = (key, rgb)
        return rgb

    def refresh_view(self, *_):
        self.show_current(keep_view=True)

    def show_current(self, keep_view=False):
        e = self.cur()
        if not e:
            return
        rgb = self._rgb_for(e)
        t = self.view.transform(); c = self.view.mapToScene(self.view.viewport().rect().center())
        self.view.show_entry(rgb, e.res, self.cb_mask.isChecked(), self.cb_rects.isChecked(), self.cb_proc.isChecked())
        if keep_view:
            self.view.setTransform(t); self.view.centerOn(c)
        else:
            self.view.fit_layout(e.res)
        self._fill_tables(e)

    def _fill_tables(self, e: Entry):
        self.tbl.clearContents(); self.tbl_cal.clearContents()
        if e.res is None:
            self.txt_flags.setPlainText(e.error or "not processed yet"); self.lbl_r2.setText(""); return
        for b in range(3):
            for k in range(9):
                it = QtWidgets.QTableWidgetItem(f"{e.res.values[b, k]:,.0f}")
                it.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                if e.res.overflow_cols[b, k] and e.res.params.uint8_wrap:
                    it.setBackground(QtGui.QColor(255, 210, 120))
                self.tbl.setItem(b, k, it)
            for k in range(6):
                v = e.cal.conc[b, k]
                it = QtWidgets.QTableWidgetItem("" if not np.isfinite(v) else f"{v:,.1f}")
                it.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter); self.tbl_cal.setItem(b, k, it)
        self.lbl_r2.setText("standard-curve R²:  " + "   ".join(f"band {b + 1}: {e.cal.r2[b]:.3f}" for b in range(3)
                                                               if np.isfinite(e.cal.r2[b])) +
                            f"\nkernel {e.res.bg_kernel}px · rotation {90 * e.res.turns}°")
        self.txt_flags.setPlainText("\n".join(e.flags) or "no flags")

    # ---------- export / session ----------
    def export_excel(self, path=None):
        path = path or QtWidgets.QFileDialog.getSaveFileName(self, "Export results", "qtlc_results.xlsx", "Excel (*.xlsx)")[0]
        if not path:
            return
        stale = self.params_mismatch()
        if stale and path != "gui_export.xlsx":
            r = QtWidgets.QMessageBox.question(
                self, "Mixed parameters",
                f"{len(stale)} plate(s) were computed with different parameters than the ones shown "
                f"(e.g. {stale[0]}).\nPress Cancel, then 'Re-process all', to use the same rules for every plate.\n\n"
                "Export anyway?", QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel, QtWidgets.QMessageBox.Cancel)
            if r != QtWidgets.QMessageBox.Yes:
                return
        items = [dict(image=e.path.name, plate=e.plate or None, res=e.res, df=e.df, cal=e.cal, flags=e.flags, error=e.error)
                 for e in self.entries if e.res is not None or e.error]
        try:
            export_workbook(path, items, self.params())
            self.statusBar().showMessage(f"exported {sum(1 for i in items if i.get('res') is not None)} plates to {path}")
        except Exception as ex:
            QtWidgets.QMessageBox.warning(self, "Export failed", str(ex))

    def save_session(self, path=None):
        path = path or QtWidgets.QFileDialog.getSaveFileName(self, "Save session", "session.json", "JSON (*.json)")[0]
        if not path:
            return
        data = dict(params=vars(self.params()), entries=[
            dict(path=str(e.path), plate=e.plate, turns=e.turns, reviewed=e.reviewed, edited=e.edited,
                 rects=(e.layout or (e.res.layout if e.res else None)).rects.tolist() if (e.layout or e.res) else None)
            for e in self.entries])
        Path(path).write_text(json.dumps(data))
        self.statusBar().showMessage(f"session saved to {path}")

    def load_session(self, path=None):
        path = path or QtWidgets.QFileDialog.getOpenFileName(self, "Load session", "", "JSON (*.json)")[0]
        if not path:
            return
        data = json.loads(Path(path).read_text())
        p = data["params"]
        self.sp_prom.setValue(p["min_peak_prom"]); self.sp_div.setValue(p["divisor"])
        self.sp_ker.setValue(p.get("bg_kernel") or 0); self.cb_wrap.setChecked(p.get("uint8_wrap", True))
        self.load_folder(Path(data["entries"][0]["path"]).parent) if data["entries"] else None
        by_path = {str(e.path): e for e in self.entries}
        jobs = []
        for d in data["entries"]:
            e = by_path.get(d["path"])
            if not e:
                continue
            e.plate, e.turns, e.reviewed, e.edited = d["plate"], d["turns"], d["reviewed"], d["edited"]
            if d["rects"] is not None:
                rects = np.array(d["rects"], float)
                e.layout = Layout(rects=rects, lane_centers=(rects[:, 0, 0] + rects[:, 0, 1]) / 2,
                                  lane_spacing=float(np.mean(np.diff((rects[:, 0, 0] + rects[:, 0, 1]) / 2))))
            jobs.append((self._row_of(e), str(e.path), e.layout, e.turns))
            self._refresh_row(self._row_of(e))
        self._run_jobs(jobs)


def main(argv=None) -> int:
    app = QtWidgets.QApplication(sys.argv if argv is None else argv)
    w = MainWindow(); w.show()
    if len(sys.argv) > 1 and Path(sys.argv[1]).is_dir():
        w.load_folder(Path(sys.argv[1]))
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
