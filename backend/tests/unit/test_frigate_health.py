from app.main import camera_health_status


def test_camera_health_is_degraded_when_frigate_reports_unusable_quality():
    status = camera_health_status(
        {
            "camera_fps": 6.1,
            "process_fps": 6.1,
            "connection_quality": "unusable",
        }
    )

    assert status == "degraded"


def test_camera_health_requires_nonzero_capture_and_process_rates():
    assert camera_health_status({"camera_fps": 0, "process_fps": 0}) == "degraded"
    assert camera_health_status({"camera_fps": 5, "process_fps": 5}) == "healthy"

