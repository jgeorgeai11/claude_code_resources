"""Unit tests for mcp_client.py.

The HTTP boundary (urllib.request.urlopen) is the only thing mocked; everything
else -- request framing, status handling, and payload parsing -- is exercised for
real.
"""

import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from mcp_client import MCPClient, MCPError


class FakeResponse:
    """Stand-in for the object urlopen returns, usable as a context manager."""

    def __init__(self, body: str, status: int = 200, content_type: str = "application/json") -> None:
        """Initialize the fake response.

        Args:
            body: The response body, encoded to UTF-8 bytes for read().
            status: The HTTP status the client sees. Defaults to 200.
            content_type: The Content-Type header value. Defaults to application/json.
        """
        self.status = status
        self.headers = {"Content-Type": content_type}
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        """Return the response body."""
        return self._body

    def __enter__(self) -> "FakeResponse":
        """Return self, matching the with-block target urlopen provides.

        Returns:
            This response.
        """
        return self

    def __exit__(self, *exc_info: object) -> bool:
        """Propagate any exception raised inside the with-block.

        Args:
            *exc_info: The exception type, value, and traceback, if any.

        Returns:
            False, so an exception raised inside the with-block is not suppressed.
        """
        return False


def ok_body(rows: list[dict[str, Any]], truncated: bool = False) -> str:
    """Build a well-formed run_sql JSON-RPC response body.

    Args:
        rows: The rows the fake server returns.
        truncated: Whether the server flags the result as truncated.

    Returns:
        The serialized JSON-RPC response.
    """
    structured = {"rows": rows, "row_count": len(rows), "truncated": truncated}
    return json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": [{"type": "text", "text": json.dumps(structured)}],
            "structuredContent": structured,
            "isError": False,
        },
    })


