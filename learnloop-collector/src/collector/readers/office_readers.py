"""Word, PowerPoint and Excel readers.

All three formats are ZIP archives of XML. The libraries only parse that XML:
macros are not part of these formats (.docm/.xlsm are not supported) and
Excel formulas are never calculated – we read the values saved in the file.
"""

from __future__ import annotations

import zipfile
from typing import BinaryIO

from collector.readers.base import ReaderError, Segment
from collector.readers.file_types import require_office

_PARSE_ERRORS = (zipfile.BadZipFile, KeyError, ValueError, TypeError)


def read_docx(f: BinaryIO) -> list[Segment]:
    import docx  # python-docx

    require_office(f, ".docx")
    try:
        document = docx.Document(f)
    except _PARSE_ERRORS as e:
        raise ReaderError(f"Word file is corrupted ({type(e).__name__})")

    segments = []
    for i, paragraph in enumerate(document.paragraphs, start=1):
        if paragraph.text.strip():
            segments.append(Segment(f"paragraph {i}", paragraph.text))
    for t, table in enumerate(document.tables, start=1):
        for r, row in enumerate(table.rows, start=1):
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                segments.append(Segment(f"table {t} row {r}", " | ".join(cells)))
    return segments


def read_pptx(f: BinaryIO) -> list[Segment]:
    from pptx import Presentation  # python-pptx

    require_office(f, ".pptx")
    try:
        presentation = Presentation(f)
    except _PARSE_ERRORS as e:
        raise ReaderError(f"PowerPoint file is corrupted ({type(e).__name__})")

    segments = []
    for number, slide in enumerate(presentation.slides, start=1):
        texts = [shape.text_frame.text for shape in slide.shapes
                 if shape.has_text_frame and shape.text_frame.text.strip()]
        if texts:
            segments.append(Segment(f"slide {number}", "\n".join(texts)))
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text
            if notes.strip():
                segments.append(Segment(f"slide {number} notes", notes))
    return segments


def read_xlsx(f: BinaryIO) -> list[Segment]:
    import openpyxl

    require_office(f, ".xlsx")
    try:
        # read_only: streams the file; data_only: saved values, formulas are NOT evaluated.
        workbook = openpyxl.load_workbook(f, read_only=True, data_only=True)
    except _PARSE_ERRORS as e:
        raise ReaderError(f"Excel file is corrupted ({type(e).__name__})")

    segments = []
    try:
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows():
                # Only non-empty cells, each with its address (e.g. "G4: value"),
                # so a conclusion can later point to the exact cell.
                # (Empty cells in read-only mode have no address, so we filter first.)
                filled = [cell for cell in row
                          if cell.value is not None and str(cell.value).strip()]
                if filled:
                    text = " | ".join(f"{cell.coordinate}: {cell.value}" for cell in filled)
                    segments.append(Segment(f"sheet {sheet.title} row {filled[0].row}", text))
    finally:
        workbook.close()
    return segments
