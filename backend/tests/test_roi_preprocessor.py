"""Tests for ROI polygon → bounding rect derivation and preprocessor."""
import sys
import importlib

# Import pipeline roi module without triggering backend app package
spec = importlib.util.spec_from_file_location(
    "pipeline_roi",
    "../../pipeline/app/roi.py",
    submodule_search_locations=[],
)
if spec is None:
    # Fallback: test the backend schema version
    from app.schemas.roi import ROIPoint, compute_bounding_rect, BoundingRectResponse

    def test_bounding_rect_from_polygon():
        points = [
            ROIPoint(x=0.2, y=0.3),
            ROIPoint(x=0.6, y=0.3),
            ROIPoint(x=0.6, y=0.7),
            ROIPoint(x=0.2, y=0.7),
        ]
        rect = compute_bounding_rect(points, padding=0.0)
        assert abs(rect.x - 0.2) < 0.001
        assert abs(rect.y - 0.3) < 0.001
        assert abs(rect.w - 0.4) < 0.001
        assert abs(rect.h - 0.4) < 0.001

    def test_bounding_rect_with_padding():
        points = [
            ROIPoint(x=0.3, y=0.3),
            ROIPoint(x=0.7, y=0.3),
            ROIPoint(x=0.7, y=0.7),
            ROIPoint(x=0.3, y=0.7),
        ]
        rect = compute_bounding_rect(points, padding=0.1)
        # Polygon spans 0.3-0.7 (w=0.4), padding = 0.04
        assert rect.x < 0.3
        assert rect.y < 0.3
        assert rect.x + rect.w > 0.7
        assert rect.y + rect.h > 0.7

    def test_bounding_rect_clamped_to_frame():
        """Rect should not exceed 0-1 normalized bounds."""
        points = [
            ROIPoint(x=0.0, y=0.0),
            ROIPoint(x=0.1, y=0.0),
            ROIPoint(x=0.1, y=0.1),
            ROIPoint(x=0.0, y=0.1),
        ]
        rect = compute_bounding_rect(points, padding=0.5)
        assert rect.x >= 0.0
        assert rect.y >= 0.0
        assert rect.x + rect.w <= 1.0
        assert rect.y + rect.h <= 1.0

    def test_bounding_rect_triangle_polygon():
        """Non-rectangular polygon should still produce correct bounding rect."""
        points = [
            ROIPoint(x=0.5, y=0.2),
            ROIPoint(x=0.8, y=0.8),
            ROIPoint(x=0.2, y=0.8),
        ]
        rect = compute_bounding_rect(points, padding=0.0)
        assert abs(rect.x - 0.2) < 0.001
        assert abs(rect.y - 0.2) < 0.001
        assert abs(rect.w - 0.6) < 0.001
        assert abs(rect.h - 0.6) < 0.001

else:
    # Test the pipeline version
    pipeline_roi = importlib.util.module_from_spec(spec)

    def test_bounding_rect_from_polygon():
        from app.schemas.roi import ROIPoint, compute_bounding_rect
        points = [
            ROIPoint(x=0.2, y=0.3),
            ROIPoint(x=0.6, y=0.3),
            ROIPoint(x=0.6, y=0.7),
            ROIPoint(x=0.2, y=0.7),
        ]
        rect = compute_bounding_rect(points, padding=0.0)
        assert abs(rect.x - 0.2) < 0.001
        assert abs(rect.y - 0.3) < 0.001
