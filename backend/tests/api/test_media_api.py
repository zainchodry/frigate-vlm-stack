from starlette.requests import Request

from app.api import api_token_is_valid


def _request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request({"type": "http", "headers": headers})


def test_api_token_accepts_bearer_header_without_logging_or_query_tokens():
    request = _request([(b"authorization", b"Bearer api-secret")])

    assert api_token_is_valid(request, "api-secret") is True
    assert api_token_is_valid(request, "different") is False


def test_api_token_accepts_loopback_api_key_header():
    request = _request([(b"x-api-key", b"api-secret")])

    assert api_token_is_valid(request, "api-secret") is True
