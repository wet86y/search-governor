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
        (self.root / "providers" / "demo").mkdir(parents=True)
        (self.root / "providers.local").mkdir()
        (self.root / "providers" / "demo" / "source.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
        (self.root / "providers" / "registry.json").write_text(
            json.dumps({"sources": [{"id": "demo", "path": "demo/source.json", "enabled": True}]}),
            encoding="utf-8",
        )
        self.previous = os.environ.get("SG_HOME")
        self.previous_disable_local = os.environ.pop("SEARCH_GOVERNOR_DISABLE_LOCAL", None)
        os.environ["SG_HOME"] = str(self.root)

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("SG_HOME", None)
        else:
            os.environ["SG_HOME"] = self.previous
        if self.previous_disable_local is not None:
            os.environ["SEARCH_GOVERNOR_DISABLE_LOCAL"] = self.previous_disable_local
        self.tmp.cleanup()

    def test_manual_registry_loads(self) -> None:
        sources = load_sources()
        self.assertEqual(["demo"], list(sources))
        self.assertTrue(sources["demo"].enabled)

    def test_duplicate_across_public_and_local_is_fatal(self) -> None:
        (self.root / "providers.local" / "demo").mkdir()
        (self.root / "providers.local" / "demo" / "source.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
        (self.root / "providers.local" / "registry.json").write_text(
            json.dumps({"sources": [{"id": "demo", "path": "demo/source.json", "enabled": True}]}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SourceRegistryError, "Duplicate provider id"):
            load_sources()

    def test_registry_path_cannot_escape_root(self) -> None:
        (self.root / "providers" / "registry.json").write_text(
            json.dumps({"sources": [{"id": "demo", "path": "../outside.json", "enabled": True}]}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SourceRegistryError, "escapes registry root"):
            load_sources()

    def test_recursive_provider_id_is_rejected(self) -> None:
        manifest = dict(MANIFEST, id="search-governor")
        (self.root / "providers" / "demo" / "source.json").write_text(json.dumps(manifest), encoding="utf-8")
        (self.root / "providers" / "registry.json").write_text(
            json.dumps({"sources": [{"id": "search-governor", "path": "demo/source.json", "enabled": True}]}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SourceRegistryError, "reserved"):
            load_sources()
