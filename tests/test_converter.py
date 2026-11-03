"""Integration tests: invoke the shipped Basic module in real, isolated Writer documents.

Run with a Python interpreter that can import uno; pass --soffice /path/to/soffice.
No extension installation or user-profile changes are needed.
"""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
import xml.etree.ElementTree as ET

import uno
from com.sun.star.beans import PropertyValue
from com.sun.star.connection import NoConnectException

ROOT = Path(__file__).resolve().parents[1]
TYPES = ("NORMAL", "IN_TEXT", "EMPTY", "NOPAR", "AUTHOR_ONLY", "YEAR_ONLY")
ZWSP = "\u200b"


def prop(name, value):
    return PropertyValue(Name=name, Value=value)


def escape(value):
    slash = chr(92)
    special = {slash: slash * 2, chr(10): slash + "n", chr(13): slash + "r", chr(9): slash + "t"}
    return "".join(special.get(c, slash + c if c in "{}[];," else c) for c in value)


def token(keys, kind="NORMAL", uid="uid_1", pages=None):
    slots = ["-" if page is None else "=" + escape(page) for page in
             (pages if pages is not None else [None] * len(keys))]
    return chr(92) + "cite[" + ";".join([kind, uid] + slots) + "]{" + ",".join(map(escape, keys)) + "}"


def mark_name(keys, kind, uid):
    return ", ".join("JABREF_" + key for key in keys) + " " + kind + " " + uid


class ConverterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = tempfile.TemporaryDirectory(prefix="lo-converter-", dir=ROOT / "build")
        pipe = "converter_" + uuid.uuid4().hex
        cls.process = subprocess.Popen([
            SOFFICE, "-env:UserInstallation=" + Path(cls.profile.name).as_uri(),
            "--headless", "--norestore", "--nodefault", "--nofirststartwizard",
            "--accept=pipe,name=" + pipe + ";urp;StarOffice.ComponentContext"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
        deadline = time.monotonic() + 35
        while True:
            try:
                cls.context = resolver.resolve("uno:pipe,name=" + pipe + ";urp;StarOffice.ComponentContext")
                break
            except NoConnectException:
                if time.monotonic() >= deadline:
                    cls.process.terminate()
                    cls.process.wait(timeout=10)
                    cls.profile.cleanup()
                    raise
                time.sleep(0.2)
        cls.desktop = cls.context.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", cls.context)
        cls.source = ET.parse(ROOT / "JabRefConverterLib/Module1.xba").getroot().text

    @classmethod
    def tearDownClass(cls):
        cls.desktop.terminate()
        try:
            cls.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.process.terminate()
            cls.process.wait(timeout=10)
        cls.profile.cleanup()

    def new_doc(self, text=""):
        doc = self.desktop.loadComponentFromURL("private:factory/swriter", "_blank", 0,
                                               (prop("Hidden", True), prop("MacroExecutionMode", 4)))
        self.addCleanup(doc.close, True)
        doc.BasicLibraries.createLibrary("JabRefConverterLib").insertByName("Module1", self.source)
        doc.Text.String = text
        return doc

    def invoke(self, doc, method, args=None):
        script = doc.ScriptProvider.getScript("vnd.sun.star.script:JabRefConverterLib.Module1." +
                                              method + "?language=Basic&location=document")
        return script.invoke((doc,) if args is None else args, (), ())[0]

    def convert(self, doc, method, expected):
        result = self.invoke(doc, method)
        self.assertEqual(expected, result[0], result[1])
        self.assertFalse(doc.hasControllersLocked())

    def add_mark(self, doc, name, visible="[citation]", text=None):
        text = text or doc.Text
        cursor = text.createTextCursor()
        cursor.gotoEnd(False)
        cursor.String = visible
        mark = doc.createInstance("com.sun.star.text.ReferenceMark")
        mark.Name = name
        text.insertTextContent(cursor, mark, True)
        return mark

    def properties(self, doc):
        props = doc.DocumentProperties.UserDefinedProperties
        return {p.Name: props.getPropertyValue(p.Name) for p in props.PropertySetInfo.Properties}

    def set_property(self, doc, name, value):
        doc.DocumentProperties.UserDefinedProperties.addProperty(name, 128, value)

    def test_plain_group_and_unique_ids(self):
        doc = self.new_doc(r"Before \cite{alpha,alpha,β} between \cite{alpha} after")
        self.convert(doc, "ConvertTextToReferences", 2)
        names = doc.ReferenceMarks.ElementNames
        self.assertEqual(2, len(names))
        uids = [name.rsplit(" ", 1)[1] for name in names]
        self.assertEqual(2, len(set(uids)))
        for uid in uids:
            self.assertRegex(uid, r"\A[A-Za-z0-9_-]+\Z")
        self.assertIn("JABREF_alpha, JABREF_alpha, JABREF_β NORMAL ", names[0])
        self.assertEqual("Before " + ZWSP + "alpha, alpha, β" + ZWSP + " between " +
                         ZWSP + "alpha" + ZWSP + " after", doc.Text.String)
        self.assertEqual({}, self.properties(doc))

    def test_six_types_group_pages_and_fresh_document_roundtrip(self):
        keys = ["α{β}" + chr(92) + "[x];<tag>😀", "重复", "重复"]
        pages = ["pp. 2; {三}, [4]" + chr(92) + chr(10) + chr(13) + chr(9) + "<em>é</em>", None, ""]
        for kind in TYPES:
            with self.subTest(kind=kind):
                uid = "Opaque-" + kind
                original = "left " + token(keys, kind, uid, pages) + " right"
                doc = self.new_doc(original)
                self.convert(doc, "ConvertTextToReferences", 1)
                name = mark_name(keys, kind, uid)
                anchor = doc.ReferenceMarks.getByName(name).Anchor.String
                self.assertEqual(ZWSP + ("" if kind == "EMPTY" else ", ".join(keys)) + ZWSP, anchor)
                self.assertEqual({"JabRef_page_info:" + uid + ":0": pages[0],
                                  "JabRef_page_info:" + uid + ":2": ""}, self.properties(doc))
                self.convert(doc, "ConvertReferencesToText", 1)
                self.assertEqual(original, doc.Text.String)
                other = self.new_doc(doc.Text.String)
                self.convert(other, "ConvertTextToReferences", 1)
                self.assertEqual((name,), other.ReferenceMarks.ElementNames)
                self.assertEqual(self.properties(doc), self.properties(other))

    def test_noncanonical_marks_and_unrelated_properties_are_untouched(self):
        names = ["JR_cite100_1_key", "JR_cite3_3_key", "JABREF_key CID_4 cite",
                 "JABREF_key uid NORMAL", "ZOTERO_ITEM CSL_CITATION {}",
                 "prefix JABREF_key NORMAL uid", "JABREF_key NORMAL uid suffix",
                 "JABREF_ NORMAL uid", "JABREF_key,JABREF_other NORMAL uid",
                 "JABREF_key,  JABREF_other NORMAL uid", "JABREF_key NORMAL úid",
                 "JABREF_key NORMAL uid:1", "JABREF_key UNKNOWN uid",
                 "JABREF_a b NORMAL uid", "JABREF_a" + chr(160) + "b NORMAL uid",
                 " NORMAL uid", "JABREF_key NORMAL uid ", "JABREF_key normal uid"]
        doc = self.new_doc("left ")
        for n, name in enumerate(names):
            self.add_mark(doc, name, "[foreign " + str(n) + "]")
        for name in ["JabRef_style", "JabRef_bib", "ZOTERO_PREF_1", "JR_cite_legacy",
                     "JabRef_page_info:foreign:0", "unrelated"]:
            self.set_property(doc, name, "keep " + name)
        before = doc.Text.String
        props = self.properties(doc)
        self.convert(doc, "ConvertReferencesToText", 0)
        self.assertEqual(before, doc.Text.String)
        self.assertEqual(tuple(names), doc.ReferenceMarks.ElementNames)
        self.assertEqual(props, self.properties(doc))

    def test_invalid_text_is_not_partially_converted(self):
        invalid = [r"\cite{}", r"\cite{a,}", r"\cite{,b}", r"\cite{a b}",
                   chr(92) + "cite{a" + chr(0x202f) + "b}", r"\cite{a\,b}", r"\cite{a\q}",
                   r"\cite[unknown;id]{a}", r"\cite[NORMAL;id:1]{a}",
                   r"\cite[NORMAL;é]{a}", r"\cite[NORMAL;id;=p]{a,b}",
                   r"\cite[NORMAL;id;=p;=q]{a}", r"\cite[NORMAL;id;page]{a}",
                   r"\cite[NORMAL;id;=bare,comma]{a}", r"\cite{a{b}}",
                   r"\cite{bad \cite{nested}}", r"\cite[bad \cite{nested}}",
                   r"\cite{not closed", r"\cite[NORMAL;id]{not closed"]
        for original in invalid:
            with self.subTest(original=original):
                doc = self.new_doc("before " + original + " after")
                self.convert(doc, "ConvertTextToReferences", 0)
                self.assertEqual("before " + original + " after", doc.Text.String)
                self.assertEqual((), doc.ReferenceMarks.ElementNames)
                self.assertEqual({}, self.properties(doc))

    def test_literal_old_markers_are_keys_not_type_instructions(self):
        doc = self.new_doc(r"\cite{_1_key,_2_key,_3_key}")
        self.convert(doc, "ConvertTextToReferences", 1)
        name, = doc.ReferenceMarks.ElementNames
        self.assertTrue(name.startswith("JABREF__1_key, JABREF__2_key, JABREF__3_key NORMAL "))
        self.assertEqual(ZWSP + "_1_key, _2_key, _3_key" + ZWSP, doc.Text.String)

    def test_duplicate_explicit_uid_gets_fresh_uid_and_own_pages(self):
        doc = self.new_doc(token(["a", "a"], uid="same", pages=["first", None]) + " / " +
                           token(["b"], "IN_TEXT", "same", ["second"]))
        self.convert(doc, "ConvertTextToReferences", 2)
        first, second = doc.ReferenceMarks.ElementNames
        self.assertEqual("JABREF_a, JABREF_a NORMAL same", first)
        self.assertRegex(second, r"\AJABREF_b IN_TEXT [A-Za-z0-9_-]+\Z")
        new_uid = second.rsplit(" ", 1)[1]
        self.assertNotEqual("same", new_uid)
        self.assertEqual({"JabRef_page_info:same:0": "first",
                          "JabRef_page_info:" + new_uid + ":0": "second"}, self.properties(doc))

    def test_collision_with_existing_mark_and_omitted_pages(self):
        doc = self.new_doc(r"\cite[NOPAR;existing]{b}")
        self.add_mark(doc, "JABREF_a NORMAL existing")
        self.set_property(doc, "JabRef_page_info:existing:0", "pp. 42–43")
        self.convert(doc, "ConvertTextToReferences", 1)
        names = doc.ReferenceMarks.ElementNames
        self.assertEqual(2, len(names))
        new_name, = [name for name in names if name != "JABREF_a NORMAL existing"]
        uid = new_name.rsplit(" ", 1)[1]
        self.assertNotEqual("existing", uid)
        self.assertEqual("pp. 42–43", self.properties(doc)["JabRef_page_info:" + uid + ":0"])
        self.assertEqual("[citation]", doc.ReferenceMarks.getByName("JABREF_a NORMAL existing").Anchor.String)

    def test_tokens_inside_foreign_reference_are_not_changed(self):
        doc = self.new_doc()
        self.add_mark(doc, "foreign", r"\cite{keep}")
        self.convert(doc, "ConvertTextToReferences", 0)
        self.assertEqual(r"\cite{keep}", doc.Text.String)
        self.assertEqual(("foreign",), doc.ReferenceMarks.ElementNames)

    def test_literal_cite_fragments_inside_keys_and_pages(self):
        keys = ["x" + chr(92) + "cite{not-a-citation}", "é", "👩‍🔬"]
        pages = [chr(92) + "cite{not-a-citation}", "=;-", None]
        original = token(keys, uid="literal", pages=pages) + " " + token(["next"], uid="next")
        doc = self.new_doc(original)
        self.convert(doc, "ConvertTextToReferences", 2)
        self.convert(doc, "ConvertTextToReferences", 0)
        self.convert(doc, "ConvertReferencesToText", 2)
        self.assertEqual(original, doc.Text.String)

    def test_optional_metadata_and_explicit_absent_pages(self):
        doc = self.new_doc(r"\cite[YEAR_ONLY]{a} / \cite[AUTHOR_ONLY;;=p]{b} / \cite[NORMAL;keep;-]{c}")
        self.set_property(doc, "JabRef_page_info:keep:0", "old")
        self.set_property(doc, "JabRef_style", "unchanged")
        self.convert(doc, "ConvertTextToReferences", 3)
        names = doc.ReferenceMarks.ElementNames
        self.assertIn(" YEAR_ONLY ", names[0])
        self.assertIn(" AUTHOR_ONLY ", names[1])
        self.assertEqual("JABREF_c NORMAL keep", names[2])
        self.assertNotIn("JabRef_page_info:keep:0", self.properties(doc))
        self.assertEqual("unchanged", self.properties(doc)["JabRef_style"])
        self.assertEqual(2, len(self.properties(doc)))

    def test_tables_and_footnotes_in_both_directions(self):
        doc = self.new_doc("body ")
        self.add_mark(doc, "JABREF_body NORMAL body-id")
        table = doc.createInstance("com.sun.star.text.TextTable")
        table.initialize(1, 1)
        doc.Text.insertTextContent(doc.Text.End, table, False)
        cell = table.getCellByName("A1")
        cell.String = "cell "
        self.add_mark(doc, "JABREF_table IN_TEXT table-id", text=cell.Text)
        note = doc.createInstance("com.sun.star.text.Footnote")
        doc.Text.insertTextContent(doc.Text.End, note, False)
        note.Text.String = "note "
        self.add_mark(doc, "JABREF_note EMPTY note-id", text=note.Text)
        self.set_property(doc, "JabRef_page_info:note-id:0", "p. 9")
        self.convert(doc, "ConvertReferencesToText", 3)
        self.assertEqual("cell " + token(["table"], "IN_TEXT", "table-id"), cell.String)
        self.assertEqual("note " + token(["note"], "EMPTY", "note-id", ["p. 9"]), note.Text.String)
        self.assertEqual((), doc.ReferenceMarks.ElementNames)
        self.convert(doc, "ConvertTextToReferences", 3)
        self.assertEqual({"JABREF_body NORMAL body-id", "JABREF_table IN_TEXT table-id", "JABREF_note EMPTY note-id"},
                         set(doc.ReferenceMarks.ElementNames))
        self.assertEqual("cell " + ZWSP + "table" + ZWSP, cell.String)
        self.assertEqual("note " + ZWSP * 2, note.Text.String)

    def test_record_changes_existing_locks_selection_and_undo(self):
        for recording in (False, True):
            with self.subTest(recording=recording):
                doc = self.new_doc("before " + token(["key"], uid="undo") + " after")
                doc.RecordChanges = recording
                doc.lockControllers()
                original = doc.Text.String
                result = self.invoke(doc, "ConvertTextToReferences")
                self.assertEqual((1, ""), result)
                self.assertTrue(doc.hasControllersLocked())
                doc.unlockControllers()
                self.assertEqual(recording, doc.RecordChanges)
                self.assertEqual(0, doc.Redlines.Count)
                doc.UndoManager.undo()
                self.assertEqual(original, doc.Text.String)
                self.assertEqual((), doc.ReferenceMarks.ElementNames)
                doc.UndoManager.redo()
                self.assertEqual(("JABREF_key NORMAL undo",), doc.ReferenceMarks.ElementNames)
                self.convert(doc, "ConvertReferencesToText", 1)
                self.assertEqual(recording, doc.RecordChanges)
                self.assertEqual(original, doc.Text.String)
                self.assertEqual(0, doc.Redlines.Count)

    def test_docx_portable_text_roundtrip(self):
        originals = [token(["<α>{β}", "key", "key"], kind, "docx-" + kind,
                           ["p. 1; [2], " + chr(92), None, ""]) for kind in TYPES]
        doc = self.new_doc(" / ".join(originals))
        self.convert(doc, "ConvertTextToReferences", 6)
        self.convert(doc, "ConvertReferencesToText", 6)
        with tempfile.TemporaryDirectory(prefix="docx-", dir=ROOT / "build") as folder:
            url = (Path(folder) / "roundtrip.docx").as_uri()
            doc.storeToURL(url, (prop("FilterName", "Office Open XML Text"), prop("Overwrite", True)))
            reopened = self.desktop.loadComponentFromURL(url, "_blank", 0,
                        (prop("Hidden", True), prop("MacroExecutionMode", 4)))
            try:
                self.assertEqual(" / ".join(originals), reopened.Text.String)
                reopened.BasicLibraries.createLibrary("JabRefConverterLib").insertByName("Module1", self.source)
                # Prove portable slots suffice even without original custom properties.
                props = reopened.DocumentProperties.UserDefinedProperties
                for name in list(self.properties(reopened)):
                    if name.startswith("JabRef_page_info:"):
                        props.removeProperty(name)
                self.convert(reopened, "ConvertTextToReferences", 6)
                self.assertEqual({mark_name(["<α>{β}", "key", "key"], kind, "docx-" + kind)
                                  for kind in TYPES}, set(reopened.ReferenceMarks.ElementNames))
                self.assertEqual(12, len([name for name in self.properties(reopened) if name.startswith("JabRef_page_info:")]))
            finally:
                reopened.close(True)

    def test_nonstring_page_property_leaves_reference_intact(self):
        doc = self.new_doc()
        self.add_mark(doc, "JABREF_key NORMAL opaque")
        self.set_property(doc, "JabRef_page_info:opaque:0", 123.0)
        before = doc.Text.String
        self.convert(doc, "ConvertReferencesToText", 0)
        self.assertEqual(before, doc.Text.String)
        self.assertEqual(("JABREF_key NORMAL opaque",), doc.ReferenceMarks.ElementNames)
        self.assertEqual({"JabRef_page_info:opaque:0": 123}, self.properties(doc))

    def test_adjacent_foreign_marks_preserve_anchors(self):
        doc = self.new_doc("before" + token(["key"], uid="adjacent") + "after")
        for visible in ("before", "after"):
            search = doc.createSearchDescriptor()
            search.SearchString = visible
            mark = doc.createInstance("com.sun.star.text.ReferenceMark")
            mark.Name = "foreign-" + visible
            doc.Text.insertTextContent(doc.findFirst(search), mark, True)
        self.convert(doc, "ConvertTextToReferences", 1)
        self.assertEqual("before", doc.ReferenceMarks.getByName("foreign-before").Anchor.String)
        self.assertEqual("after", doc.ReferenceMarks.getByName("foreign-after").Anchor.String)
        self.convert(doc, "ConvertReferencesToText", 1)
        self.assertEqual("before", doc.ReferenceMarks.getByName("foreign-before").Anchor.String)
        self.assertEqual("after", doc.ReferenceMarks.getByName("foreign-after").Anchor.String)

    def test_overlapping_foreign_and_canonical_marks_are_untouched(self):
        doc = self.new_doc("before citation after")
        for name, needle in [("foreign", "before citation after"),
                             ("JABREF_key NORMAL overlap", "citation")]:
            search = doc.createSearchDescriptor()
            search.SearchString = needle
            mark = doc.createInstance("com.sun.star.text.ReferenceMark")
            mark.Name = name
            doc.Text.insertTextContent(doc.findFirst(search), mark, True)
        self.convert(doc, "ConvertReferencesToText", 0)
        self.assertEqual("before citation after", doc.Text.String)
        self.assertEqual("before citation after", doc.ReferenceMarks.getByName("foreign").Anchor.String)
        self.assertEqual("citation", doc.ReferenceMarks.getByName("JABREF_key NORMAL overlap").Anchor.String)

    def test_incompatible_page_property_leaves_import_token_intact(self):
        original = token(["key"], uid="opaque", pages=["new"])
        doc = self.new_doc(original)
        self.set_property(doc, "JabRef_page_info:opaque:0", 123.0)
        self.convert(doc, "ConvertTextToReferences", 0)
        self.assertEqual(original, doc.Text.String)
        self.assertEqual((), doc.ReferenceMarks.ElementNames)
        self.assertEqual({"JabRef_page_info:opaque:0": 123.0}, self.properties(doc))

    def test_export_zero_length_canonical_mark_inserts_text(self):
        doc = self.new_doc("left right")
        cursor = doc.Text.createTextCursor()
        cursor.goRight(5, False)
        mark = doc.createInstance("com.sun.star.text.ReferenceMark")
        mark.Name = "JABREF_key EMPTY point"
        doc.Text.insertTextContent(cursor, mark, False)
        self.convert(doc, "ConvertReferencesToText", 1)
        self.assertEqual("left " + token(["key"], "EMPTY", "point") + "right", doc.Text.String)
        self.assertEqual((), doc.ReferenceMarks.ElementNames)
        self.convert(doc, "ConvertTextToReferences", 1)
        self.assertEqual(ZWSP * 2, doc.ReferenceMarks.getByName("JABREF_key EMPTY point").Anchor.String)

    def test_adjacent_tokens_with_same_uid_preserve_both_and_empty_boundaries(self):
        doc = self.new_doc(token(["a"], "EMPTY", "same") + token(["a"], "EMPTY", "same"))
        self.convert(doc, "ConvertTextToReferences", 2)
        names = doc.ReferenceMarks.ElementNames
        self.assertEqual(2, len(names))
        self.assertEqual(2, len({name.rsplit(" ", 1)[1] for name in names}))
        self.assertEqual(ZWSP * 4, doc.Text.String)
        for name in names:
            self.assertEqual(ZWSP * 2, doc.ReferenceMarks.getByName(name).Anchor.String)
        self.convert(doc, "ConvertReferencesToText", 2)
        self.assertEqual(2, doc.Text.String.count(chr(92) + "cite[EMPTY;"))

    def test_no_matches_and_failure_restore_state(self):
        doc = self.new_doc("no citations")
        doc.RecordChanges = True
        self.convert(doc, "ConvertTextToReferences", 0)
        self.convert(doc, "ConvertReferencesToText", 0)
        self.assertTrue(doc.RecordChanges)
        # A different UNO component exercises the error cleanup without touching any Writer document.
        non_writer = self.desktop.loadComponentFromURL("private:factory/scalc", "_blank", 0, (prop("Hidden", True),))
        try:
            result = self.invoke(doc, "ConvertTextToReferences", (non_writer,))
            self.assertEqual(-1, result[0])
            self.assertIn("Conversion stopped:", result[1])
            self.assertFalse(non_writer.hasControllersLocked())
            self.assertTrue(doc.RecordChanges)
        finally:
            non_writer.close(True)

    def test_export_uses_anchor_order_not_creation_order(self):
        doc = self.new_doc("ABC")
        for visible in ("C", "A", "B"):
            search = doc.createSearchDescriptor()
            search.SearchString = visible
            mark = doc.createInstance("com.sun.star.text.ReferenceMark")
            mark.Name = mark_name([visible], "NORMAL", visible)
            doc.Text.insertTextContent(doc.findFirst(search), mark, True)
        self.convert(doc, "ConvertReferencesToText", 3)
        self.assertEqual("".join(token([key], uid=key) for key in "ABC"), doc.Text.String)

    def test_readme_escaped_example(self):
        example, = [line for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
                    if line.startswith(chr(92) + "cite[NORMAL;escaped_1;")]
        doc = self.new_doc(example)
        self.convert(doc, "ConvertTextToReferences", 1)
        self.assertEqual((mark_name(["α{β}" + chr(92) + "[x];<tag>"], "NORMAL", "escaped_1"),),
                         doc.ReferenceMarks.ElementNames)
        self.assertEqual({"JabRef_page_info:escaped_1:0": "p. 2; [3], appendix" + chr(92) + "A"},
                         self.properties(doc))
        self.convert(doc, "ConvertReferencesToText", 1)
        self.assertEqual(example, doc.Text.String)

    def test_dialog_loads_without_mode_controls(self):
        provider = self.context.ServiceManager.createInstanceWithContext("com.sun.star.awt.DialogProvider2", self.context)
        dialog = provider.createDialog((ROOT / "JabRefConverterLib/JabRefConvert.xdl").as_uri())
        try:
            self.assertEqual("Text to references", dialog.getControl("ImportButton").Model.Label)
            self.assertEqual("References to text", dialog.getControl("ExportButton").Model.Label)
            self.assertIsNone(dialog.getControl("optCslMode"))
            self.assertIsNone(dialog.getControl("optJstyleMode"))
        finally:
            dialog.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--soffice", required=True)
    options, rest = parser.parse_known_args()
    SOFFICE = str(Path(options.soffice).resolve())
    (ROOT / "build").mkdir(exist_ok=True)
    unittest.main(argv=[sys.argv[0]] + rest, verbosity=2)
