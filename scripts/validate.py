from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


class ContractError(ValueError):
    def __init__(self, *args: object, hint: str | None = None) -> None:
        super().__init__(*args)
        self.hint = hint


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
    _validated_path(path, machine=False)


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
            raise ContractError(
                f"artifact path escapes the generation: {raw}",
                hint="Do not consume this package; obtain artifacts contained in the generation.",
            )
    except ValueError as exc:
        raise ContractError(
            f"artifact path is on another volume: {raw}",
            hint="Do not consume this package; obtain artifacts contained in the generation.",
        ) from exc
    return target


def _validated_path(path: str | Path, *, machine: bool) -> dict[str, Any]:
    candidate = Path(path).resolve() if machine else Path(path)
    if machine and candidate.name != "machine.json":
        raise ContractError(
            "public machine metadata basename must be machine.json",
            hint="Pass the actual generation/machine.json, not a renamed envelope or fixture.",
        )
    document = _load_json(candidate)
    validate_document(document)
    if not machine:
        return document
    root = candidate.parent
    for artifact_id, receipt in document["artifacts"].items():
        if receipt["status"] != "available":
            continue
        target = _artifact_path(root, receipt["path"])
        if not target.is_file():
            raise ContractError(
                f"artifact file is missing: {artifact_id}",
                hint="Obtain the intact generation with its declared artifacts; preserve receipts.",
            )
        if target.stat().st_size != receipt["bytes"]:
            raise ContractError(
                f"artifact byte count mismatch: {artifact_id}",
                hint="Do not consume these bytes or rewrite receipts; obtain an intact generation.",
            )
        if _sha256(target) != receipt["byte_sha256"]:
            raise ContractError(
                f"artifact sha256 mismatch: {artifact_id}",
                hint="Do not consume these bytes or rewrite receipts; obtain an intact generation.",
            )
    return document


def validate_machine_path(path: str | Path) -> None:
    _validated_path(path, machine=True)


def _error_hint(error: Exception) -> str:
    if isinstance(error, ContractError):
        return error.hint or (
            "Inspect the reported rule against the pinned schemas/registry; "
            "do not change observed statuses just to pass."
        )
    if isinstance(error, json.JSONDecodeError):
        return "Check JSON syntax at the reported location in the input or validator resource."
    if isinstance(error, UnicodeError):
        return "Check that the reported input or validator resource is UTF-8 encoded."
    return (
        "Check the reported path, availability and permissions, "
        "including validator schema/registry resources."
    )


def _validation_result(path: Path, *, machine: bool) -> tuple[dict[str, Any], str | None]:
    result: dict[str, Any] = {
        "path": str(path),
        "valid": False,
        "observation": None,
        "error": None,
    }
    try:
        document = _validated_path(path, machine=machine)
    except (ContractError, json.JSONDecodeError, UnicodeError, OSError) as exc:
        if isinstance(exc, ContractError):
            code = "contract_error"
        elif isinstance(exc, json.JSONDecodeError):
            code = "json_error"
        elif isinstance(exc, UnicodeError):
            code = "encoding_error"
        else:
            code = "io_error"
        result["error"] = {"code": code, "message": str(exc)}
        return result, _error_hint(exc)

    result["valid"] = True
    result["observation"] = {
        key: document[key]
        for key in ("contract", "producer", "operation", "lifecycle", "handoff", "delivery")
    }
    payload = document["payload"]
    result["observation"]["payload_contract"] = None if payload is None else payload["contract"]
    return result, None


def _display(value: object) -> str:
    """Keep untrusted fields on one terminal line, including accepted lone surrogates."""
    return "".join(char if char.isprintable() else ascii(char)[1:-1] for char in str(value))


def _handoff_hint(status: str, *, machine: bool) -> str:
    if status == "blocked":
        return "Do not continue downstream; inspect the producer's handoff codes and evidence."
    if status == "pending":
        return "Wait for a terminal observation; do not consume a pending handoff."
    if status == "not_applicable":
        return "No downstream handoff is declared."
    if not machine:
        return "Artifact files are unverified; check the actual generation with --machine."
    return (
        "Readiness is declared; still check the expected producer/operation/payload, "
        "product acceptance and execution authorization."
    )


def _print_text_result(result: dict[str, Any], hint: str | None, *, machine: bool) -> None:
    print(f"[{'VALID' if result['valid'] else 'FAIL'}] {_display(result['path'])}")
    observation = result["observation"]
    if observation is None:
        error = result["error"]
        print(f"  Error: {error['code']}: {_display(error['message'])}")
        print(f"  Next: {_display(hint)}")
        return
    producer = observation["producer"]
    operation = observation["operation"]
    payload = observation["payload_contract"]
    print(f"  Producer: {_display(producer['name'])} {_display(producer['version'])}")
    print(f"  Operation: {_display(operation['kind'])} ({_display(operation['id'])})")
    payload_text = "none" if payload is None else f"{payload['name']} v{payload['version']}"
    print(f"  Payload: {_display(payload_text)}")
    for axis in ("lifecycle", "delivery", "handoff"):
        state = observation[axis]
        value = (
            f"{state['phase']} / {state['outcome'] or 'none'}"
            if axis == "lifecycle"
            else state["status"]
        )
        print(f"  {axis.capitalize()}: {_display(value)}")
        if state["codes"]:
            print(f"  {axis.capitalize()} codes: {_display(', '.join(state['codes']))}")
    print(f"  Next: {_handoff_hint(observation['handoff']['status'], machine=machine)}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate explicit machine observation paths without launching downstream work.",
        epilog=(
            "Default: readable results for every input; artifact files are NOT checked.\n"
            "Use --machine for package checks and --json for automation.\n"
            "Exit codes: 0 = all valid; 1 = validation/read failure; 2 = argument error.\n"
            "Validation is not execution success or authorization for downstream work.\n\n"
            "Example: python3 scripts/validate.py --machine path/to/generation/machine.json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--machine",
        action="store_true",
        help="require machine.json basename and verify available artifact bytes",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="report every input in one JSON document; valid does not imply ready handoff",
    )
    parser.add_argument(
        "paths", nargs="+", type=Path, help="explicit input paths, checked in order"
    )
    args = parser.parse_args(argv)
    checked = [_validation_result(candidate, machine=args.machine) for candidate in args.paths]
    results = [result for result, _ in checked]
    valid = all(result["valid"] for result in results)
    if args.json:
        print(
            json.dumps(
                {
                    "report_version": 1,
                    "scope": "package" if args.machine else "envelope",
                    "valid": valid,
                    "results": results,
                },
                sort_keys=True,
            )
        )
        return 0 if valid else 1
    scope = (
        "package (available artifact files checked for valid inputs)"
        if args.machine
        else "envelope (artifact files NOT checked)"
    )
    print(f"Validation scope: {scope}")
    for result, hint in checked:
        print()
        _print_text_result(result, hint, machine=args.machine)
    count = sum(result["valid"] for result in results)
    print(f"\nSummary: {count} valid, {len(results) - count} failed ({len(results)} inputs).")
    print("Validation is not execution success or authorization for downstream work.")
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
