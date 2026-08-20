from pathlib import Path


def is_pdf_file(path: Path) -> bool:
    if path.suffix.casefold() != ".pdf" or not path.is_file():
        return False
    try:
        with path.open("rb") as stream:
            return stream.read(5) == b"%PDF-"
    except OSError:
        return False
