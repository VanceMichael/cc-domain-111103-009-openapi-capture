import json
import socket
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from app.diff import diff_plans
from app.errors import PolicyError
from app.plan import build_plan, load_config
from app.refs import RefError, Resolver, find_cycles
from app.replay import ResponseBook, run_replay
from app.schema import validate
from app.serialize import encode_query, expand_path, serialize_query_pairs

FIXTURES = Path("fixtures")


def _plan(config_name):
    config, base = load_config(FIXTURES / config_name)
    return build_plan(config, base_dir=base)


def _loop_config(**extra):
    config = {
        "document": "loop.json",
        "document_version": "loop",
        "mapping_version": "m0",
        "allowed_operations": ["listThings"],
        "response_schemas": {"listThings": "Node"},
    }
    config.update(extra)
    return config


class RefTests(unittest.TestCase):
    def setUp(self):
        self.resolver = Resolver(FIXTURES / "loop.json")

    def test_cross_file_ref(self):
        node, doc = self.resolver.resolve_ref(
            "common.json#/components/schemas/Audit", FIXTURES / "loop.json")
        self.assertEqual(node["type"], "object")
        self.assertTrue(doc.endswith("common.json"))

    def test_remote_ref_rejected(self):
        with self.assertRaises(RefError):
            self.resolver.resolve_ref(
                "https://evil.invalid/x.json#/components/schemas/A", FIXTURES / "loop.json")

    def test_cycle_detection(self):
        cycles = find_cycles(self.resolver)
        self.assertIn(["Node", "Node"], cycles)
        self.assertTrue(any("Root" in c for c in cycles))

    def test_cyclic_schema_validation_terminates(self):
        schema = {"$ref": "#/components/schemas/Node"}
        ok = {"value": "a",
              "child": {"value": "b", "child": {"value": "c"}},
              "audit": {"note": "x"}}
        self.assertEqual(validate(ok, schema, self.resolver, FIXTURES / "loop.json"), [])
        bad = {"value": "a", "child": {"value": 1}}
        violations = validate(bad, schema, self.resolver, FIXTURES / "loop.json")
        self.assertTrue(any(v["path"] == "/child/value" for v in violations))


class SerializationTests(unittest.TestCase):
    def test_array_query_styles(self):
        plan = build_plan(_loop_config(), base_dir=FIXTURES)
        op = plan.operations[0]
        values = {
            "tags": ["a", "b"],
            "ids": [3, 4, 5],
            "pipes": ["x", "y"],
            "spaces": ["x", "y"],
            "filter": {"state": "open"},
        }
        qs = encode_query(serialize_query_pairs(op.parameters, values))
        for expected in ("tags=a&tags=b", "ids=3,4,5", "pipes=x%7Cy",
                         "spaces=x%20y", "filter[state]=open"):
            self.assertIn(expected, qs)

    def test_static_params_in_plan_url(self):
        plan = build_plan(_loop_config(params={"listThings": {"ids": [1, 2]}}),
                          base_dir=FIXTURES)
        self.assertIn("ids=1,2", plan.operations[0].url)

    def test_path_param_styles(self):
        specs = [{"name": "id", "in": "path"}]
        self.assertEqual(expand_path("/t/{id}", specs, {"id": [1, 2]}), "/t/1,2")
        label = [{"name": "id", "in": "path", "style": "label", "explode": True}]
        self.assertEqual(expand_path("/t/{id}", label, {"id": [1, 2]}), "/t/.1.2")
        matrix = [{"name": "id", "in": "path", "style": "matrix", "explode": True}]
        self.assertEqual(expand_path("/t/{id}", matrix, {"id": [1, 2]}), "/t/;id=1;id=2")


class PolicyTests(unittest.TestCase):
    def test_non_get_allowlist_rejected(self):
        with self.assertRaises(PolicyError):
            build_plan(_loop_config(allowed_operations=["createThing"]), base_dir=FIXTURES)

    def test_plan_requests_are_get_only(self):
        plan = _plan("collection.v1.json")
        specs = plan.request_specs()
        self.assertTrue(specs)
        self.assertTrue(all(s["method"] == "GET" for s in specs))
        self.assertTrue(all(s["url"].startswith("https://api.invalid/") for s in specs))

    def test_inheritance_and_server_override(self):
        doc = {
            "openapi": "3.0.3",
            "servers": [{"url": "https://api.invalid/x"}],
            "paths": {
                "/a": {
                    "parameters": [
                        {"name": "cursor", "in": "query", "schema": {"type": "string"}}
                    ],
                    "get": {"operationId": "readA"},
                    "post": {"operationId": "writeA"},
                },
                "/b": {
                    "get": {
                        "operationId": "readB",
                        "servers": [{"url": "https://elsewhere.invalid"}],
                    }
                },
            },
        }
        with TemporaryDirectory() as d:
            (Path(d) / "doc.json").write_text(json.dumps(doc), encoding="utf-8")
            base = {"document": "doc.json", "document_version": "t", "mapping_version": "m"}
            # 路径级参数被 GET 操作继承，计划仍只含 GET
            plan = build_plan({**base, "allowed_operations": ["readA"]}, base_dir=d)
            self.assertEqual([s["method"] for s in plan.request_specs()], ["GET"])
            self.assertIn("cursor", {p["name"] for p in plan.operations[0].parameters})
            # 写操作进入允许列表：拒绝
            with self.assertRaises(PolicyError):
                build_plan({**base, "allowed_operations": ["writeA"]}, base_dir=d)
            # 操作级 servers 覆盖会把采集带离声明来源：拒绝
            with self.assertRaises(PolicyError):
                build_plan({**base, "allowed_operations": ["readB"]}, base_dir=d)


