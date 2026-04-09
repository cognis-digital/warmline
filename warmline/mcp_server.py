"""WARMLINE MCP server — exposes scan() as an MCP tool for Cognis.Studio."""
from __future__ import annotations
from warmline.core import scan, to_json

def serve() -> int:
    """Start an MCP stdio server. Requires the optional 'mcp' extra:
        pip install "cognis-warmline[mcp]"
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception:
        print("Install the MCP extra: pip install 'cognis-warmline[mcp]'")
        return 1
    app = FastMCP("warmline")

    @app.tool()
    def warmline_scan(target: str) -> str:
        """Score and rank inbound/outbound leads from a YAML rulebook, emitting a ranked queue as JSON/CSV for your SDRs and CI gates.. Returns JSON findings."""
        return to_json(scan(target))

    app.run()
    return 0
