import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2] / "src"))


def test_ib_client_importable():
    import services.ib_client  # noqa: F401
