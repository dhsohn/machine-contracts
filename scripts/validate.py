from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


class ContractError(ValueError):
    pass


REQUIREMENT_OPERATORS = frozenset({"equals", "not_null"})


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_object)
    if not isinstance(payload, dict):
        raise ContractError(f"{path}: expected a JSON object")
    return payload


def _registry() -> dict[str, Any]:
    return _load_json(ROOT / "registry.json")


def _payload_registry(registry: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    return {
        (str(item["name"]), int(item["version"])): item for item in registry["payload_contracts"]
    }


def _validate_schema(instance: dict[str, Any], schema_path: Path) -> None:
    schema = _load_json(schema_path)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(instance),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if not errors:
        return
    first = errors[0]
    location = "/".join(str(part) for part in first.absolute_path) or "<root>"
    raise ContractError(f"{schema_path.name}:{location}: {first.message}")


def _value_at(data: Any, path: list[str], *, location: str) -> Any:
    current = data
    for part in path:
        if not isinstance(current, dict) or part not in current:
            joined = "/".join(path)
            raise ContractError(f"{location} references missing payload path: {joined}")
        current = current[part]
    return current


def _values_at(data: Any, path: list[str], *, location: str) -> list[Any]:
    if not path:
        return [data]
    part, *remaining = path
    if part == "*":
        if not isinstance(data, list):
            raise ContractError(f"{location} wildcard expects an array")
        values: list[Any] = []
        for item in data:
            values.extend(_values_at(item, remaining, location=location))
        return values
    if not isinstance(data, dict) or part not in data:
        joined = "/".join(path)
        raise ContractError(f"{location} references missing payload path: {joined}")
    return _values_at(data[part], remaining, location=location)


def _check_requirements(
    data: dict[str, Any], requirements: list[dict[str, Any]], *, location: str
) -> None:
    for requirement in requirements:
        path = [str(part) for part in requirement["path"]]
        value = _value_at(data, path, location=location)
        operator = requirement["operator"]
        joined = "/".join(path)
        if operator == "equals":
            if value != requirement.get("value"):
                raise ContractError(f"{location} requires {joined}={requirement.get('value')!r}")
        elif operator == "not_null":
            if value is None:
                raise ContractError(f"{location} requires non-null {joined}")
        else:
            raise ContractError(f"{location} uses an unimplemented operator: {operator}")


def _reference_values(
    data: dict[str, Any], descriptor: dict[str, Any], *, location: str
) -> set[str]:
    values = _values_at(
        data,
        [str(part) for part in descriptor["path"]],
        location=location,
    )
    references: list[Any] = []
    if descriptor["many"]:
        for value in values:
            if not isinstance(value, list):
                raise ContractError(f"{location} expects an artifact id array")
            references.extend(value)
    else:
        references.extend(values)
    if any(not isinstance(item, str) or not item for item in references):
        raise ContractError(f"{location} contains an invalid artifact id")
    return set(references)


def _validate_artifact_references(
    data: dict[str, Any], payload_entry: dict[str, Any], artifacts: dict[str, Any]
) -> None:
    contract_name = str(payload_entry["name"])
    reference_contract = payload_entry.get("artifact_references")
    if reference_contract is None:
        return
    declared = _reference_values(
        data,
        reference_contract["declared"],
        location=f"{contract_name}.artifact_references.declared",
    )
    missing = sorted(declared - set(artifacts))
    if missing:
        raise ContractError(f"payload references unknown artifacts: {', '.join(missing)}")
    nested: set[str] = set()
    for descriptor in reference_contract["nested"]:
        nested.update(
            _reference_values(
                data,
                descriptor,
                location=f"{contract_name}.artifact_references.nested",
            )
        )
    unlisted = sorted(nested - declared)
    if unlisted:
        raise ContractError(
            f"payload contains unlisted nested artifact refs: {', '.join(unlisted)}"
        )


def _validate_semantics(document: dict[str, Any]) -> None:
    lifecycle = document["lifecycle"]
    handoff = document["handoff"]
    delivery = document["delivery"]
    artifacts = document["artifacts"]
    payload = document["payload"]

    if lifecycle["phase"] == "finished" and handoff["status"] == "pending":
        raise ContractError("finished observations cannot have pending handoff")
    if lifecycle["phase"] == "finished" and delivery["status"] == "pending":
        raise ContractError("finished observations cannot have pending delivery")
    if lifecycle["outcome"] != "succeeded" and handoff["status"] == "ready":
        raise ContractError("only succeeded observations can be ready")

    required_statuses = [receipt["status"] for receipt in artifacts.values() if receipt["required"]]
    if delivery["status"] == "complete" and any(
        status != "available" for status in required_statuses
    ):
        raise ContractError("complete delivery contains an unavailable required artifact")
    if delivery["status"] == "incomplete" and not any(
        status != "available" for status in required_statuses
    ):
        raise ContractError("incomplete delivery needs an unavailable required artifact")

    upstream_keys = [
        (item["producer"]["name"], item["operation_id"], item["byte_sha256"])
        for item in document["lineage"]["upstream"]
    ]
    if len(upstream_keys) != len(set(upstream_keys)):
        raise ContractError("lineage contains a duplicate upstream envelope")

    registry = _registry()
    producer_operation = (
        document["producer"]["name"],
        document["operation"]["kind"],
    )
    matching_routes = [
        route
        for route in registry["routes"]
        if (route["producer"], route["operation_kind"]) == producer_operation
    ]
    if not matching_routes:
        raise ContractError("producer and operation route is not registered")

    if payload is None:
        if handoff["status"] == "ready":
            raise ContractError("ready handoff needs a payload")
        return

    contract = payload["contract"]
    payload_key = (contract["name"], contract["version"])
    payload_entry = _payload_registry(registry).get(payload_key)
    if payload_entry is None:
        raise ContractError("payload contract is not registered")
    route = next(
        (
            item
            for item in matching_routes
            if (item["payload_contract"], item["payload_version"]) == payload_key
        ),
        None,
    )
    if route is None:
        raise ContractError("producer, operation, and payload route is not registered")

    payload_schema = ROOT / str(payload_entry["schema"])
    _validate_schema(payload["data"], payload_schema)
    _check_requirements(
        payload["data"],
        route["requirements"],
        location="route",
    )
    _validate_artifact_references(payload["data"], payload_entry, artifacts)
    if handoff["status"] == "ready":
        _check_requirements(
            payload["data"],
            payload_entry["ready_requirements"],
            location=f"ready {contract['name']}",
        )


def validate_document(document: dict[str, Any]) -> None:
    _validate_schema(document, ROOT / "schemas" / "machine-observation-v1.schema.json")
    _validate_semantics(document)


def validate_path(path: str | Path) -> None:
    validate_document(_load_json(Path(path)))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_path(root: Path, raw: str) -> Path:
    relative = PurePosixPath(raw)
    target = (root / Path(*relative.parts)).resolve()
    try:
        if os.path.commonpath((str(root), str(target))) != str(root):
            raise ContractError(f"artifact path escapes the generation: {raw}")
    except ValueError as exc:
        raise ContractError(f"artifact path is on another volume: {raw}") from exc
    return target


def validate_machine_path(path: str | Path) -> None:
    candidate = Path(path).resolve()
    if candidate.name != "machine.json":
        raise ContractError("public machine metadata basename must be machine.json")
    document = _load_json(candidate)
    validate_document(document)
    root = candidate.parent
    for artifact_id, receipt in document["artifacts"].items():
        if receipt["status"] != "available":
            continue
        target = _artifact_path(root, receipt["path"])
        if not target.is_file():
            raise ContractError(f"artifact file is missing: {artifact_id}")
        if target.stat().st_size != receipt["bytes"]:
            raise ContractError(f"artifact byte count mismatch: {artifact_id}")
        if _sha256(target) != receipt["byte_sha256"]:
            raise ContractError(f"artifact sha256 mismatch: {artifact_id}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="validate machine observation envelopes")
    parser.add_argument(
        "--machine",
        action="store_true",
        help="require machine.json basename and verify available artifact bytes",
    )
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    for candidate in args.paths:
        if args.machine:
            validate_machine_path(candidate)
        else:
            validate_path(candidate)
        print(f"ok: {candidate}")


if __name__ == "__main__":
    main()
