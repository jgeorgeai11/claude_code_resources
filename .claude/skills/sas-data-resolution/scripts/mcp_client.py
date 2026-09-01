"""Minimal JSON-RPC client for the metadata_db MCP HTTP server.

The resolver runs where there is no direct Postgres access, so it reaches the
catalog the same way the agent does: a JSON-RPC `tools/call` for the `run_sql`
tool over the MCP server's streamable-HTTP endpoint. The server is stateless --
a `tools/call` succeeds without an `initialize` handshake -- so this client is a
single POST per query and holds no session.

Only the standard library is used (urllib), so the resolver adds no dependency.

Every failure mode raises MCPError rather than returning a partial result. That
includes a truncated result: the server caps rows (MCP_MAX_ROWS), and a silently
truncated column list would produce a well-formed but wrong resolution that
neither the coordinate check nor the output validator could catch.
"""

import sys
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# logconfig ships beside this module in this skill. Resolve against this file,
# never the cwd, so this module imports from any working directory.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from logconfig import get_logger

logger = get_logger(__name__)

# The MCP tool this client calls, and the only content type it speaks.
RUN_SQL_TOOL = "run_sql"
JSON_CONTENT_TYPE = "application/json"

# How much of a statement, response body, or tool error to echo in a message. Full
# statements are logged at DEBUG; exception text stays readable.
EXCERPT_CHARS = 200


class MCPError(RuntimeError):
    """Raised when an MCP request fails or returns an unusable payload."""


