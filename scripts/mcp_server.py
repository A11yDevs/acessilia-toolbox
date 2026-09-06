#!/usr/bin/env python3
"""Expose the toolbox as an MCP server over stdio transport.

Usage:
    python scripts/mcp_server.py

    # Then connect with any MCP client, for example the mcp CLI:
    pip install mcp
    mcp run scripts/mcp_server.py

    # Or test interactively:
    python scripts/mcp_server.py --interactive
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from acessilia_toolbox.api.mcp import ToolboxMCPServer  # noqa: E402
from acessilia_toolbox.core.capability import CapabilityRegistry  # noqa: E402
from acessilia_toolbox.core.executor import CapabilityExecutor  # noqa: E402
from acessilia_toolbox.core.provider import ProviderRegistry  # noqa: E402
from acessilia_toolbox.providers import create_adapter  # noqa: E402

CAPABILITIES_DIR = PROJECT_ROOT / "capabilities"
PROVIDERS_CONFIG = PROJECT_ROOT / "providers-config.yaml"


def _build_server() -> ToolboxMCPServer:
    capabilities = CapabilityRegistry.from_directory(CAPABILITIES_DIR)
    providers = ProviderRegistry.from_file(PROVIDERS_CONFIG)
    executor = CapabilityExecutor(capabilities, providers, create_adapter)
    return ToolboxMCPServer(capabilities, providers, executor)


def _handle_request(server: ToolboxMCPServer, request: dict[str, Any]) -> dict[str, Any]:
    """Process a single JSON-RPC 2.0 request."""
    req_id = request.get("id")
    method = request.get("method", "")
    params = request.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": server.get_server_info(),
                "capabilities": {
                    "tools": {},
                    "resources": {},
                },
            },
        }
    elif method == "notifications/initialized":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": server.list_tools()},
        }
    elif method == "tools/call":
        result = server.call_tool(params.get("name", ""), params.get("arguments", {}))
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"content": result[0]["content"] if result else []},
        }
    elif method == "resources/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"resources": server.list_resources()},
        }
    elif method == "resources/read":
        uri = params.get("uri", "")
        content = server.read_resource(uri)
        mime = (
            "application/vnd.pddl+text"
            if uri.startswith("acessilia://planning/")
            else "application/json"
        )
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "contents": [{"uri": uri, "mimeType": mime, "text": content}]
            },
        }
    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }


def main_stdio() -> None:
    """Run over stdio transport (standard MCP mode)."""
    server = _build_server()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = _handle_request(server, request)
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
        except json.JSONDecodeError as exc:
            error = {"jsonrpc": "2.0", "error": {"code": -32700, "message": f"Parse error: {exc}"}}
            sys.stdout.write(json.dumps(error) + "\n")
            sys.stdout.flush()


def main_interactive() -> None:
    """Interactive mode for manual testing."""
    server = _build_server()

    print("=== Acessilia Toolbox MCP Server ===")
    print(f"Server: {server.get_server_info()['name']} v{server.get_server_info()['version']}")
    print()

    tools = server.list_tools()
    print("=== Tools ===")
    for tool in tools:
        print(f"  {tool['name']}")
        print(f"    {tool['description']}")
        for prop, schema in tool.get("inputSchema", {}).get("properties", {}).items():
            print(f"    - {prop}: {schema.get('description', '')}")
    print()

    resources = server.list_resources()
    print("=== Resources ===")
    for res in resources:
        print(f"  {res['uri']}")
        print(f"    {res['name']}: {res['description']}")
    print()

    print("=== Interactive Testing ===")
    print("Commands:")
    print("  tools                        List tools")
    print("  resources                    List resources")
    print("  read <uri>                   Read a resource")
    print("  call <tool> <json args>      Call a tool")
    print("  quit                         Exit")
    print()

    while True:
        try:
            cmd = input("mcp> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not cmd or cmd == "quit":
            break
        elif cmd == "tools":
            for tool in tools:
                print(f"  {tool['name']}: {tool['description']}")
        elif cmd == "resources":
            for res in resources:
                print(f"  {res['uri']}")
        elif cmd.startswith("read "):
            uri = cmd[5:]
            try:
                content = server.read_resource(uri)
                print(content[:2000])
            except Exception as exc:
                print(f"Error: {exc}")
        elif cmd.startswith("call "):
            parts = cmd[5:].split(" ", 1)
            if len(parts) < 2:
                print("Usage: call <tool> <json args>")
                continue
            tool_name, args_str = parts
            try:
                args = json.loads(args_str) if args_str.strip() else {}
            except json.JSONDecodeError as exc:
                print(f"Invalid JSON: {exc}")
                continue
            result = server.call_tool(tool_name, args)
            for item in result:
                for content in item.get("content", []):
                    print(content.get("text", "")[:2000])
        else:
            print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    if "--interactive" in sys.argv:
        main_interactive()
    else:
        main_stdio()
