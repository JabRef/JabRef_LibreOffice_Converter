# JabRef LibreOffice Converter

**Version 3.0 — breaking canonical-format update.** Convert JabRef citations to
portable text before editing in Microsoft Word or another word processor, then
restore the references in LibreOffice Writer. No additional user dependency is needed.

## Use

1. Install `JabRefConverter.oxt` through LibreOffice's Extension Manager and restart LibreOffice.
2. Open a **copy** of your manuscript in Writer and click the converter's JabRef toolbar button.
3. Choose **References to text** before saving a Word copy. Citations become portable tokens.
4. Edit in Word, keeping tokens intact. You can add manual `\cite{key}` tokens too.
5. Reopen in Writer and choose **Text to references**.
6. Refresh citations and bibliography using JabRef and your chosen citation style.

The converter does not look up entries or format a bibliography. Restored citations
initially display their keys, not their final formatted citations.

## Canonical reference names

All three JabRef style families use the **same** representation. There is no
CSL/JStyle mode picker. Use a JabRef build whose codec writes this exact order:

```text
JABREF_<key>[, JABREF_<key2>...] <TYPE> <uid>
JABREF_Doe2024, JABREF_李2025 IN_TEXT opaque-Id_7
```

- `TYPE` is exactly `NORMAL`, `IN_TEXT`, `EMPTY`, `NOPAR`, `AUTHOR_ONLY`, or `YEAR_ONLY`.
- Keys are nonempty Unicode text without Unicode whitespace or commas.
- Group separators are exactly comma + one ASCII space; every key has a `JABREF_` prefix.
- UIDs are nonempty, opaque strings of ASCII letters, digits, `_`, and `-`.
- Entire names must match, including case and spacing. UID-before-TYPE is not accepted.
- Group order and repeated keys are preserved, with separate page-info slots for repeated keys.

New references enclose the visible key placeholder in two U+200B zero-width spaces.
`EMPTY` references contain only those two characters. These persistent boundaries
match JabRef's mark-store range layout, rather than creating zero-length references.

## Portable text

Simple manual citations create `NORMAL` groups with new document-unique UIDs:

```text
\cite{Doe2024}
\cite{Doe2024,李2025}
```

Spaces around commas are not accepted: whitespace cannot be part of a key.
For explicit metadata, use:

```text
\cite[TYPE;uid;page-slot1;page-slot2]{escaped-key1,escaped-key2}
```

- The entire `[...]` is optional; without it the type is `NORMAL`.
- With brackets, `TYPE` is required; UID and page slots may be omitted.
- An omitted or empty UID requests a fresh UID.
- If page slots are provided, there must be **exactly one per key**, in storage order.
- `-` means an absent page property. `=value` means a present property with that exact string. `=` alone preserves a present empty string.
- Omitted page slots reuse document metadata for the supplied UID, where available. Plain manual citations do not create empty properties.
- Export always includes TYPE, UID, and all page slots, so the text suffices in a fresh document.

Examples:

```text
\cite[NORMAL;opaque-Id_7;-]{Doe2024}
\cite[IN_TEXT;group_8;=pp. 12–14;-]{Doe2024,李2025}
\cite[EMPTY;hidden_9;=]{Doe2024}
\cite[NOPAR;group_10;=p. 2;=p. 9]{Doe2024,Doe2024}
\cite[AUTHOR_ONLY]{Doe2024}
\cite[YEAR_ONLY;;=p. 12]{Doe2024}
```

Escape each literal backslash, brace, bracket, semicolon, or comma with a backslash:
`\\`, `\{`, `\}`, `\[`, `\]`, `\;`, `\,`. Use `\n`, `\r`, and `\t` for linefeed,
carriage return, and tab **inside page values**. Unknown escapes are invalid.
Commas cannot occur in canonical keys, even escaped. Unicode and markup-like
characters such as `<`, `>`, and `&` are literal, not interpreted as XML.

A key `α{β}\[x];<tag>` and page text `p. 2; [3], appendix\A` export as:

```text
\cite[NORMAL;escaped_1;=p. 2\; \[3\]\, appendix\\A]{α\{β\}\\\[x\]\;<tag>}
```

