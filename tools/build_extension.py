"""Build the extension from an explicit source allowlist with reproducible ZIP metadata."""
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "Addons.xcu", "description.xml", "META-INF/manifest.xml",
    "pkg-description/pkg-description.en", "registration/license.txt",
    "icons/JabRef-icon-256.png", "LICENSE", "README.md",
    "JabRefConverterLib/dialog.xlb", "JabRefConverterLib/script.xlb",
    "JabRefConverterLib/JabRefConvert.xdl", "JabRefConverterLib/Module1.xba",
)


def source_bytes(name):
    data = (ROOT / name).read_bytes()
    return data if name.endswith(".png") else data.replace(bytes((13, 10)), bytes((10,)))


def build(destination=ROOT / "JabRefConverter.oxt"):
    # Stored entries avoid compressor-version differences; this extension is small.
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(FILES):
            entry = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, source_bytes(name))
    return destination


if __name__ == "__main__":
    output = build()
    print(f"{output.name}: {hashlib.sha256(output.read_bytes()).hexdigest()}")
