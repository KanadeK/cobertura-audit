import contextlib
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from cobertura_audit.cli import main
from cobertura_audit.core import InputError, audit, audit_file, parse_xml

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def valid():
    return parse_xml(EXAMPLES / "valid.xml")


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.path = self.base / "input.xml"

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")
        return self.path

    def test_valid_real_examples(self):
        result = audit_file(EXAMPLES / "valid.xml", min_line_rate="0.6")
        self.assertTrue(result["passed"])
        self.assertEqual(result["computed"]["lines_covered"], 2)
        self.assertEqual(result["computed"]["lines_valid"], 3)
        self.assertFalse(audit_file(EXAMPLES / "inflated.xml")["passed"])

    def test_threshold_uses_exact_counts(self):
        self.assertTrue(audit(valid(), min_line_rate="0.6666")["passed"])
        self.assertIn("below_threshold", audit(valid(), min_line_rate="0.6667")["codes"])
        for bad in ["80", "-1", "NaN", "Infinity", "invalid"]:
            with self.subTest(bad=bad), self.assertRaises(InputError):
                audit(valid(), min_line_rate=bad)

    def test_class_package_root_rates(self):
        for path in [".", "./packages/package", "./packages/package/classes/class"]:
            root = valid()
            root.find(path).set("line-rate", "1")
            result = audit(root)
            self.assertIn("summary_rate_mismatch", result["codes"])

    def test_summary_counters_optional_and_required(self):
        root = valid()
        cls = root.find("./packages/package/classes/class")
        cls.set("lines-valid", "3")
        cls.set("lines-covered", "2")
        self.assertTrue(audit(root)["passed"])
        cls.set("lines-covered", "3")
        self.assertIn("summary_count_mismatch", audit(root)["codes"])
        for attribute in ["line-rate", "lines-covered", "lines-valid"]:
            root = valid()
            del root.attrib[attribute]
            self.assertIn("missing_summary", audit(root)["codes"])

    def test_invalid_summary_values(self):
        for attribute, value, code in [
            ("line-rate", "NaN", "invalid_summary_rate"),
            ("line-rate", "bad", "invalid_summary_rate"),
            ("line-rate", "1e999999", "invalid_summary_rate"),
            ("lines-valid", "-1", "invalid_summary_count"),
            ("lines-covered", "1.0", "invalid_summary_count"),
        ]:
            root = valid()
            root.set(attribute, value)
            self.assertIn(code, audit(root)["codes"])

    def test_invalid_observations(self):
        for attribute, value in [
            ("number", "0"),
            ("number", "x"),
            ("hits", "-1"),
            ("hits", "1.5"),
            ("hits", "9" * 13),
        ]:
            root = valid()
            root.find(".//class/lines/line").set(attribute, value)
            self.assertIn("invalid_line_observation", audit(root)["codes"])
        root = valid()
        del root.find(".//class/lines/line").attrib["hits"]
        self.assertIn("invalid_line_observation", audit(root)["codes"])

    def test_duplicate_within_class(self):
        root = valid()
        lines = root.find(".//class/lines")
        lines.append(copy.deepcopy(lines[0]))
        result = audit(root)
        self.assertIn("duplicate_class_line", result["codes"])
        self.assertEqual(result["computed"]["lines_valid"], 3)

    def test_cross_class_overlap_never_passes(self):
        root = valid()
        classes = root.find(".//classes")
        classes.append(copy.deepcopy(classes[0]))
        result = audit(root)
        self.assertEqual(result["codes"]["overlapping_source_line"], 3)
        self.assertFalse(result["passed"])
        self.assertEqual(result["computed"]["lines_covered"], 2)

    def test_disjoint_classes_in_same_file(self):
        root = valid()
        classes = root.find(".//classes")
        second = copy.deepcopy(classes[0])
        second.find("./lines").remove(second.find("./lines")[0])
        second.find("./lines").remove(second.find("./lines")[1])
        second.set("line-rate", "0")
        first = classes[0]
        first.find("./lines").remove(first.find("./lines")[1])
        first.set("line-rate", "1")
        classes.append(second)
        self.assertTrue(audit(root)["passed"])

    def test_path_normalization_and_method_lines(self):
        root = valid()
        classes = root.find(".//classes")
        second = copy.deepcopy(classes[0])
        second.set("filename", ".\\folder\\..\\app.py")
        classes.append(second)
        self.assertIn("overlapping_source_line", audit(root)["codes"])
        root = valid()
        cls = root.find(".//class")
        method = ET.SubElement(cls.find("methods"), "method")
        method.append(copy.deepcopy(cls.find("lines")))
        self.assertTrue(audit(root)["passed"])

    def test_rounding_boundary(self):
        root = valid()
        root.set("line-rate", "0.6666")
        self.assertTrue(audit(root)["passed"])
        root.set("line-rate", "0.6665")
        self.assertIn("summary_rate_mismatch", audit(root)["codes"])

    def test_empty_report_is_failure(self):
        self.write(
            '<coverage line-rate="1" lines-valid="0" lines-covered="0"><packages/></coverage>'
        )
        self.assertIn("no_measurable_lines", audit_file(self.path)["codes"])
        root = valid()
        root.find(".//class/lines").clear()
        for element in [root, root.find(".//package"), root.find(".//class")]:
            element.set("line-rate", "1")
        root.set("lines-valid", "0")
        root.set("lines-covered", "0")
        self.assertEqual(audit(root)["findings_count"], 1)

    def test_structure_and_limits(self):
        for text in [
            "",
            "<bad>",
            "<report/>",
            '<coverage xmlns="urn:test"/>',
            "<coverage/>",
            "<coverage><packages/><packages/></coverage>",
        ]:
            self.write(text)
            with self.subTest(text=text), self.assertRaises(InputError):
                audit_file(self.path)
        root = valid()
        del root.find(".//class").attrib["filename"]
        with self.assertRaises(InputError):
            audit(root)
        for options in [{"max_bytes": 1}, {"max_nodes": 2}, {"max_nodes": 0}]:
            with self.assertRaises(InputError):
                audit_file(EXAMPLES / "valid.xml", **options)
        for options in [{"file_limit": -1}, {"finding_limit": True}]:
            with self.assertRaises(InputError):
                audit(valid(), **options)

    def test_entities_and_encodings_rejected(self):
        for text in [
            '<!DOCTYPE coverage [<!ENTITY e "secret">]><coverage>&e;</coverage>',
            "<!DOCTYPE coverage [<!ELEMENT coverage ANY>]><coverage/>",
            '<!DOCTYPE coverage [<!ENTITY e SYSTEM "file:///sensitive">]><coverage>&e;</coverage>',
            '<?xml version="1.0" encoding="UTF-16"?><coverage/>',
            "<coverage>\x00</coverage>",
        ]:
            with self.subTest(text=text), self.assertRaises(InputError):
                parse_xml(self.write(text))
        self.path.write_bytes(b"\xff")
        with self.assertRaises(InputError):
            parse_xml(self.path)
        self.write(
            '<!DOCTYPE coverage SYSTEM "https://invalid.example/never-fetch.dtd">'
            + (EXAMPLES / "valid.xml").read_text(encoding="utf-8").split("?>", 1)[1]
        )
        self.assertTrue(audit_file(self.path)["passed"])

    def test_bounded_report_and_no_source_reads(self):
        root = valid()
        cls = root.find(".//class")
        cls.set("filename", "/nonexistent/example/app.py")
        self.assertTrue(audit(root)["passed"])
        root.set("line-rate", "1")
        root.set("lines-covered", "3")
        result = audit(root, finding_limit=1, file_limit=0)
        self.assertEqual(result["findings_count"], 2)
        self.assertTrue(result["findings_truncated"])
        self.assertEqual(result["files"], [])
        self.assertTrue(result["files_truncated"])

    def test_declaration_whitespace_and_quoted_dtd_delimiters(self):
        for text in [
            '<?xml version="1.0" encoding = "UTF-16"?><coverage/>',
            '<!DOCTYPE coverage SYSTEM "https://invalid.example/x>y" '
            "[<!ELEMENT coverage ANY>]><coverage/>",
            '<!DOCTYPE coverage SYSTEM "unterminated',
        ]:
            with self.subTest(text=text), self.assertRaises(InputError):
                parse_xml(self.write(text))
        content = (EXAMPLES / "valid.xml").read_text(encoding="utf-8").split("?>", 1)[1]
        self.write("<!DOCTYPE coverage SYSTEM 'https://invalid.example/x[y'>" + content)
        self.assertTrue(audit_file(self.path)["passed"])

    def test_cli_and_input_preservation(self):
        path = EXAMPLES / "valid.xml"
        digest = hashlib.sha256(path.read_bytes()).digest()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(main([str(path), "--json"]), 0)
            self.assertEqual(main([str(path)]), 0)
            self.assertEqual(main([str(path), "--min-line-rate", "0.9"]), 1)
            self.assertEqual(main([str(path), "--finding-limit", "0", "--min-line-rate", "0.9"]), 1)
            self.assertEqual(main([str(path), "--min-line-rate", "NaN"]), 2)
            self.assertEqual(main([str(self.base / "missing")]), 2)
        self.assertIn('"schema_version": 1', out.getvalue())
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), digest)

    def test_real_coverage_py_producer(self):
        script = self.base / "sample.py"
        script.write_text(
            "def classify(x):\n    if x:\n        return 1\n    return 0\n\nclassify(True)\n",
            encoding="utf-8",
        )
        environment = {k: v for k, v in os.environ.items() if not k.startswith("COVERAGE_")}
        commands = [
            ["run", "--branch", str(script)],
            ["xml", "-o", "actual.xml"],
            ["json", "-o", "actual.json"],
        ]
        for args in commands:
            result = subprocess.run(
                [sys.executable, "-m", "coverage", *args],
                cwd=self.base,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        report = audit_file(self.base / "actual.xml")
        producer = json.loads((self.base / "actual.json").read_text(encoding="utf-8"))["totals"]
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["computed"]["lines_valid"], producer["num_statements"])
        self.assertEqual(report["computed"]["lines_covered"], producer["covered_lines"])


if __name__ == "__main__":
    unittest.main()