Page metadata lives at `JabRef_page_info:<uid>:<zero-based-index>`. Export leaves
these properties in place and also carries them in the token. Import updates only
the imported group's slots, without creating absent properties. Unrelated styles,
bibliography state, and custom properties are never changed or cleaned up.

Existing UIDs are preserved. If pasted tokens repeat a UID or collide with an
existing canonical reference, later imported occurrences get fresh UIDs with their
own page slots. A native opaque random/counter generator checks document marks,
reserved token IDs, and page-property names. These IDs are **not CUID2** and are
never derived from unstable entry IDs.

## Breaking changes and safety

- Version 3.0 does **not** decode `JR_cite`, `CID_`, old marker-based formats, or Zotero data. There is no legacy import mode.
- Legacy, malformed, and foreign reference marks remain intact, not disposed. A substring containing `JABREF_` is insufficient.
- `_1_`, `_2_`, and `_3_` inside valid manual keys are literal characters, never type instructions.
- Tokens overlapping references, and references overlapping other references, are skipped to protect unrelated anchors.
- Invalid tokens remain unmodified. After malformed `\cite` input, the rest of that paragraph is conservatively skipped to prevent nested partial conversions. Correct the token and run again.
- Tokens must fit within one Writer paragraph. Encoded page newlines do not split a token.
- Incompatible/non-string page properties are not overwritten; the affected citation is skipped.
- Body text, table cells, and footnotes are supported, independently of selection and view cursor.
- One undo context groups text/reference changes. Controller locks and the original track-changes setting are restored, including on errors. Conversion itself is not recorded as redlines.
- Native custom-property changes are **not undoable** through this Basic macro. Keep a backup when changing page slots; text undo does not roll metadata back.
- Unexpected UNO errors stop conversion with an error message. Earlier conversions may remain; review before saving.

Old screenshots were removed from these instructions because they show obsolete
formats and controls. Older JabRef builds are intentionally not supported. Test a
copy before using the converter in a collaborative workflow.

## Build and focused tests

From this repository, with Python 3:

```sh
python tools/build_extension.py
python -m unittest discover -s tests -p test_package.py -v
```

The standard-library builder uses an explicit source allowlist, normalized LF text,
sorted ZIP entries, fixed timestamps/permissions, and stored entries (no compressor
variation). It replaces tracked `JabRefConverter.oxt`; rerun after packaged changes.

For actual Basic integration tests, use a Python runtime that can `import uno`
(such as LibreOffice's bundled Python on Windows):

```sh
/path/to/libreoffice/python tests/test_converter.py --soffice /path/to/libreoffice/soffice
```

Tests launch an isolated headless profile, insert the shipped XML module into
temporary documents' `BasicLibraries`, and invoke `ConvertTextToReferences(doc)`
and `ConvertReferencesToText(doc)` through the document script provider. They do
not reimplement the Basic codec. Both functions return
`Array(convertedCount, errorMessage)`; errors return count `-1`.

Coverage includes all six types, duplicate keys, escaped Unicode/markup-like text,
page data, UID collisions, invalid/foreign input, adjacent references, tables,
footnotes, state restoration, text undo/redo, and a DOCX save/reopen cycle.
Temporary profiles/documents use ignored `build/` and are removed on completion.
DOCX testing uses LibreOffice's filter; it does **not** run Microsoft Word.

### Manual review before release

1. Install rebuilt 3.0 in a disposable LibreOffice profile. Verify the two conversion buttons and absence of a mode selector.
2. In a copy of a canonical JabRef document, include all six types, grouped/repeated keys, page locators, and citations in a table and footnote.
3. Convert references to text. Check TYPE/UID/page slots, surrounding text, and unchanged unrelated references.
4. Save as DOCX, edit in Microsoft Word, add `\cite{newkey}`, and paste a copy of an explicit token.
5. Reopen in Writer, restore references, and refresh with JabRef. Verify grouping, types, page locators, invisible citations, and distinct UIDs for pasted copies.
6. Capture the updated dialog and relevant before/after citation areas for review. Check rendering with the intended JabRef style families.
