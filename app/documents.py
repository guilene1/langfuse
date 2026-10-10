# 1. Markdown Reader
import csv
import re
from docx import Document
from pypdf import PdfReader
def read_md(path):
    text = path.read_text(encoding="utf-8")
    sections = []
    heading = "Introduction"
    body = []
    for line in text.splitlines():
        match = re.match(r"^(\#{1,3})\s+(.*)$", line)
        if match:
            if body and "\n".join(body).strip():
                sections.append((heading, "\n".join(body).strip()))
            heading = match.group(2).strip()
            body = []
        else:
            body.append(line)
    if body and "\n".join(body).strip():
        sections.append((heading, "\n".join(body).strip()))
    return sections
# 2. Word Reader
def read_docx(path):
    # Text before the first heading (document ID, version, who it applies to) goes in an
    # "Introduction" section, the same as read_md, rather than being silently dropped.
    sections = [("Introduction", "")]
    for paragraph in Document(str(path)).paragraphs:
        if paragraph.style.name.startswith("Heading"):
            sections.append((paragraph.text, ""))
        elif paragraph.text.strip():
            heading, body = sections[-1]
            sections[-1] = (heading, body + paragraph.text + "\n")
    return sections
# 3. HTML Reader
def read_html(path):
    from html.parser import HTMLParser
    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
        def handle_data(self, data):
            text = data.strip()
            if text:
                self.parts.append(text)
    parser = TextExtractor()
    parser.feed(path.read_text(encoding="utf-8"))
    text = "\n".join(parser.parts).strip()
    return [("Introduction", text)] if text else []
# 1. Dispatch
def read_document(path):
    suffix = path.suffix.lower()
    if suffix == ".md": return read_md(path)
    if suffix == ".docx": return read_docx(path)
    if suffix == ".pdf": return read_pdf(path)
    if suffix == ".csv": return read_csv(path)
    if suffix == ".txt": return read_txt(path)
    if suffix in (".html", ".htm"): return read_html(path)
    raise ValueError(
        f"Unsupported document type: {path.name}"
    )
def read_pdf(path):
    pages = PdfReader(path).pages
 
    return [
        (
            f"Page {number}",
            page.extract_text() or "",
        )
        for number, page in enumerate(pages, start=1)
    ]
 
def read_csv(path):
    with open(path, encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
 
    # The generator writes a provenance comment as the first row;
    # the real header follows.
    if rows and rows[0] and rows[0][0].startswith("#"):
        rows = rows[1:]
 
    if not rows:
        return []
 
    header, data = rows[0], rows[1:]
    sections = []
 
    for row in data:
        if not any(cell.strip() for cell in row):
            continue
 
        line = ", ".join(f"{column}: {value}" for column, value in zip(header, row))
 
        # Name the section after the first column that is not a date.
        label = next((value for column, value in zip(header, row) if value.strip() and column.lower() not in ("date", "day")), "Row")
        sections.append((label, line))
 
    return sections
 
def read_txt(path):
    text = path.read_text(encoding="utf-8").strip()
    return [("Introduction", text)] if text else []
