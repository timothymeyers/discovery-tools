import json
from pathlib import Path


FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def load_fixture(name: str):
    path = FIXTURES_DIR / name
    if path.suffix == ".xml":
        return path.read_bytes()
    return json.loads(path.read_text(encoding="utf-8"))
