from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.validate import (  # noqa: E402
    ContractError,
    validate_document,
    validate_machine_path,
    validate_path,
)


def _fixture(name: str) -> dict[str, object]:
    return json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))


class MachineObservationFixtureTests(unittest.TestCase):
    def test_all_examples_conform(self) -> None:
        fixtures = sorted((ROOT / "fixtures").glob("*.json"))
        self.assertGreaterEqual(len(fixtures), 6)
        for fixture in fixtures:
            with self.subTest(fixture=fixture.name):
                validate_path(fixture)

    def test_registry_payload_keys_match_their_schemas(self) -> None:
        registry = _fixture("../registry.json")
        payloads = registry["payload_contracts"]
        for payload in payloads:
            with self.subTest(payload=payload["name"]):
                schema = json.loads((ROOT / payload["schema"]).read_text(encoding="utf-8"))
                self.assertEqual(payload["required_keys"], schema["required"])

    def test_wrong_producer_operation_payload_route_is_rejected(self) -> None:
        document = _fixture("llmdocx-dry-run.json")
        document["producer"]["name"] = "chemvas"
        with self.assertRaisesRegex(ContractError, "route"):
            validate_document(document)

    def test_patch_result_requires_applied_true(self) -> None:
        document = _fixture("llmdocx-dry-run.json")
        document["payload"]["contract"]["name"] = "document/patch-result"
        with self.assertRaisesRegex(ContractError, "applied"):
            validate_document(document)

    def test_ready_run_requires_ok_true(self) -> None:
        document = _fixture("llmdocx-failed-with-output.json")
        document["lifecycle"] = {"phase": "finished", "outcome": "succeeded", "codes": []}
        document["handoff"] = {"status": "ready", "codes": []}
        with self.assertRaisesRegex(ContractError, "ok=True"):
            validate_document(document)

    def test_nested_run_artifact_refs_must_be_declared(self) -> None:
        document = _fixture("llmdocx-failed-with-output.json")
        document["payload"]["data"]["artifact_refs"] = []
        with self.assertRaisesRegex(ContractError, "unlisted nested"):
            validate_document(document)

    def test_orca_result_kind_must_match_operation_route(self) -> None:
        document = _fixture("orca-completed.json")
        document["payload"]["data"]["result_kind"] = "workflow"
        with self.assertRaisesRegex(ContractError, "result_kind='engine-run'"):
            validate_document(document)

    def test_machine_validation_checks_basename_and_artifact_bytes(self) -> None:
        document = copy.deepcopy(_fixture("llmdocx-failed-with-output.json"))
        artifact = b"verified figure bytes\n"
        receipt = document["artifacts"]["figure-001"]
        receipt["bytes"] = len(artifact)
        receipt["byte_sha256"] = hashlib.sha256(artifact).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / receipt["path"]).write_bytes(artifact)
            machine = root / "machine.json"
            machine.write_text(json.dumps(document), encoding="utf-8")

            validate_machine_path(machine)
            with self.assertRaisesRegex(ContractError, "basename"):
                validate_machine_path(root / "observation.json")

            (root / receipt["path"]).write_bytes(b"changed\n")
            with self.assertRaisesRegex(ContractError, "byte count|sha256"):
                validate_machine_path(machine)

    def test_finished_observation_cannot_remain_pending(self) -> None:
        document = _fixture("chemvas-ready.json")
        document["handoff"]["status"] = "pending"
        with self.assertRaisesRegex(ContractError, "pending"):
            validate_document(document)


if __name__ == "__main__":
    unittest.main()
