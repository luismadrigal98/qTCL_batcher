import os

import pytest

pytest.importorskip("PyQt5")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_gui_window_builds_and_lists_photos(tmp_path):
    from PyQt5 import QtWidgets
    from PIL import Image

    from qtlc_batcher.gui import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    Image.new("RGB", (64, 48), "white").save(tmp_path / "a.jpg")
    (tmp_path / "notes.txt").write_text("not a photo")
    w = MainWindow()
    w.load_folder(tmp_path)
    assert [e.path.name for e in w.entries] == ["a.jpg"]
    assert w.list.rowCount() == 1 and w.entries[0].status == "pending"
    assert w.params().min_peak_prom == 3.0 and w.params().rotate is None and not w.params().uint8_wrap
    w.close()


def test_manual_edit_is_audited_in_export_and_session(tmp_path):
    import time

    import numpy as np
    import pandas as pd
    from PIL import Image
    from PyQt5 import QtWidgets

    from qtlc_batcher.gui import MainWindow
    from test_layout import synthetic_plate

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    rgb, _, _ = synthetic_plate(0.0)
    Image.fromarray(rgb).save(tmp_path / "plate.png")
    w = MainWindow()
    w.load_folder(tmp_path)
    w.process_all()
    t0 = time.time()
    while w.worker and w.worker.isRunning() and time.time() - t0 < 120:
        app.processEvents(); time.sleep(0.05)
    app.processEvents()
    w.list.selectRow(0); app.processEvents()
    e = w.cur()
    x0, x1, y0, y1 = e.res.layout.rects[4, 1]               # lane 5, band 2: shift the box up by 25 px
    w.rect_edited(4, 1, x0, x1, y0 - 25, y1 - 25)
    assert e.edited and e.auto_layout is not None
    w.cb_blind.setChecked(True)
    assert w.results_box.isHidden()
    w.export_excel(str(tmp_path / "out.xlsx"))
    tidy = pd.read_excel(tmp_path / "out.xlsx", sheet_name="Tidy")
    edited = tidy[tidy.box_edited]
    assert list(zip(edited.lane_index, edited.band)) == [(5, 2)]       # only the box that was moved
    row = edited.iloc[0]
    assert row.integral != row.integral_auto                           # the edit changed this value...
    same = tidy[~tidy.box_edited & (tidy.lane_index > 3)]
    assert np.allclose(same.integral, same.integral_auto)              # ...and no other sample box
    w.save_session(str(tmp_path / "s.json"))
    w2 = MainWindow(); w2.load_session(str(tmp_path / "s.json"))
    assert np.allclose(w2.entries[0].auto_layout.rects, e.auto_layout.rects)
    while w2.worker and w2.worker.isRunning():
        app.processEvents(); time.sleep(0.05)
    w.close(); w2.close()
