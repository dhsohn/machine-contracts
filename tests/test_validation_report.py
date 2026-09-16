from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import validate  # noqa: E402


def _fixture(name: str) -> dict:
    return json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))


def _package(root: Path, fixture: str) -> tuple[Path, dict]:
    """Materialize synthetic receipts; these are not actual producer outputs."""
    root.mkdir()
    document = _fixture(fixture)
    if not document["artifacts"]:
        document["artifacts"]["geometry"] = {
            "status": "available",
            "required": True,
            "role": "geometry",
            "path": "geometry.xyz",
            "media_type": "chemical/x-xyz",
            "bytes": 0,
            "byte_sha256": "0" * 64,
        }
    for artifact_id, receipt in document["artifacts"].items():
        content = f"synthetic bytes for {artifact_id}\n".encode()
        artifact = root / receipt["path"]
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(content)
        receipt["bytes"] = len(content)
        receipt["byte_sha256"] = hashlib.sha256(content).hexdigest()
    machine = root / "machine.json"
    machine.write_text(json.dumps(document), encoding="utf-8")
    return machine, document


class ValidationReportTests(unittest.TestCase):
    def _cli(self, *arguments: str | Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "validate.py"), *map(str, arguments)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

    def _report(self, *arguments: str | Path, exit_code: int = 0) -> dict:
        completed = self._cli("--json", *arguments)
        self.assertEqual(completed.returncode, exit_code, completed.stderr)
        self.assertEqual(completed.stderr, "")
        report = json.loads(completed.stdout)
        self.assertEqual(set(report), {"report_version", "scope", "valid", "results"})
        self.assertEqual(report["report_version"], 1)
        self.assertIs(report["valid"], exit_code == 0)
        for result in report["results"]:
            self.assertEqual(set(result), {"path", "valid", "observation", "error"})
        return report

    def _assert_observation(self, result: dict, document: dict) -> None:
        expected = {
            name: document[name]
            for name in ("contract", "producer", "operation", "lifecycle", "handoff", "delivery")
        }
        payload = document["payload"]
        expected["payload_contract"] = payload["contract"] if payload is not None else None
        self.assertIs(result["valid"], True)
        self.assertIsNone(result["error"])
        self.assertEqual(result["observation"], expected)

    def _assert_error(self, result: dict, code: str) -> None:
        self.assertIs(result["valid"], False)
        self.assertIsNone(result["observation"])
        self.assertEqual(set(result["error"]), {"code", "message"})
        self.assertEqual(result["error"]["code"], code)
        self.assertIsInstance(result["error"]["message"], str)
        self.assertTrue(result["error"]["message"])

    def test_all_fixtures_report_valid_even_when_not_ready_or_successful(self) -> None:
        paths = [
            Path("fixtures") / path.name for path in sorted((ROOT / "fixtures").glob("*.json"))
        ]
        report = self._report(*paths)
        self.assertEqual(report["scope"], "envelope")
        self.assertEqual([item["path"] for item in report["results"]], list(map(str, paths)))
        for result, path in zip(report["results"], paths, strict=True):
            with self.subTest(fixture=path.name):
                self._assert_observation(result, _fixture(path.name))

    def test_null_payload_is_reported_without_inventing_a_contract(self) -> None:
        document = _fixture("chemvas-blocked.json")
        document["payload"] = None
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "observation.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            report = self._report(path)
        self._assert_observation(report["results"][0], document)

    def test_json_report_preserves_unicode_including_an_accepted_lone_surrogate(self) -> None:
        document = _fixture("chemvas-ready.json")
        document["producer"]["version"] = "한글-\ud800"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "observation.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            report = self._report(path)
        self._assert_observation(report["results"][0], document)

    def test_mixed_inputs_continue_after_each_known_failure_in_input_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            invalid = root / "invalid.json"
            invalid.write_text('{"unknown": true}', encoding="utf-8")
            malformed = root / "malformed.json"
            malformed.write_text("{", encoding="utf-8")
            duplicate = root / "duplicate.json"
            duplicate.write_text('{"key": 1, "key": 2}', encoding="utf-8")
            encoding = root / "encoding.json"
            encoding.write_bytes(b"\xff")
            ready = Path("fixtures/chemvas-ready.json")
            blocked = Path("fixtures/chemvas-blocked.json")
            paths = [ready, invalid, malformed, duplicate, encoding, root / "missing.json", blocked]
            report = self._report(*paths, exit_code=1)
        self.assertEqual([item["path"] for item in report["results"]], list(map(str, paths)))
        self._assert_observation(report["results"][0], _fixture(ready.name))
        self._assert_observation(report["results"][-1], _fixture(blocked.name))
        codes = ["contract_error", "json_error", "contract_error", "encoding_error", "io_error"]
        for result, code in zip(report["results"][1:-1], codes, strict=True):
            self._assert_error(result, code)

    def test_package_success_verifies_synthetic_bytes_for_three_products(self) -> None:
        fixtures = ["chemvas-ready.json", "orca-completed.json", "llmdocx-failed-with-output.json"]
        with tempfile.TemporaryDirectory() as temporary:
            packages = [
                _package(Path(temporary) / str(index), fixture)
                for index, fixture in enumerate(fixtures)
            ]
            report = self._report("--machine", *(path for path, _ in packages))
        self.assertEqual(report["scope"], "package")
        for result, (_, document) in zip(report["results"], packages, strict=True):
            self._assert_observation(result, document)

    def test_artifact_failures_do_not_change_envelope_only_verdict(self) -> None:
        for failure in ("length", "hash", "missing", "escape"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                machine, document = _package(root / "package", "llmdocx-failed-with-output.json")
                receipt = document["artifacts"]["figure-001"]
                artifact = machine.parent / receipt["path"]
                if failure == "length":
                    artifact.write_bytes(b"short")
                elif failure == "hash":
                    artifact.write_bytes(b"x" * receipt["bytes"])
                elif failure == "missing":
                    artifact.unlink()
                else:
                    outside = root / "outside.bin"
                    artifact.rename(outside)
                    artifact.symlink_to(outside)
                envelope = self._report(machine)
                self.assertEqual(envelope["scope"], "envelope")
                self._assert_observation(envelope["results"][0], document)
                package = self._report("--machine", machine, exit_code=1)
                self._assert_error(package["results"][0], "contract_error")

    def test_package_mode_rejects_wrong_basename(self) -> None:
        path = Path("fixtures/chemvas-ready.json")
        report = self._report("--machine", path, exit_code=1)
        self._assert_error(report["results"][0], "contract_error")
        self.assertIn("basename", report["results"][0]["error"]["message"])

    def test_report_reads_each_candidate_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            machine, document = _package(Path(temporary) / "package", "chemvas-ready.json")
            for mode in ([], ["--machine"]):
                with self.subTest(mode=mode):
                    stdout = io.StringIO()
                    with (
                        patch.object(validate, "_load_json", wraps=validate._load_json) as loader,
                        contextlib.redirect_stdout(stdout),
                    ):
                        exit_code = validate.main(["--json", *mode, str(machine)])
                    self.assertEqual(exit_code, 0)
                    candidate_reads = [
                        call for call in loader.call_args_list if Path(call.args[0]) == machine
                    ]
                    self.assertEqual(len(candidate_reads), 1)
                    self._assert_observation(json.loads(stdout.getvalue())["results"][0], document)

    def test_resource_read_failure_is_reported_without_claiming_input_is_invalid(self) -> None:
        stdout = io.StringIO()
        with (
            patch.object(validate, "_registry", side_effect=OSError("registry unavailable")),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = validate.main(["--json", str(ROOT / "fixtures" / "chemvas-ready.json")])
        self.assertEqual(exit_code, 1)
        result = json.loads(stdout.getvalue())["results"][0]
        self._assert_error(result, "io_error")
        self.assertEqual(result["error"]["message"], "registry unavailable")

    def test_text_reports_validation_separately_from_execution_and_handoff(self) -> None:
        paths = ["fixtures/chemvas-blocked.json", "fixtures/orca-uncertain.json"]
        completed = self._cli(*paths)
        self.assertEqual(completed.returncode, 0)
        for path in paths:
            self.assertIn(f"[VALID] {path}", completed.stdout)
        self.assertIn("Lifecycle: finished / uncertain", completed.stdout)
        self.assertIn("Handoff: blocked", completed.stdout)
        self.assertIn("Summary: 2 valid, 0 failed (2 inputs).", completed.stdout)
        self.assertEqual(completed.stderr, "")
        failed = self._cli("--machine", paths[0])
        self.assertEqual(failed.returncode, 1)
        self.assertIn(f"[FAIL] {paths[0]}", failed.stdout)
        self.assertIn("Error: contract_error:", failed.stdout)
        self.assertIn("Next:", failed.stdout)
        self.assertNotIn("Traceback", failed.stdout)
        self.assertEqual(failed.stderr, "")

    def test_argument_errors_keep_standard_argparse_behavior(self) -> None:
        for arguments in (("--json",), ("--json", "--unknown", "fixtures/chemvas-ready.json")):
            with self.subTest(arguments=arguments):
                completed = self._cli(*arguments)
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertIn("usage:", completed.stderr)


if __name__ == "__main__":
    unittest.main()