class DiffTests(unittest.TestCase):
    def test_pagination_structure_change(self):
        report = diff_plans(_plan("collection.v1.json"), _plan("collection.v2.json"))
        kinds = {(c["kind"], c["path"]) for c in report["changes"]}
        self.assertIn(("pagination_structure_changed", "Page/next"), kinds)
        names = {(m["mapping"], m["mapping_version"]) for m in report["affected_mappings"]}
        self.assertIn(("next_cursor", "map-1"), names)
        self.assertIn(("next_cursor", "map-2"), names)

    def test_single_field_type_drift(self):
        def drift_plan(doc, ver, mv):
            config = {
                "document": doc,
                "document_version": ver,
                "mapping_version": mv,
                "allowed_operations": ["listMetrics"],
                "response_schemas": {"listMetrics": "Metric"},
                "mappings": [
                    {"name": "count", "operation": "listMetrics",
                     "source": "/count", "target": "count"},
                    {"name": "state", "operation": "listMetrics",
                     "source": "/state", "target": "state"},
                ],
            }
            return build_plan(config, base_dir=FIXTURES)

        report = diff_plans(drift_plan("drift-v1.json", "d1", "dm-1"),
                            drift_plan("drift-v2.json", "d2", "dm-2"))
        kinds = {(c["kind"], c["path"]) for c in report["changes"]}
        self.assertIn(("type_widened", "Metric/count"), kinds)
        self.assertIn(("enum_values_added", "Metric/state"), kinds)
        affected = {m["mapping"] for m in report["affected_mappings"]}
        self.assertEqual({"count", "state"}, affected)


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.plan_v1 = _plan("collection.v1.json")
        self.plan_v2 = _plan("collection.v2.json")
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = self.tmp.name

    def _run(self, plan, label):
        book = ResponseBook.from_file(FIXTURES / "fixture-responses.json", label)
        return run_replay(plan, book, self.out)

    def test_v1_completes_with_provenance(self):
        [result] = self._run(self.plan_v1, "v1")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["pages"]), 2)
        self.assertTrue(all(r["method"] == "GET" for r in result["requests"]))
        prov = result["plan"]
        self.assertEqual(prov["document_version"], "v1")
        self.assertEqual(prov["mapping_version"], "map-1")
        self.assertEqual(prov["document_digest"], self.plan_v1.document_digest)
        self.assertTrue(prov["document_digest"].startswith("sha256:"))
        saved = json.loads(Path(result["output_file"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["plan"], prov)

    def test_v2_cross_origin_next_quarantined(self):
        [result] = self._run(self.plan_v2, "v2")
        self.assertEqual(result["status"], "quarantined")
        self.assertEqual(result["quarantine"]["reason"], "next_link_rejected")
        self.assertEqual(len(result["pages"]), 1)  # 已采集页保留，不清空
        self.assertTrue(all("other.invalid" not in r["url"] for r in result["requests"]))

    def test_schema_violation_quarantined_without_clearing(self):
        [ok] = self._run(self.plan_v1, "v1")
        completed_path = Path(ok["output_file"])
        before = completed_path.read_bytes()
        [bad] = self._run(self.plan_v1, "v1-invalid")
        self.assertEqual(bad["status"], "quarantined")
        self.assertEqual(bad["quarantine"]["reason"], "schema_violation")
        self.assertEqual(len(bad["pages"]), 1)  # 不伪装成空页
        self.assertEqual(completed_path.read_bytes(), before)  # 已有结果未被清空
        self.assertIn("quarantine-", Path(bad["output_file"]).name)

    def test_replay_is_reproducible(self):
        [first] = self._run(self.plan_v1, "v1")
        self._run(self.plan_v2, "v2")  # 描述升级之后
        [second] = self._run(self.plan_v1, "v1")
        self.assertEqual(first, second)  # 旧批次仍按当时描述可复现

    def test_no_network_and_no_writes(self):
        def blocked(*args, **kwargs):
            raise AssertionError("禁止外网访问")

        with mock.patch.object(socket, "socket", blocked), \
                mock.patch.object(socket, "create_connection", blocked), \
                mock.patch.object(socket, "getaddrinfo", blocked):
            for plan, label in ((self.plan_v1, "v1"), (self.plan_v2, "v2")):
                for result in self._run(plan, label):
                    self.assertTrue(all(r["method"] == "GET" for r in result["requests"]))
            diff_plans(self.plan_v1, self.plan_v2)


if __name__ == "__main__":
    unittest.main()