@pytest.fixture
def capture_request(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch urlopen to record the request and return a queued response.

    Returns:
        A dict with 'response' (set by the test) and 'request'/'timeout' (set by the
        patched urlopen when the client calls it).
    """
    state: dict[str, Any] = {"response": FakeResponse(ok_body([]))}

    def fake_urlopen(request: urllib.request.Request, timeout: float | None = None) -> FakeResponse:
        state["request"] = request
        state["timeout"] = timeout
        response = state["response"]
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr("mcp_client.urllib.request.urlopen", fake_urlopen)
    return state


# --- Request framing ---


def test_run_sql_sends_well_formed_tools_call(capture_request: dict[str, Any]) -> None:
    """The request carries a JSON-RPC tools/call body for run_sql with both arguments."""
    capture_request["response"] = FakeResponse(ok_body([{"id": "warehouse"}]))
    client = MCPClient("http://localhost:8002/mcp", timeout_s=12.0, token="secret")

    client.run_sql("metadata_db", "select 1")

    request = capture_request["request"]
    body = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "http://localhost:8002/mcp"
    assert request.get_method() == "POST"
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "tools/call"
    assert body["params"]["name"] == "run_sql"
    assert body["params"]["arguments"] == {"database": "metadata_db", "sql": "select 1"}
    assert request.headers["Content-type"] == "application/json"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["Authorization"] == "Bearer secret"
    assert capture_request["timeout"] == 12.0


def test_run_sql_omits_authorization_without_token(capture_request: dict[str, Any]) -> None:
    """No token means no Authorization header at all -- not an empty one."""
    client = MCPClient("http://localhost:8002/mcp")

    client.run_sql("metadata_db", "select 1")

    assert "Authorization" not in capture_request["request"].headers


def test_run_sql_increments_request_id(capture_request: dict[str, Any]) -> None:
    """Request ids are sequential per client, so a run is reproducible."""
    client = MCPClient("http://localhost:8002/mcp")

    client.run_sql("metadata_db", "select 1")
    first = json.loads(capture_request["request"].data.decode("utf-8"))["id"]
    client.run_sql("metadata_db", "select 2")
    second = json.loads(capture_request["request"].data.decode("utf-8"))["id"]

    assert (first, second) == (1, 2)


# --- Success ---


def test_run_sql_returns_parsed_rows(capture_request: dict[str, Any]) -> None:
    """A successful response yields the structuredContent rows verbatim."""
    rows = [
        {"column_id": "fixture_ocs.general.clm.claim_no", "is_primary_key": True},
        {"column_id": "fixture_ocs.general.clm.person_key", "is_primary_key": True},
    ]
    capture_request["response"] = FakeResponse(ok_body(rows))
    client = MCPClient("http://localhost:8002/mcp", token="secret")

    assert client.run_sql("metadata_db", "select 1") == rows


def test_run_sql_returns_empty_list_for_no_rows(capture_request: dict[str, Any]) -> None:
    """A query matching nothing returns an empty list rather than raising."""
    capture_request["response"] = FakeResponse(ok_body([]))
    client = MCPClient("http://localhost:8002/mcp", token="secret")

    assert client.run_sql("metadata_db", "select 1") == []


# --- Failure modes ---


def test_run_sql_raises_on_jsonrpc_error(capture_request: dict[str, Any]) -> None:
    """A JSON-RPC error response raises MCPError carrying the server's message."""
    capture_request["response"] = FakeResponse(json.dumps({
        "jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "unknown tool"},
    }))
    client = MCPClient("http://localhost:8002/mcp", token="secret")

    with pytest.raises(MCPError, match="unknown tool"):
        client.run_sql("metadata_db", "select 1")


def test_run_sql_raises_on_http_error_status(capture_request: dict[str, Any]) -> None:
    """A 401 from the server raises MCPError naming the status and echoing the body."""
    capture_request["response"] = urllib.error.HTTPError(
        "http://localhost:8002/mcp", 401, "Unauthorized", {},
        io.BytesIO(b'{"error":"unauthorized"}'),
    )
    client = MCPClient("http://localhost:8002/mcp")

    with pytest.raises(MCPError, match="HTTP 401") as excinfo:
        client.run_sql("metadata_db", "select 1")

    assert "unauthorized" in str(excinfo.value)


def test_run_sql_raises_on_http_error_with_unreadable_body(capture_request: dict[str, Any]) -> None:
    """An error body that cannot be read still raises MCPError, not the underlying OSError."""

    class BrokenBody(io.BytesIO):
        """An HTTPError body whose socket is gone by the time it is read."""

        def read(self, size: int | None = -1) -> bytes:
            """Fail the way a reset connection does.

            Args:
                size: Byte count urllib would pass; unused, since the read always fails.

            Raises:
                OSError: Always.
            """
            raise OSError("connection reset")

    capture_request["response"] = urllib.error.HTTPError(
        "http://localhost:8002/mcp", 500, "Internal Server Error", {}, BrokenBody(),
    )
    client = MCPClient("http://localhost:8002/mcp", token="secret")

    with pytest.raises(MCPError, match="response body unavailable") as excinfo:
        client.run_sql("metadata_db", "select 1")

    assert "HTTP 500" in str(excinfo.value)


def test_run_sql_raises_on_unexpected_success_status(capture_request: dict[str, Any]) -> None:
    """A 2xx that is not 200 raises rather than being parsed."""
    capture_request["response"] = FakeResponse("", status=204)
    client = MCPClient("http://localhost:8002/mcp", token="secret")

    with pytest.raises(MCPError, match="HTTP 204"):
        client.run_sql("metadata_db", "select 1")


@pytest.mark.parametrize(
    "error",
    [urllib.error.URLError("connection refused"), TimeoutError("timed out")],
    ids=["unreachable", "timeout"],
)
def test_run_sql_raises_on_transport_failure(
    capture_request: dict[str, Any], error: Exception
) -> None:
    """A transport failure raises MCPError rather than a bare URLError or TimeoutError."""
    capture_request["response"] = error
    client = MCPClient("http://localhost:8002/mcp", token="secret")

    with pytest.raises(MCPError, match="could not be completed"):
        client.run_sql("metadata_db", "select 1")


def test_run_sql_raises_on_streaming_content_type(capture_request: dict[str, Any]) -> None:
    """An SSE response is refused, since this client speaks plain JSON only."""
    capture_request["response"] = FakeResponse(
        "data: {}", content_type="text/event-stream"
    )
    client = MCPClient("http://localhost:8002/mcp", token="secret")

    with pytest.raises(MCPError, match="Content-Type"):
        client.run_sql("metadata_db", "select 1")


def test_run_sql_raises_on_non_json_body(capture_request: dict[str, Any]) -> None:
    """A body that is not JSON raises MCPError."""
    capture_request["response"] = FakeResponse("<html>proxy error</html>")
    client = MCPClient("http://localhost:8002/mcp", token="secret")

    with pytest.raises(MCPError, match="not valid JSON"):
        client.run_sql("metadata_db", "select 1")


def test_run_sql_raises_on_non_object_body(capture_request: dict[str, Any]) -> None:
    """A JSON array instead of a JSON-RPC object raises MCPError."""
    capture_request["response"] = FakeResponse("[]")
    client = MCPClient("http://localhost:8002/mcp", token="secret")

    with pytest.raises(MCPError, match="must be a JSON object"):
        client.run_sql("metadata_db", "select 1")


def test_run_sql_raises_on_missing_result(capture_request: dict[str, Any]) -> None:
    """A response with neither result nor error raises MCPError."""
    capture_request["response"] = FakeResponse(json.dumps({"jsonrpc": "2.0", "id": 1}))
    client = MCPClient("http://localhost:8002/mcp", token="secret")

    with pytest.raises(MCPError, match="no usable 'result'"):
        client.run_sql("metadata_db", "select 1")


def test_run_sql_raises_on_tool_error(capture_request: dict[str, Any]) -> None:
    """isError: true raises MCPError carrying the tool's text content."""
    capture_request["response"] = FakeResponse(json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "result": {"content": [{"type": "text", "text": 'type "ltree" does not exist'}], "isError": True},
    }))
    client = MCPClient("http://localhost:8002/mcp", token="secret")

    with pytest.raises(MCPError, match='type "ltree" does not exist'):
        client.run_sql("metadata_db", "select 1")


