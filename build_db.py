"""Utility for building the SQLite database from PDF case files."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Iterable, Iterator, List, Sequence

PROCESS_NUMBER_RE = re.compile(
    r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b"
)
MOVEMENT_RE = re.compile(
    r"^(?P<date>\d{2}/\d{2}/\d{4})\s+-\s+(?P<description>.+)$",
    flags=re.MULTILINE,
)


class BuildDbError(Exception):
    """Base exception for build_db related errors."""


class PdfTextExtractionError(BuildDbError):
    """Raised when a PDF cannot be converted to text."""


class PdfImportResult:
    """Holds information extracted from a processed PDF."""

    def __init__(
        self,
        process_number: str,
        title: str,
        events: Sequence[tuple[str, str]],
        document_text: str,
        stored_path: Path,
    ) -> None:
        self.process_number = process_number
        self.title = title
        self.events = list(events)
        self.document_text = document_text
        self.stored_path = stored_path

    def to_document_record(self) -> tuple[str, str, str]:
        """Return the tuple used to persist documents."""

        return (
            self.process_number,
            self.stored_path.name,
            self.document_text,
        )


def extract_text_from_pdf(pdf_path: Path, destination: Path) -> str:
    """Extract text from *pdf_path* using the ``pdftotext`` command.

    Parameters
    ----------
    pdf_path:
        The path to the PDF file that should be converted into a text file.
    destination:
        Path where the temporary text file will be stored. The file will be
        removed after its content is read.

    Returns
    -------
    str
        The textual content of the PDF.

    Raises
    ------
    PdfTextExtractionError
        If ``pdftotext`` is not available or the conversion fails.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["pdftotext", str(pdf_path), str(destination)],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - depends on environment
        raise PdfTextExtractionError(
            "O utilitário 'pdftotext' não está disponível no sistema."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise PdfTextExtractionError(
            f"Falha ao converter '{pdf_path.name}' para texto: {exc.stderr!r}"
        ) from exc

    text = destination.read_text(encoding="utf-8", errors="ignore")
    destination.unlink(missing_ok=True)
    return text


def _parse_case_number(text: str) -> str:
    """Return the first process number found in ``text``."""

    match = PROCESS_NUMBER_RE.search(text)
    if not match:
        raise BuildDbError(
            "Não foi possível identificar o número do processo no documento."
        )
    return match.group(0)


def _parse_events(text: str) -> List[tuple[str, str]]:
    """Extract movement events from the document text."""

    events = [(m.group("date"), m.group("description").strip()) for m in MOVEMENT_RE.finditer(text)]
    if events:
        return events

    # Fallback: attempt to detect lines containing a date without the dash.
    fallback_events: List[tuple[str, str]] = []
    date_re = re.compile(r"^(\d{2}/\d{2}/\d{4})\s+(.*)$", flags=re.MULTILINE)
    for match in date_re.finditer(text):
        fallback_events.append((match.group(1), match.group(2).strip()))
    return fallback_events


def _derive_title(text: str) -> str:
    """Return a user friendly title for the process."""

    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned[:200]
    return "Processo sem título"


def process_pdf(pdf_path: Path, storage_dir: Path, temp_dir: Path) -> PdfImportResult:
    """Process the given PDF, importing it to the storage directory."""

    storage_dir.mkdir(parents=True, exist_ok=True)
    target_path = storage_dir / pdf_path.name
    if pdf_path.resolve() != target_path.resolve():
        shutil.copy2(pdf_path, target_path)

    text_destination = temp_dir / f"{pdf_path.stem}.txt"
    text_content = extract_text_from_pdf(target_path, text_destination)

    process_number = _parse_case_number(text_content)
    events = _parse_events(text_content)
    title = _derive_title(text_content)

    return PdfImportResult(
        process_number=process_number,
        title=title,
        events=events,
        document_text=text_content,
        stored_path=target_path,
    )


def _iter_pdf_files(directory: Path) -> Iterator[Path]:
    for path in sorted(directory.glob("*.pdf")):
        if path.is_file():
            yield path


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create database tables when they do not exist."""

    cursor = connection.cursor()
    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS processes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            pdf_path TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            process_id INTEGER NOT NULL,
            event_date TEXT NOT NULL,
            description TEXT NOT NULL,
            FOREIGN KEY(process_id) REFERENCES processes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            process_id INTEGER NOT NULL,
            file_name TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(process_id) REFERENCES processes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            process_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            start_at TEXT NOT NULL,
            notes TEXT,
            FOREIGN KEY(process_id) REFERENCES processes(id) ON DELETE CASCADE
        );
        """
    )
    connection.commit()


