import pytest
from core.workers.download import DownloadWorker
from core.workers.aria2 import Aria2Worker
from core.workers.fetcher import FileInfoFetcherWorker

# format_bytes() is implemented once and shared by all worker classes; these three
# used to be near-identical copy-pasted test functions differing only by which
# class was instantiated. Parametrizing makes that sharing explicit instead of
# maintaining three copies of the same assertions.
@pytest.mark.parametrize("make_worker,value,expected", [
    (lambda: DownloadWorker("http://example.com/test.zip", 0, "test.zip"), 1024, "1.00  KB"),
    (lambda: DownloadWorker("http://example.com/test.zip", 0, "test.zip"), 1048576, "1.00  MB"),
    (lambda: DownloadWorker("http://example.com/test.zip", 0, "test.zip"), 500, "500.00  B"),
    (lambda: Aria2Worker("http://example.com/test.zip", 0, "."), 2048, "2.00  KB"),
    (lambda: FileInfoFetcherWorker("http://example.com"), 4096, "4.00  KB"),
], ids=["download_kb", "download_mb", "download_bytes", "aria2_kb", "fetcher_kb"])
def test_worker_format_bytes(qapp, make_worker, value, expected):
    worker = make_worker()
    assert worker.format_bytes(value, precision=2, pad=False) == expected

def test_download_worker_format_time(qapp):
    worker = DownloadWorker("http://example.com/test.zip", 0, "test.zip")
    assert worker.format_time(45) == "45 sec"
    assert worker.format_time(120) == "2 min"
    assert worker.format_time(3600) == "1 hr"

def test_extension_max_connections_save_load_and_clamp(qapp, monkeypatch, tmp_path):
    """Was named test_workers_respect_configured_max_connections, but exercises no
    worker at all -- it tests core.utils' extension-config save/load/clamp logic.
    Renamed to match what it actually verifies."""
    from core.utils import save_extension_config, load_extension_config
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    save_extension_config({"protocol": "ws", "port": 56800, "token": "", "max_connections": 12})
    cfg = load_extension_config()
    assert cfg["max_connections"] == 12

    # Verify clamping
    save_extension_config({"max_connections": 999})
    assert load_extension_config()["max_connections"] == 32

    save_extension_config({"max_connections": 0})
    assert load_extension_config()["max_connections"] == 1

