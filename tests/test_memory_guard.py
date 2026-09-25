import pytest
from PyQt6.QtWidgets import QDialog, QWidget, QLabel
from PyQt6.QtCore import QObject, pyqtSignal, QTimer, Qt
from core.memory_guard import MemoryGuard


class DummyEmitter(QObject):
    test_signal = pyqtSignal(str)


def test_memory_guard_dialog_lifecycle_triggers_cleanup(qapp, monkeypatch):
    """Replaces three separate tests that only checked isinstance(int)/isinstance(bool)
    on gc.collect()/SetProcessWorkingSetSize's return values (testing the Python gc
    and libc/kernel32, not this codebase) plus a third with no assertions at all.
    This verifies the actual, observable contract auto_manage_dialog() promises
    (memory_guard.py:159-174): WA_DeleteOnClose gets set, and finishing the dialog
    triggers clean_and_trim()."""
    dlg = QDialog()

    trim_calls = []
    monkeypatch.setattr(MemoryGuard, "clean_and_trim", classmethod(lambda cls: trim_calls.append(True)))

    MemoryGuard.auto_manage_dialog(dlg)
    assert dlg.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose) is True
    assert len(trim_calls) == 0  # not triggered until the dialog actually finishes

    dlg.accept()  # emits finished, which auto_manage_dialog wired to clean_and_trim()
    assert len(trim_calls) == 1

    dlg.close()


def test_memory_guard_safe_delete_later(qapp):
    widget = QWidget()
    MemoryGuard.safe_delete_later(widget)
    # None or non-QObject should also safely pass without error
    MemoryGuard.safe_delete_later(None)
    MemoryGuard.safe_delete_later("not_a_qobject")


def test_memory_guard_safe_disconnect():
    emitter = DummyEmitter()
    called = []
    slot = lambda msg: called.append(msg)
    emitter.test_signal.connect(slot)
    
    # Successful disconnect
    assert MemoryGuard.safe_disconnect(emitter.test_signal, slot) is True
    
    # Safe repeated disconnect (no crash)
    assert MemoryGuard.safe_disconnect(emitter.test_signal, slot) is False
    assert MemoryGuard.safe_disconnect(None) is False


def test_memory_guard_auto_manage_dialog(qapp):
    dlg = QDialog()
    MemoryGuard.auto_manage_dialog(dlg)
    counts = MemoryGuard.get_tracked_counts()
    assert "active_dialogs" in counts
    assert counts["active_dialogs"] >= 1
    
    # Finish dialog
    dlg.accept()
    dlg.close()


def test_memory_guard_track_worker(qapp):
    worker = DummyEmitter()
    MemoryGuard.track_worker(worker)
    counts = MemoryGuard.get_tracked_counts()
    assert "active_workers" in counts
    assert counts["active_workers"] >= 1


def test_memory_guard_periodic_timer(qapp):
    timer = MemoryGuard.start_periodic_trim(interval_ms=1000)
    assert isinstance(timer, QTimer)
    assert timer.isActive()
    timer.stop()
