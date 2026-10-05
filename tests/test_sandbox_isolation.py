import pytest
from engine.safe_executor import DurableRefundDestination


def test_database_path_traversal_denied(tmp_path):
    with pytest.raises(PermissionError):
        DurableRefundDestination(tmp_path / "root", "../outside.sqlite3")


def test_symlink_database_denied(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    real = tmp_path / "real.sqlite3"
    real.touch()
    (root / "refunds.sqlite3").symlink_to(real)
    with pytest.raises(PermissionError):
        DurableRefundDestination(root)
