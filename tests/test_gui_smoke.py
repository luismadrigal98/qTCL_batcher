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
