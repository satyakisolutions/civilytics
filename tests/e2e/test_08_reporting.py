"""Step 8 — reporting path produces a report."""
import pytest

pytestmark = pytest.mark.e2e


def test_generate_weekly_report(client):
    r = client.post("/api/reports/generate/weekly")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("report_type") == "weekly"
    assert "status" in body


def test_invalid_report_type_rejected(client):
    r = client.post("/api/reports/generate/quarterly")
    assert r.status_code == 400


def test_report_history_reachable(client):
    r = client.get("/api/reports/history")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
