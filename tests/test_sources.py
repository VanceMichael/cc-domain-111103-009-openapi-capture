import json
import unittest
from pathlib import Path
from app.openapi import operation_names,read_document
class DomainTests(unittest.TestCase):
    def test_version(self): self.assertEqual(read_document("fixtures/v1.json")["openapi"],"3.0.3")
    def test_operation(self): self.assertEqual(operation_names(read_document("fixtures/v1.json")),["/notices GET"])
    def test_invalid(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as d:
            p=Path(d)/"x.json";p.write_text('{"openapi":"3","paths":[]}')
            with self.assertRaises(ValueError): read_document(p)

class FixtureTests(unittest.TestCase):
    def test_fixture_directory(self): self.assertTrue(Path("fixtures").is_dir())
