"""The vectors the phone app is tested against must stay what this program produces and reads."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import make_mobile_vectors  # noqa: E402
from tuckvault import crypto  # noqa: E402


class Vectors(unittest.TestCase):
    def setUp(self):
        self.stored = json.loads(make_mobile_vectors.OUT.read_text(encoding="utf-8"))
        self.keys = crypto.Keys(bytes.fromhex(self.stored["master"]))

    def test_file_matches_the_format_this_version_reads(self):
        self.assertEqual(self.keys.check, self.stored["key_check"])
        self.assertEqual(crypto.decode_recovery_key(self.stored["recovery_key"]).hex(), self.stored["master"])
        for chunk in self.stored["chunks"]:
            data = self.keys.open_chunk(chunk["name"], bytes.fromhex(chunk["blob"]))
            self.assertEqual(data.hex(), chunk["data"], chunk["label"])
        snap = self.stored["snapshot"]
        self.assertEqual(self.keys.open_snapshot(bytes.fromhex(snap["raw"])), json.loads(snap["json"]))
        for bad in self.stored["bad_recovery_keys"].values():
            with self.assertRaises(ValueError):
                crypto.decode_recovery_key(bad)

    def test_what_is_not_compressed_is_stored_identically(self):
        # Compressed bytes may differ between zlib builds, so only these are compared exactly.
        fresh = {c["label"]: c for c in make_mobile_vectors.build()["chunks"]}
        for chunk in self.stored["chunks"]:
            if not chunk["compressed"]:
                self.assertEqual(fresh[chunk["label"]]["blob"], chunk["blob"], chunk["label"])
        self.assertEqual(make_mobile_vectors.build()["signature"], self.stored["signature"])


if __name__ == "__main__":
    unittest.main()
