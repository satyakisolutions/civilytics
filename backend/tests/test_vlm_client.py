"""Tests for VLM client."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from app.vlm.schemas import VLMRequest, VLMResponse, VLMSeverity, WasteType


@pytest.fixture
def vlm_request():
    return VLMRequest(
        camera_id="cam-01",
        track_id=42,
        track_session_id="sess-1",
        zone_name="Parking Lot",
        dwell_time_s=25.0,
        class_name="person",
        path_summary="stationary",
        frames_base64=["abc123"],
        frame_timestamps=[1.0],
    )


@pytest.mark.asyncio
async def test_analyze_success(vlm_request):
    from app.vlm.client import VLMClient
    response_json = '{"person_description":"adult","action":"dumping","is_dumping":true,"waste_type":"plastic_bag","severity":"critical","confidence":0.95,"reasoning":"test","command":"alert"}'

    client = VLMClient()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"choices": [{"message": {"content": response_json}}]}

    with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_resp):
        result = await client.analyze(vlm_request)

    assert result is not None
    assert result.camera_id == "cam-01"
    assert result.response.is_dumping is True
    assert result.frames_analyzed == 1
    assert result.inference_time_ms > 0
    await client.close()


@pytest.mark.asyncio
async def test_analyze_parse_failure_retries(vlm_request):
    from app.vlm.client import VLMClient
    client = VLMClient()

    # First call returns unparseable, second returns valid
    valid_json = '{"person_description":"","action":"loitering","is_dumping":false,"waste_type":"none","severity":"info","confidence":0.3,"reasoning":"ok","command":"monitor"}'
    mock_resp_bad = MagicMock()
    mock_resp_bad.raise_for_status = MagicMock()
    mock_resp_bad.json.return_value = {"choices": [{"message": {"content": "not json at all"}}]}

    mock_resp_good = MagicMock()
    mock_resp_good.raise_for_status = MagicMock()
    mock_resp_good.json.return_value = {"choices": [{"message": {"content": valid_json}}]}

    with patch.object(client._http, "post", new_callable=AsyncMock, side_effect=[mock_resp_bad, mock_resp_good]):
        result = await client.analyze(vlm_request)

    assert result is not None
    assert result.response.is_dumping is False
    await client.close()


@pytest.mark.asyncio
async def test_analyze_double_failure_fallback(vlm_request):
    from app.vlm.client import VLMClient
    client = VLMClient()

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"choices": [{"message": {"content": "garbage"}}]}

    with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_resp):
        result = await client.analyze(vlm_request)

    assert result is not None
    assert result.response.confidence == 0.0
    assert result.response.severity == VLMSeverity.warning
    await client.close()


@pytest.mark.asyncio
async def test_analyze_http_error(vlm_request):
    from app.vlm.client import VLMClient
    client = VLMClient()

    with patch.object(client._http, "post", new_callable=AsyncMock, side_effect=Exception("timeout")):
        result = await client.analyze(vlm_request)

    # Should return fallback, not crash
    assert result is not None
    assert result.response.confidence == 0.0
    await client.close()