def _get_process_id(connection: sqlite3.Connection, process_number: str, title: str, pdf_path: Path) -> int:
    cursor = connection.cursor()
    cursor.execute(
        """
        INSERT INTO processes (number, title, pdf_path, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(number) DO UPDATE SET
            title = excluded.title,
            pdf_path = excluded.pdf_path
        """,
        (
            process_number,
            title,
            str(pdf_path),
            dt.datetime.utcnow().isoformat(),
        ),
    )
    connection.commit()
    cursor.execute(
        "SELECT id FROM processes WHERE number = ?",
        (process_number,),
    )
    row = cursor.fetchone()
    if row is None:  # pragma: no cover - defensive
        raise BuildDbError("Falha ao obter o ID do processo recém-criado.")
    return int(row[0])


def persist_import_results(
    connection: sqlite3.Connection,
    results: Iterable[PdfImportResult],
) -> None:
    """Persist the provided import results into the database."""

    for result in results:
        process_id = _get_process_id(
            connection,
            process_number=result.process_number,
            title=result.title,
            pdf_path=result.stored_path,
        )

        cursor = connection.cursor()
        # Remove previous events/documents for a clean import.
        cursor.execute("DELETE FROM events WHERE process_id = ?", (process_id,))
        cursor.execute("DELETE FROM documents WHERE process_id = ?", (process_id,))

        event_rows = [
            (process_id, event_date, description)
            for event_date, description in result.events
        ]
        if event_rows:
            cursor.executemany(
                "INSERT INTO events (process_id, event_date, description) VALUES (?, ?, ?)",
                event_rows,
            )

        cursor.execute(
            """
            INSERT INTO documents (process_id, file_name, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                process_id,
                result.stored_path.name,
                result.document_text,
                dt.datetime.utcnow().isoformat(),
            ),
        )
        connection.commit()


def load_pdf_results(pdf_dir: Path, storage_dir: Path, temp_dir: Path) -> List[PdfImportResult]:
    """Process all PDFs within ``pdf_dir`` and return the extracted data."""

    results: List[PdfImportResult] = []
    for pdf_path in _iter_pdf_files(pdf_dir):
        results.append(process_pdf(pdf_path, storage_dir=storage_dir, temp_dir=temp_dir))
    return results


def build_database(source_dir: Path, database_path: Path) -> None:
    """Entry point for building the database from ``source_dir``."""

    storage_dir = database_path.parent / "pdfs"
    temp_dir = database_path.parent / "tmp"

    results = load_pdf_results(source_dir, storage_dir, temp_dir)

    connection = sqlite3.connect(database_path)
    try:
        ensure_schema(connection)
        persist_import_results(connection, results)
    finally:
        connection.close()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        type=Path,
        help="Diretório contendo os PDFs a serem importados.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("db.sqlite3"),
        help="Caminho para o arquivo SQLite onde os dados serão gravados.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    source_dir: Path = args.source
    if not source_dir.exists() or not source_dir.is_dir():
        raise BuildDbError(f"O diretório de origem '{source_dir}' é inválido.")
    build_database(source_dir, args.database)


if __name__ == "__main__":  # pragma: no cover - entry point
    try:
        main()
    except BuildDbError as error:
        print(json.dumps({"error": str(error)}))
        raise SystemExit(1)
