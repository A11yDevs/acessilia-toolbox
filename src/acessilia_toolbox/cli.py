"""Command-line interface.

Replaces `acessilia-extract`. The CLI now speaks capabilities rather than
extraction backends, matching what REST and MCP expose.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from pathlib import Path
from typing import Any

from acessilia_toolbox import __version__
from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.errors import InvalidInputError, ToolboxError
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.provider import ProviderRegistry
from acessilia_toolbox.providers import create_adapter

DEFAULT_CAPABILITIES_DIR = Path("capabilities")
DEFAULT_PROVIDERS_CONFIG = Path("providers-config.yaml")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acessilia-toolbox",
        description="Invoke Acessilia Toolbox capabilities from the command line.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--capabilities-dir",
        type=Path,
        default=Path(os.getenv("TOOLBOX_CAPABILITIES_DIR", DEFAULT_CAPABILITIES_DIR)),
        help="Directory holding capability manifests.",
    )
    parser.add_argument(
        "--providers-config",
        type=Path,
        default=Path(os.getenv("TOOLBOX_PROVIDERS_CONFIG", DEFAULT_PROVIDERS_CONFIG)),
        help="Provider topology file.",
    )

    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("capabilities", help="List registered capabilities.")
    listing.add_argument("--json", action="store_true", help="Emit JSON.")

    provider_listing = commands.add_parser("providers", help="List registered providers.")
    provider_listing.add_argument("--json", action="store_true", help="Emit JSON.")
    provider_listing.add_argument(
        "--health", action="store_true", help="Probe each provider."
    )

    execute = commands.add_parser("execute", help="Execute a capability on a document.")
    execute.add_argument("capability", help="Capability ID, e.g. document.structure.extract")
    execute.add_argument("document", type=Path, help="Input document.")
    execute.add_argument("-o", "--output", type=Path, help="Output JSON path.")
    execute.add_argument("--provider", help="Provider ID; required when several apply.")
    execute.add_argument("--capability-version", type=int, help="Pin a capability version.")
    execute.add_argument("--language", default="pt-BR", help="BCP 47 document language.")
    execute.add_argument("--parameters", help="Provider parameters as a JSON object.")
    execute.add_argument(
        "--provenance", action="store_true", help="Print provenance to stderr."
    )

    dataset = commands.add_parser("dataset", help="Dataset operations.")
    dataset_sub = dataset.add_subparsers(dest="dataset_command", required=True)

    ds_list = dataset_sub.add_parser("list", help="List available datasets.")
    ds_list.add_argument("--json", action="store_true", help="Emit JSON.")

    ds_describe = dataset_sub.add_parser("describe", help="Describe a dataset.")
    ds_describe.add_argument("id", help="Dataset identifier.")
    ds_describe.add_argument("--revision", help="Git/HF revision to pin.")
    ds_describe.add_argument("--json", action="store_true", help="Emit JSON.")

    ds_splits = dataset_sub.add_parser("list-splits", help="List dataset splits.")
    ds_splits.add_argument("id", help="Dataset identifier.")
    ds_splits.add_argument("--revision", help="Git/HF revision to pin.")
    ds_splits.add_argument("--json", action="store_true", help="Emit JSON.")

    ds_items = dataset_sub.add_parser("list-items", help="List items in a split.")
    ds_items.add_argument("id", help="Dataset identifier.")
    ds_items.add_argument("split", help="Split name.")
    ds_items.add_argument("--revision", help="Git/HF revision to pin.")
    ds_items.add_argument("--limit", type=int, default=100, help="Max items.")
    ds_items.add_argument("--offset", type=int, default=0, help="Pagination offset.")
    ds_items.add_argument("--json", action="store_true", help="Emit JSON.")

    ds_item = dataset_sub.add_parser("get-item", help="Get a full item.")
    ds_item.add_argument("id", help="Dataset identifier.")
    ds_item.add_argument("split", help="Split name.")
    ds_item.add_argument("item_id", help="Item identifier.")
    ds_item.add_argument("--revision", help="Git/HF revision to pin.")
    ds_item.add_argument("--json", action="store_true", help="Emit JSON.")

    ds_artifact = dataset_sub.add_parser("get-artifact", help="Get artifact bytes.")
    ds_artifact.add_argument("id", help="Dataset identifier.")
    ds_artifact.add_argument("path", help="Artifact path within dataset.")
    ds_artifact.add_argument("--revision", help="Git/HF revision to pin.")
    ds_artifact.add_argument("-o", "--output", type=Path, help="Output file path.")

    ds_sample = dataset_sub.add_parser("sample", help="Sample items from a split.")
    ds_sample.add_argument("id", help="Dataset identifier.")
    ds_sample.add_argument("split", help="Split name.")
    ds_sample.add_argument("--revision", help="Git/HF revision to pin.")
    ds_sample.add_argument("-n", type=int, default=5, help="Number of samples.")
    ds_sample.add_argument("--json", action="store_true", help="Emit JSON.")

    ds_sync = dataset_sub.add_parser("sync", help="Mirror dataset artifacts to local store.")
    ds_sync.add_argument("id", help="Dataset identifier.")
    ds_sync.add_argument("--revision", help="Git/HF revision to pin.")
    ds_sync.add_argument("--split", help="Sync only this split.")
    ds_sync.add_argument("--json", action="store_true", help="Emit JSON.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "capabilities":
            return _list_capabilities(args)
        if args.command == "providers":
            return _list_providers(args)
        if args.command == "dataset":
            return _dataset_command(args)
        return _execute(args)
    except ToolboxError as error:
        print(f"{error.code}: {error.message}", file=sys.stderr)
        return EXIT_ERROR
    except FileNotFoundError as error:
        print(f"file not found: {error}", file=sys.stderr)
        return EXIT_USAGE


def _list_capabilities(args: argparse.Namespace) -> int:
    registry = CapabilityRegistry.from_directory(args.capabilities_dir)
    manifests = registry.manifests()

    if args.json:
        print(json.dumps([m.model_dump(mode="json", by_alias=True) for m in manifests], indent=2))
        return EXIT_OK

    for manifest in manifests:
        providers = ", ".join(b.id for b in manifest.providers) or "none"
        print(f"{manifest.key}")
        print(f"  {manifest.description.strip()}")
        print(f"  providers: {providers}")
    return EXIT_OK


def _list_providers(args: argparse.Namespace) -> int:
    registry = ProviderRegistry.from_file(args.providers_config)
    descriptors = registry.descriptors()

    if args.json:
        print(json.dumps([d.public_payload() for d in descriptors], indent=2))
        return EXIT_OK

    for descriptor in descriptors:
        print(f"{descriptor.id}@{descriptor.version}  {descriptor.endpoint or '-'}")
        print(f"  capabilities: {', '.join(descriptor.capabilities)}")
        if args.health:
            health = create_adapter(descriptor).health()
            status = "healthy" if health.healthy else f"unhealthy ({health.detail})"
            print(f"  status: {status}")
    return EXIT_OK


def _execute(args: argparse.Namespace) -> int:
    document: Path = args.document.resolve()
    if not document.is_file():
        raise FileNotFoundError(document)

    executor = CapabilityExecutor(
        CapabilityRegistry.from_directory(args.capabilities_dir),
        ProviderRegistry.from_file(args.providers_config),
        create_adapter,
    )

    result = executor.execute(
        args.capability,
        document.read_bytes(),
        filename=document.name,
        media_type=_media_type(document),
        provider_id=args.provider,
        capability_version=args.capability_version,
        parameters=_parse_parameters(args.parameters),
        language=args.language,
    )

    output = args.output or document.with_suffix(".structured-document.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result.document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    summary = result.document.get("summary", {})
    print(f"{result.capability} via {result.provider} -> {output}")
    print(
        f"pages: {summary.get('page_count')}; "
        f"elements: {summary.get('element_count')}; "
        f"obligations: {summary.get('obligation_count')}"
    )
    if args.provenance:
        print(json.dumps(result.provenance.to_payload(), indent=2), file=sys.stderr)
    return EXIT_OK


def _dataset_command(args: argparse.Namespace) -> int:
    """Dispatch dataset subcommands."""
    executor = CapabilityExecutor(
        CapabilityRegistry.from_directory(args.capabilities_dir),
        ProviderRegistry.from_file(args.providers_config),
        create_adapter,
    )

    cmd = args.dataset_command
    provider = "dataset-github"

    if cmd == "list":
        result = executor.execute(
            "dataset.list", b"{}", filename="query.json", media_type="application/json",
            provider_id=provider,
        )
        _print_json(result.document, args)
        return EXIT_OK

    if cmd == "describe":
        result = executor.execute(
            "dataset.describe", b"{}", filename="query.json", media_type="application/json",
            provider_id=provider,
            parameters={"dataset_id": args.id, "revision": args.revision},
        )
        _print_json(result.document, args)
        return EXIT_OK

    if cmd == "list-splits":
        result = executor.execute(
            "dataset.list_splits", b"{}", filename="query.json", media_type="application/json",
            provider_id=provider,
            parameters={"dataset_id": args.id, "revision": args.revision},
        )
        _print_json(result.document, args)
        return EXIT_OK

    if cmd == "list-items":
        result = executor.execute(
            "dataset.list_items", b"{}", filename="query.json", media_type="application/json",
            provider_id=provider,
            parameters={
                "dataset_id": args.id,
                "split": args.split,
                "revision": args.revision,
                "limit": args.limit,
                "offset": args.offset,
            },
        )
        _print_json(result.document, args)
        return EXIT_OK

    if cmd == "get-item":
        result = executor.execute(
            "dataset.get_item", b"{}", filename="query.json", media_type="application/json",
            provider_id=provider,
            parameters={
                "dataset_id": args.id,
                "split": args.split,
                "item_id": args.item_id,
                "revision": args.revision,
            },
        )
        _print_json(result.document, args)
        return EXIT_OK

    if cmd == "get-artifact":
        result = executor.execute(
            "dataset.get_artifact", b"{}", filename="query.json", media_type="application/json",
            provider_id=provider,
            parameters={
                "dataset_id": args.id,
                "artifact_path": args.path,
                "revision": args.revision,
            },
        )
        doc = result.document
        payload = bytes.fromhex(doc["payload"])
        if args.output:
            args.output.write_bytes(payload)
            print(f"artifact saved to {args.output}")
        else:
            sys.stdout.buffer.write(payload)
        return EXIT_OK

    if cmd == "sample":
        result = executor.execute(
            "dataset.sample", b"{}", filename="query.json", media_type="application/json",
            provider_id=provider,
            parameters={
                "dataset_id": args.id,
                "split": args.split,
                "revision": args.revision,
                "n": args.n,
            },
        )
        _print_json(result.document, args)
        return EXIT_OK

    if cmd == "sync":
        result = executor.execute(
            "dataset.sync", b"{}", filename="query.json", media_type="application/json",
            provider_id=provider,
            parameters={
                "dataset_id": args.id,
                "revision": args.revision,
                "split": args.split,
            },
        )
        _print_json(result.document, args)
        return EXIT_OK

    print(f"unknown dataset command: {cmd}", file=sys.stderr)
    return EXIT_USAGE


def _print_json(data: Any, args: argparse.Namespace) -> None:
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def _media_type(document: Path) -> str:
    media_type, _ = mimetypes.guess_type(document.name)
    return media_type or "application/octet-stream"


def _parse_parameters(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidInputError(f"--parameters must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise InvalidInputError("--parameters must be a JSON object")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
