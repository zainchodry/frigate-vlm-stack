import sqlite3

from scripts.import_frigate_sqlite import event_payload


def test_sqlite_event_payload_preserves_source_identity_and_media_reference(tmp_path):
    path = tmp_path / "frigate.db"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute(
        "create table event (id text, label text, camera text, start_time real, end_time real, "
        "top_score real, zones text, thumbnail text, has_clip integer, has_snapshot integer, "
        "sub_label text, score real, data text)"
    )
    connection.execute(
        "insert into event values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "event-1",
            "person",
            "iphone_camera",
            1793181600,
            1793181605,
            None,
            "[]",
            None,
            1,
            0,
            "Zain",
            None,
            '{"sub_label_score": 0.91}',
        ),
    )
    connection.commit()
    row = connection.execute("select * from event").fetchone()

    payload, source_event_at = event_payload(row)

    assert payload["after"]["sub_label"] == "Zain"
    assert payload["after"]["recording_path"] == "/api/events/event-1/clip.mp4"
    assert source_event_at.isoformat() == "2026-10-28T10:00:05+00:00"
