"""Check the evidence /file path-traversal guard logic (no app import needed)."""
from pathlib import Path


def _allowed(root: Path, path: str) -> bool:
    return Path(path).resolve().is_relative_to(root.resolve())


def test_path_guard():
    root = Path("/app/evidence")
    assert _allowed(root, "/app/evidence/violations/cam-01/a.jpg")
    assert not _allowed(root, "/etc/passwd")
    assert not _allowed(root, "/app/evidence/../secrets.txt")  # traversal
    assert not _allowed(root, "/app/evidence-secret/x.jpg")    # sibling prefix (old bug)


if __name__ == "__main__":
    test_path_guard()
    print("path guard OK")
