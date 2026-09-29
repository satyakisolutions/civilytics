import uuid
from datetime import timezone
from pathlib import Path

import pytest

from app.subjects.types import FaceRequest


def test_from_payload_keeps_crops_in_own_folder(tmp_path: Path):
    iid = uuid.uuid4()
    own = tmp_path / str(iid) / "00.jpg"
    req = FaceRequest.from_payload(
        {"incident_id": str(iid), "camera_id": "cam-01", "detected_at": 1790000000.5,
         "person_track_id": 7, "crops": [str(own)]},
        tmp_path,
    )
    assert req.incident_id == iid
    assert req.camera_id == "cam-01"
    assert req.person_track_id == 7
    assert req.crops == (str(own.resolve()),)
    assert req.detected_at.tzinfo == timezone.utc
    assert req.detected_at.timestamp() == pytest.approx(1790000000.5)


def test_from_payload_drops_paths_outside_incident_folder(tmp_path: Path):
    iid = uuid.uuid4()
    other = tmp_path / str(uuid.uuid4()) / "00.jpg"
    traversal = tmp_path / str(iid) / ".." / ".." / "etc" / "passwd"
    req = FaceRequest.from_payload(
        {"incident_id": str(iid), "camera_id": "cam-01", "detected_at": 1.0,
         "crops": [str(other), str(traversal), "/etc/passwd"]},
        tmp_path,
    )
    assert req.crops == ()


def test_from_payload_rejects_missing_or_bad_incident_id(tmp_path: Path):
    with pytest.raises(KeyError):
        FaceRequest.from_payload({"camera_id": "cam-01"}, tmp_path)
    with pytest.raises(ValueError):
        FaceRequest.from_payload({"incident_id": "not-a-uuid"}, tmp_path)
