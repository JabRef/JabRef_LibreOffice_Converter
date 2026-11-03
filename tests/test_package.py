"""Package/source consistency checks; no LibreOffice or third-party modules required."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_extension", ROOT / "tools/build_extension.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class PackageTest(unittest.TestCase):
    def test_tracked_extension_matches_reproducible_build(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as folder:
            first = builder.build(Path(folder) / "first.oxt")
            second = builder.build(Path(folder) / "second.oxt")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first.read_bytes(), (ROOT / "JabRefConverter.oxt").read_bytes(),
                             "Stale extension: run python tools/build_extension.py")

    def test_archive_entries_match_sources_and_fixed_metadata(self):
        with zipfile.ZipFile(ROOT / "JabRefConverter.oxt") as archive:
            self.assertEqual(sorted(builder.FILES), archive.namelist())
            self.assertIsNone(archive.testzip())
            for entry in archive.infolist():
                with self.subTest(entry=entry.filename):
                    self.assertEqual(builder.source_bytes(entry.filename), archive.read(entry.filename))
                    self.assertEqual((2020, 1, 1, 0, 0, 0), entry.date_time)
                    self.assertEqual(zipfile.ZIP_STORED, entry.compress_type)
                    self.assertEqual(0o100644 << 16, entry.external_attr)
                    self.assertNotIn("build/", entry.filename)
                    self.assertNotIn("tests/", entry.filename)

    def test_manifest_paths_and_versions(self):
        manifest = ET.parse(ROOT / "META-INF/manifest.xml").getroot()
        ns = "{http://openoffice.org/2001/manifest}"
        for entry in manifest:
            self.assertTrue((ROOT / entry.attrib[ns + "full-path"]).exists())
        paths = [entry.attrib[ns + "full-path"] for entry in manifest]
        self.assertIn("pkg-description/pkg-description.en", paths)
        description = ET.parse(ROOT / "description.xml").getroot()
        self.assertEqual("3.0", description.find("{*}version").attrib["value"])
        self.assertIn("3.0", (ROOT / "pkg-description/pkg-description.en").read_text(encoding="utf-8"))
        for entry in description.iter():
            href = entry.attrib.get("{http://www.w3.org/1999/xlink}href")
            if href and not href.startswith("mailto:"):
                self.assertIn(href, builder.FILES)

    def test_dialog_has_two_buttons_without_modes(self):
        dialog = ET.parse(ROOT / "JabRefConverterLib/JabRefConvert.xdl").getroot()
        self.assertEqual([], dialog.findall(".//{*}radio"))
        self.assertEqual(2, len(dialog.findall(".//{*}button")))
        events = dialog.findall(".//{*}event")
        urls = [event.attrib["{http://openoffice.org/2000/script}macro-name"] for event in events]
        for method in ("FromTextToReference", "FromReferenceToText"):
            self.assertIn("vnd.sun.star.script:JabRefConverterLib.Module1." + method +
                          "?language=Basic&location=application", urls)

    def test_library_module_registration_and_xml(self):
        for name in ("script.xlb", "dialog.xlb"):
            library = ET.parse(ROOT / "JabRefConverterLib" / name).getroot()
            self.assertEqual("JabRefConverterLib", library.attrib["{http://openoffice.org/2000/library}name"])
        for name in builder.FILES:
            if Path(name).suffix in (".xml", ".xcu", ".xlb", ".xdl", ".xba"):
                ET.parse(ROOT / name)
        module = ET.parse(ROOT / "JabRefConverterLib/Module1.xba").getroot().text
        for obsolete in ("JR_cite", "CID_", "ZOTERO", "useCslMode", "ViewCursor"):
            self.assertNotIn(obsolete, module)


if __name__ == "__main__":
    unittest.main(verbosity=2)
