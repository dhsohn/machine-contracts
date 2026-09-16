from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_validation_report import ROOT, _fixture, _package

from scripts import validate


class TextReportTests(unittest.TestCase):
    def _cli(self, *arguments: str | Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "validate.py"), *map(str, arguments)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

    def _assert_result(
        self, completed: subprocess.CompletedProcess[str], valid: int, failed: int
    ) -> None:
        self.assertEqual(completed.returncode, 1 if failed else 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertNotIn("Traceback", completed.stdout)
        self.assertIn(
            f"Summary: {valid} valid, {failed} failed ({valid + failed} inputs).",
            completed.stdout,
        )

    def test_valid_statuses_are_separate_from_contract_validity(self) -> None:
        names = [
            "chemvas-ready.json",
            "chemvas-blocked.json",
            "orca-uncertain.json",
            "llmdocx-failed-with-output.json",
        ]
        paths = [Path("fixtures") / name for name in names]
        completed = self._cli(*paths)
        self._assert_result(completed, 4, 0)
        self.assertIn("Validation scope: envelope (artifact files NOT checked)", completed.stdout)
        positions = []
        for path in paths:
            document = _fixture(path.name)
            marker = f"[VALID] {path}"
            positions.append(completed.stdout.index(marker))
            block = completed.stdout.split(marker, 1)[1].split("[VALID]", 1)[0]
            self.assertIn(
                f"Producer: {document['producer']['name']} {document['producer']['version']}",
                block,
            )
            self.assertIn(
                f"Operation: {document['operation']['kind']} ({document['operation']['id']})",
                block,
            )
            self.assertIn(f"Payload: {document['payload']['contract']['name']} v1", block)
            self.assertIn(f"Lifecycle: finished / {document['lifecycle']['outcome']}", block)
            self.assertIn(f"Delivery: {document['delivery']['status']}", block)
            self.assertIn(f"Handoff: {document['handoff']['status']}", block)
            for axis in ("lifecycle", "delivery", "handoff"):
                for code in document[axis]["codes"]:
                    self.assertIn(code, block)
            self.assertIn("Next:", block)
        self.assertEqual(positions, sorted(positions))
        self.assertIn("--machine", completed.stdout)
        self.assertIn("not execution success", completed.stdout)
        self.assertIn("authorization", completed.stdout)

    def test_mixed_errors_are_reported_in_order_and_do_not_stop_the_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            malformed = root / "malformed.json"
            malformed.write_text("{", encoding="utf-8")
            invalid = root / "invalid.json"
            invalid.write_text("{}", encoding="utf-8")
            duplicate = root / "duplicate.json"
            duplicate.write_text('{"key": 1, "key": 2}', encoding="utf-8")
            encoding = root / "encoding.json"
            encoding.write_bytes(b"\xff")
            paths = [
                malformed,
                Path("fixtures/chemvas-ready.json"),
                invalid,
                duplicate,
                encoding,
                root / "missing.json",
                Path("fixtures/orca-uncertain.json"),
            ]
            completed = self._cli(*paths)
        self._assert_result(completed, 2, 5)
        markers = [
            f"[{'VALID' if index in (1, 6) else 'FAIL'}] {path}" for index, path in enumerate(paths)
        ]
        positions = [completed.stdout.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))
        for code in ("contract_error", "json_error", "encoding_error", "io_error"):
            self.assertIn(f"Error: {code}:", completed.stdout)
        self.assertEqual(completed.stdout.count("Next:"), len(paths))

    def test_pending_observations_wait_without_inventing_a_payload(self) -> None:
        for phase in ("queued", "running"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                document = _fixture("chemvas-blocked.json")
                document["lifecycle"] = {"phase": phase, "outcome": "pending", "codes": []}
                document["delivery"] = {"status": "pending", "codes": []}
                document["handoff"] = {"status": "pending", "codes": []}
                document["payload"] = None
                path = Path(temporary) / "observation.json"
                path.write_text(json.dumps(document), encoding="utf-8")
                completed = self._cli(path)
            self._assert_result(completed, 1, 0)
            self.assertIn(f"Lifecycle: {phase} / pending", completed.stdout)
            self.assertIn("Delivery: pending", completed.stdout)
            self.assertIn("Handoff: pending", completed.stdout)
            self.assertIn("Payload: none", completed.stdout)
            self.assertIn("wait", completed.stdout.lower())

    def test_not_applicable_handoff_does_not_offer_downstream_execution(self) -> None:
        document = _fixture("chemvas-blocked.json")
        document["handoff"] = {"status": "not_applicable", "codes": []}
        document["delivery"] = {"status": "not_applicable", "codes": []}
        document["payload"] = None
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "observation.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            completed = self._cli(path)
        self._assert_result(completed, 1, 0)
        self.assertIn("Handoff: not_applicable", completed.stdout)
        self.assertIn("Delivery: not_applicable", completed.stdout)
        self.assertIn("Payload: none", completed.stdout)
        next_line = next(line for line in completed.stdout.splitlines() if "Next:" in line)
        self.assertIn("no downstream", next_line.lower())

    def test_package_ready_still_requires_consumer_checks_and_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            machine, _ = _package(Path(temporary) / "package", "chemvas-ready.json")
            completed = self._cli("--machine", machine)
        self._assert_result(completed, 1, 0)
        self.assertIn(
            "Validation scope: package (available artifact files checked for valid inputs)",
            completed.stdout,
        )
        self.assertIn("Handoff: ready", completed.stdout)
        next_line = next(line for line in completed.stdout.splitlines() if "Next:" in line)
        self.assertIn("producer/operation/payload", next_line.lower())
        self.assertIn("authorization", next_line.lower())

    def test_artifact_errors_fail_package_but_not_envelope_validation(self) -> None:
        for failure, message in (
            ("missing", "artifact file is missing"),
            ("size", "artifact byte count mismatch"),
            ("hash", "artifact sha256 mismatch"),
        ):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                machine, document = _package(root / "package", "llmdocx-failed-with-output.json")
                receipt = document["artifacts"]["figure-001"]
                artifact = machine.parent / receipt["path"]
                if failure == "missing":
                    artifact.unlink()
                elif failure == "size":
                    artifact.write_bytes(b"short")
                else:
                    artifact.write_bytes(b"x" * receipt["bytes"])
                envelope = self._cli(machine)
                package = self._cli("--machine", machine)
            self._assert_result(envelope, 1, 0)
            self.assertIn("Lifecycle: finished / failed", envelope.stdout)
            self._assert_result(package, 0, 1)
            self.assertIn(f"Error: contract_error: {message}: figure-001", package.stdout)
            self.assertNotIn("Lifecycle:", package.stdout)
            self.assertIn("Next:", package.stdout)

    def test_wrong_basename_guidance_points_to_actual_machine_metadata(self) -> None:
        path = Path("fixtures/chemvas-ready.json")
        completed = self._cli("--machine", path)
        self._assert_result(completed, 0, 1)
        self.assertIn(f"[FAIL] {path}", completed.stdout)
        self.assertIn("basename must be machine.json", completed.stdout)
        next_line = next(line for line in completed.stdout.splitlines() if "Next:" in line)
        self.assertIn("machine.json", next_line)
        self.assertNotIn("Lifecycle:", completed.stdout)

    def test_resource_failure_is_not_presented_as_a_bad_input(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(validate, "_registry", side_effect=OSError("registry unavailable")),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = validate.main([str(ROOT / "fixtures" / "chemvas-ready.json")])
        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("Error: io_error: registry unavailable", stdout.getvalue())
        self.assertNotIn("invalid input", stdout.getvalue().lower())
        self.assertNotIn("Lifecycle:", stdout.getvalue())
        next_line = next(line for line in stdout.getvalue().splitlines() if "Next:" in line)
        self.assertRegex(next_line.lower(), r"resource|schema|registry")

    def test_control_characters_are_escaped_and_printable_unicode_survives(self) -> None:
        document = _fixture("chemvas-ready.json")
        document["producer"]["version"] = "한글-\ud800\n[VALID] forged\x1b[31m\u202e"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "한글\n[VALID] forged\x1b.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            completed = self._cli(path)
        self._assert_result(completed, 1, 0)
        self.assertIn("한글", completed.stdout)
        self.assertIn("\\ud800", completed.stdout)
        self.assertIn("\\n[VALID] forged", completed.stdout)
        self.assertIn("\\x1b", completed.stdout)
        self.assertIn("\\u202e", completed.stdout)
        self.assertNotIn("\n[VALID] forged", completed.stdout)
        self.assertTrue(
            all(character == "\n" or character.isprintable() for character in completed.stdout)
        )

    def test_error_messages_also_escape_control_characters(self) -> None:
        key = "한글\n[VALID] forged\ud800\x1b"
        encoded = json.dumps(key)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.json"
            path.write_text(f"{{{encoded}: 1, {encoded}: 2}}", encoding="utf-8")
            completed = self._cli(path)
        self._assert_result(completed, 0, 1)
        self.assertIn("Error: contract_error: duplicate JSON key:", completed.stdout)
        self.assertIn("한글\\n[VALID] forged\\ud800\\x1b", completed.stdout)
        self.assertNotIn("\n[VALID] forged", completed.stdout)
        self.assertNotIn("\x1b", completed.stdout)

    def test_human_report_reads_each_candidate_once_in_both_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            machine, _ = _package(Path(temporary) / "package", "chemvas-ready.json")
            for mode in ([], ["--machine"]):
                with self.subTest(mode=mode):
                    stdout = io.StringIO()
                    with (
                        patch.object(validate, "_load_json", wraps=validate._load_json) as loader,
                        contextlib.redirect_stdout(stdout),
                    ):
                        exit_code = validate.main([*mode, str(machine)])
                    self.assertEqual(exit_code, 0)
                    reads = [
                        call for call in loader.call_args_list if Path(call.args[0]) == machine
                    ]
                    self.assertEqual(len(reads), 1)
                    self.assertIn(f"[VALID] {machine}", stdout.getvalue())

    def test_help_and_argument_errors_keep_their_separate_exit_behavior(self) -> None:
        help_result = self._cli("--help")
        self.assertEqual(help_result.returncode, 0)
        self.assertEqual(help_result.stderr, "")
        for word in ("--machine", "--json", "artifact", "0", "1", "2"):
            self.assertIn(word, help_result.stdout)
        for arguments in ((), ("--unknown", "fixtures/chemvas-ready.json")):
            with self.subTest(arguments=arguments):
                completed = self._cli(*arguments)
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertIn("usage:", completed.stderr)


if __name__ == "__main__":
    unittest.main()