class MCPClient:
    """Issues `run_sql` calls against an MCP HTTP endpoint.

    Attributes:
        url: The MCP endpoint URL.
        timeout_s: Per-request socket timeout in seconds.
    """

    def __init__(self, url: str, timeout_s: float = 30.0, token: str | None = None) -> None:
        """Initialize the client.

        Args:
            url: The MCP endpoint URL (e.g. http://localhost:8002/mcp).
            timeout_s: Per-request socket timeout in seconds. Defaults to 30.0.
            token: Bearer token for the Authorization header. The server rejects
                unauthenticated requests with 401, so this is normally required;
                None sends no header, which surfaces as an explicit 401 failure.
        """
        self.url = url
        self.timeout_s = timeout_s
        self._token = token
        # Request ids are per-client and sequential, so a run is reproducible.
        self._request_id = 0

    def run_sql(self, database: str, sql: str) -> list[dict[str, Any]]:
        """Run a read-only SQL statement through the MCP `run_sql` tool.

        Args:
            database: The database the MCP server should target (e.g. metadata_db).
            sql: A single read-only SQL statement.

        Returns:
            The result rows, each a mapping of column name to value.

        Raises:
            MCPError: On a transport failure, a non-200 status, a JSON-RPC error, a
                tool error, an unreadable result payload, or a truncated result.
        """
        payload = self._call_tool(RUN_SQL_TOOL, {"database": database, "sql": sql})
        rows = self._extract_rows(payload, sql)
        logger.debug(f"run_sql returned {len(rows)} rows: {_excerpt(sql)}")
        return rows

    def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """POST a JSON-RPC `tools/call` request and return the `result` object.

        Args:
            tool_name: The MCP tool to invoke.
            arguments: The tool's arguments.

        Returns:
            The JSON-RPC `result` object.

        Raises:
            MCPError: On a transport failure, a non-200 status, an unexpected
                Content-Type, a non-JSON or non-object response, a JSON-RPC error, a
                missing result, or a tool error.
        """
        self._request_id += 1
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }).encode("utf-8")

        headers = {"Content-Type": JSON_CONTENT_TYPE, "Accept": JSON_CONTENT_TYPE}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                status = response.status
                content_type = response.headers.get("Content-Type", "")
                # errors="replace" keeps a mangled body inside the MCPError contract:
                # it will fail JSON parsing below rather than escaping as UnicodeDecodeError.
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            # Non-2xx: 401 (no or bad token) and 500 (server-side failure) arrive here.
            # Reading the error body can itself raise OSError (socket closed or reset
            # mid-body), which would escape as a non-MCPError, so it is handled here.
            try:
                error_body = _excerpt(e.read().decode("utf-8", errors="replace"))
            except OSError:
                error_body = "<response body unavailable>"
            logger.error(f"MCP request failed with HTTP {e.code}: {error_body}")
            raise MCPError(
                f"MCP request to {self.url} failed with HTTP {e.code}: {error_body}"
            ) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            logger.error(f"MCP request to {self.url} could not be completed: {e}")
            raise MCPError(f"MCP request to {self.url} could not be completed: {e}") from e

        if status != 200:
            detail = f"MCP request to {self.url} returned HTTP {status}, expected 200"
            logger.error(detail)
            raise MCPError(detail)
        # The endpoint may negotiate an SSE stream; this client speaks plain JSON only,
        # so anything else is refused rather than half-parsed. The media type is matched
        # exactly, with parameters such as charset stripped, so that a type that merely
        # contains "application/json" (application/jsonl) is not treated as JSON.
        if content_type.split(";")[0].strip().lower() != JSON_CONTENT_TYPE:
            detail = f"MCP response Content-Type is '{content_type}', expected {JSON_CONTENT_TYPE}"
            logger.error(detail)
            raise MCPError(detail)

        try:
            message = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(f"MCP response was not valid JSON: {_excerpt(raw)}")
            raise MCPError(f"MCP response was not valid JSON: {e}") from e

        if not isinstance(message, dict):
            detail = f"MCP response must be a JSON object, got {type(message).__name__}"
            logger.error(detail)
            raise MCPError(detail)
        if "error" in message:
            detail = f"MCP returned a JSON-RPC error for {tool_name}: {message['error']}"
            logger.error(detail)
            raise MCPError(detail)

        result = message.get("result")
        if not isinstance(result, dict):
            detail = f"MCP response for {tool_name} has no usable 'result' object"
            logger.error(detail)
            raise MCPError(detail)
        if result.get("isError"):
            detail = f"MCP tool '{tool_name}' reported an error: {_tool_error_text(result)}"
            logger.error(detail)
            raise MCPError(detail)
        return result

    @staticmethod
    def _extract_rows(result: dict[str, Any], sql: str) -> list[dict[str, Any]]:
        """Read the row list out of a `run_sql` result, refusing a truncated one.

        `structuredContent` is the only payload read: the same JSON is duplicated into
        `content[0].text`, but falling back to it would mask a malformed response
        instead of reporting it.

        Args:
            result: The JSON-RPC `result` object.
            sql: The statement that produced it, for error messages.

        Returns:
            The result rows.

        Raises:
            MCPError: If the payload is missing or malformed, if the `truncated` flag is
                absent or not a boolean, or if the result is truncated.
        """
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            detail = f"MCP run_sql result has no structuredContent: {_excerpt(sql)}"
            logger.error(detail)
            raise MCPError(detail)

        # The flag itself is validated, not just read: absent or non-boolean would make
        # a row-capped result look complete, which is the one failure this module must
        # never pass through.
        truncated = structured.get("truncated")
        if not isinstance(truncated, bool):
            detail = (
                f"MCP run_sql result has no boolean 'truncated' flag, got "
                f"{type(truncated).__name__}: {_excerpt(sql)}"
            )
            logger.error(detail)
            raise MCPError(detail)

        # A truncated result is well-formed and wrong, so it must fail loudly: raise
        # the server's MCP_MAX_ROWS cap rather than paginating here.
        if truncated:
            logger.error(
                f"MCP run_sql result was truncated at {structured.get('row_count')} rows; "
                f"query: {_excerpt(sql)}"
            )
            raise MCPError(
                f"MCP run_sql result was truncated at {structured.get('row_count')} rows "
                f"(raise the server's row cap); query: {_excerpt(sql)}"
            )

        rows = structured.get("rows")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            detail = f"MCP run_sql result 'rows' is not a list of objects: {_excerpt(sql)}"
            logger.error(detail)
            raise MCPError(detail)
        return rows


def _excerpt(text: str) -> str:
    """Shorten text for an error message.

    Args:
        text: The text to shorten.

    Returns:
        The text, truncated with an ellipsis when longer than EXCERPT_CHARS.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= EXCERPT_CHARS:
        return collapsed
    return collapsed[:EXCERPT_CHARS] + "..."


def _tool_error_text(result: dict[str, Any]) -> str:
    """Extract the human-readable message from an `isError` tool result.

    Args:
        result: The JSON-RPC `result` object.

    Returns:
        The first text content block, or the whole result when none is present.
    """
    content = result.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                return _excerpt(block["text"])
    return _excerpt(json.dumps(result))
