from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from search_governor.sources import SourceRegistryError, load_sources


MANIFEST = {
    "id": "demo",
    "type": "subprocess",
    "entrypoint": "python adapter.py",
    "capabilities": {},
}


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "managed_sources" / "demo").mkdir(parents=True)
        (self.root / "managed_sources" / "demo" / "source.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
        (self.root / "managed_sources" / "sources.json").write_text(
            json.dumps({"sources": [{"id": "demo", "path": "demo/source.json", "enabled": True}]}),
            encoding="utf-8",
        )
        self.previous = os.environ.get("SG_HOME")
        self.previous_sources_dir = os.environ.pop("SG_SOURCES_DIR", None)
        os.environ["SG_HOME"] = str(self.root)

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("SG_HOME", None)
        else:
            os.environ["SG_HOME"] = self.previous
        if self.previous_sources_dir is not None:
            os.environ["SG_SOURCES_DIR"] = self.previous_sources_dir
        self.tmp.cleanup()

    def test_manual_registry_loads(self) -> None:
        sources = load_sources()
        self.assertEqual(["demo"], list(sources))
        self.assertTrue(sources["demo"].enabled)

    def test_example_tree_is_not_scanned_as_runtime(self) -> None:
        example_root = self.root / "examples" / "managed_sources"
        example_root.parent.mkdir(parents=True)
        (self.root / "managed_sources").rename(example_root)
        self.assertEqual({}, load_sources())

    def test_explicit_sources_dir_can_run_isolated_contract_examples(self) -> None:
        previous = os.environ.get("SG_SOURCES_DIR")
        os.environ["SG_SOURCES_DIR"] = str(self.root / "managed_sources")
        try:
            self.assertEqual(["demo"], list(load_sources()))
        finally:
            if previous is None:
                os.environ.pop("SG_SOURCES_DIR", None)
            else:
                os.environ["SG_SOURCES_DIR"] = previous

    def test_duplicate_id_in_single_registry_is_fatal(self) -> None:
        registry = {"sources": [
            {"id": "demo", "path": "demo/source.json", "enabled": True},
            {"id": "demo", "path": "demo/source.json", "enabled": False},
        ]}
        (self.root / "managed_sources" / "sources.json").write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaisesRegex(SourceRegistryError, "Duplicate provider id"):
            load_sources()

    def test_registry_path_cannot_escape_root(self) -> None:
        (self.root / "managed_sources" / "sources.json").write_text(
            json.dumps({"sources": [{"id": "demo", "path": "../outside.json", "enabled": True}]}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SourceRegistryError, "escapes registry root"):
            load_sources()

    def test_recursive_provider_id_is_rejected(self) -> None:
        manifest = dict(MANIFEST, id="search-governor")
        (self.root / "managed_sources" / "demo" / "source.json").write_text(json.dumps(manifest), encoding="utf-8")
        (self.root / "managed_sources" / "sources.json").write_text(
            json.dumps({"sources": [{"id": "search-governor", "path": "demo/source.json", "enabled": True}]}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SourceRegistryError, "reserved"):
            load_sources()