def test_run_sql_raises_on_tool_error_without_text_content(capture_request: dict[str, Any]) -> None:
    """An isError result with no text block still raises, echoing the raw result."""
    capture_request["response"] = FakeResponse(json.dumps({
        "jsonrpc": "2.0", "id": 1, "result": {"content": "boom", "isError": True},
    }))
    client = MCPClient("http://localhost:8002/mcp", token="secret")

    with pytest.raises(MCPError, match="reported an error"):
        client.run_sql("metadata_db", "select 1")


def test_run_sql_raises_on_missing_structured_content(capture_request: dict[str, Any]) -> None:
    """A result without structuredContent raises rather than falling back to content text."""
    capture_request["response"] = FakeResponse(json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "result": {"content": [{"type": "text", "text": '{"rows": []}'}], "isError": False},
    }))
    client = MCPClient("http://localhost:8002/mcp", token="secret")

    with pytest.raises(MCPError, match="no structuredContent"):
        client.run_sql("metadata_db", "select 1")


def test_run_sql_raises_on_malformed_rows(capture_request: dict[str, Any]) -> None:
    """structuredContent whose rows are not objects raises MCPError."""
    capture_request["response"] = FakeResponse(json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "result": {"structuredContent": {"rows": ["clm"], "truncated": False}, "isError": False},
    }))
    client = MCPClient("http://localhost:8002/mcp", token="secret")

    with pytest.raises(MCPError, match="not a list of objects"):
        client.run_sql("metadata_db", "select 1")


@pytest.mark.parametrize(
    "structured",
    [{"rows": [], "row_count": 0}, {"rows": [], "row_count": 0, "truncated": "false"}],
    ids=["absent", "not_boolean"],
)
def test_run_sql_raises_on_unusable_truncated_flag(
    capture_request: dict[str, Any], structured: dict[str, Any]
) -> None:
    """A truncated flag that is absent or not boolean raises instead of being read as False."""
    capture_request["response"] = FakeResponse(json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "result": {"structuredContent": structured, "isError": False},
    }))
    client = MCPClient("http://localhost:8002/mcp", token="secret")

    with pytest.raises(MCPError, match="truncated"):
        client.run_sql("metadata_db", "select 1")


def test_run_sql_raises_on_truncated_result_naming_query(capture_request: dict[str, Any]) -> None:
    """A truncated result raises and names the query, since it is well-formed and wrong."""
    capture_request["response"] = FakeResponse(
        ok_body([{"column_id": "fixture_ocs.general.clm.claim_no"}], truncated=True)
    )
    client = MCPClient("http://localhost:8002/mcp", token="secret")

    with pytest.raises(MCPError, match="truncated") as excinfo:
        client.run_sql("metadata_db", "select column_id from prod.columns")

    assert "select column_id from prod.columns" in str(excinfo.value)


def test_run_sql_error_excerpt_is_shortened(capture_request: dict[str, Any]) -> None:
    """A very long failing statement is abbreviated in the error message."""
    capture_request["response"] = FakeResponse(ok_body([], truncated=True))
    client = MCPClient("http://localhost:8002/mcp", token="secret")

    with pytest.raises(MCPError) as excinfo:
        client.run_sql("metadata_db", "select " + "x" * 500)

    assert "..." in str(excinfo.value)
