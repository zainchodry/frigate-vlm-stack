from app.logging import redact_mapping


def test_redact_mapping_handles_nested_passwords():
    assert redact_mapping({"password": "p", "nested": {"authorization": "Bearer x"}}) == {
        "password": "[redacted]",
        "nested": {"authorization": "[redacted]"},
    }

