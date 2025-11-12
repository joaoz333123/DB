"""Tkinter interface for browsing legal cases stored in the SQLite database."""
from __future__ import annotations

import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

DATABASE_PATH = Path("db.sqlite3")


@dataclass
class ProcessRecord:
    """Representation of a process stored in the database."""

    id: int
    number: str
    title: str
    pdf_path: str


@dataclass
class EventRecord:
    """Representation of a process event."""

    id: int
    process_id: int
    event_date: str
    description: str


@dataclass
class DocumentRecord:
    """Representation of a document stored in the database."""

    id: int
    process_id: int
    file_name: str
    content: str


@dataclass
class AppointmentRecord:
    """Representation of an appointment associated with a process."""

    id: int
    process_id: int
    title: str
    start_at: str
    notes: str | None


class Database:
    """Simple wrapper around SQLite operations used by the UI."""

    def __init__(self, database_path: Path) -> None:
        self._conn = sqlite3.connect(database_path)
        self._conn.row_factory = sqlite3.Row

    def fetch_processes(self) -> list[ProcessRecord]:
        cursor = self._conn.execute(
            "SELECT id, number, title, pdf_path FROM processes ORDER BY title"
        )
        return [
            ProcessRecord(
                id=row["id"],
                number=row["number"],
                title=row["title"],
                pdf_path=row["pdf_path"],
            )
            for row in cursor.fetchall()
        ]

    def fetch_events(self, process_id: int) -> list[EventRecord]:
        cursor = self._conn.execute(
            """
            SELECT id, process_id, event_date, description
            FROM events
            WHERE process_id = ?
            ORDER BY event_date DESC
            """,
            (process_id,),
        )
        return [
            EventRecord(
                id=row["id"],
                process_id=row["process_id"],
                event_date=row["event_date"],
                description=row["description"],
            )
            for row in cursor.fetchall()
        ]

    def fetch_documents(self, process_id: int) -> list[DocumentRecord]:
        cursor = self._conn.execute(
            """
            SELECT id, process_id, file_name, content
            FROM documents
            WHERE process_id = ?
            ORDER BY id DESC
            """,
            (process_id,),
        )
        return [
            DocumentRecord(
                id=row["id"],
                process_id=row["process_id"],
                file_name=row["file_name"],
                content=row["content"],
            )
            for row in cursor.fetchall()
        ]

    def fetch_appointments(self, process_id: int) -> list[AppointmentRecord]:
        cursor = self._conn.execute(
            """
            SELECT id, process_id, title, start_at, notes
            FROM appointments
            WHERE process_id = ?
            ORDER BY start_at
            """,
            (process_id,),
        )
        return [
            AppointmentRecord(
                id=row["id"],
                process_id=row["process_id"],
                title=row["title"],
                start_at=row["start_at"],
                notes=row["notes"],
            )
            for row in cursor.fetchall()
        ]

    def add_appointment(self, process_id: int, title: str, start_at: str, notes: str) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO appointments (process_id, title, start_at, notes)
            VALUES (?, ?, ?, ?)
            """,
            (process_id, title, start_at, notes or None),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def delete_appointment(self, appointment_id: int) -> None:
        self._conn.execute("DELETE FROM appointments WHERE id = ?", (appointment_id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


class CaseManagerApp:
    """Tkinter graphical interface for the case database."""

    def __init__(self, root: tk.Tk, database: Database) -> None:
        self.root = root
        self.db = database
        self.selected_process: ProcessRecord | None = None
        self.selected_document: DocumentRecord | None = None
        self.selected_appointment: AppointmentRecord | None = None
        self._process_cache: list[ProcessRecord] = []
        self._documents_cache: list[DocumentRecord] = []

        self.root.title("Gestor de Processos")
        self.root.geometry("1100x700")
        self._create_widgets()
        self._populate_processes()

    # ------------------------------------------------------------------
    # Widget creation
    # ------------------------------------------------------------------
    def _create_widgets(self) -> None:
        main_paned = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True)

        self.process_tree = ttk.Treeview(
            main_paned,
            columns=("number", "title"),
            show="headings",
            height=20,
        )
        self.process_tree.heading("number", text="Número")
        self.process_tree.heading("title", text="Título")
        self.process_tree.column("number", width=200)
        self.process_tree.column("title", width=300)
        self.process_tree.bind("<<TreeviewSelect>>", self._on_process_select)
        main_paned.add(self.process_tree, weight=1)

        right_frame = ttk.Frame(main_paned)
        main_paned.add(right_frame, weight=3)

        notebook = ttk.Notebook(right_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        # Events tab
        events_frame = ttk.Frame(notebook, padding=10)
        notebook.add(events_frame, text="Movimentações")
        self.events_tree = ttk.Treeview(
            events_frame,
            columns=("date", "description"),
            show="headings",
            height=15,
        )
        self.events_tree.heading("date", text="Data")
        self.events_tree.heading("description", text="Descrição")
        self.events_tree.column("date", width=120, anchor=tk.CENTER)
        self.events_tree.column("description", width=550)
        self.events_tree.pack(fill=tk.BOTH, expand=True)

        # Documents tab
        documents_frame = ttk.Frame(notebook, padding=10)
        notebook.add(documents_frame, text="Documentos")
        doc_paned = ttk.Panedwindow(documents_frame, orient=tk.HORIZONTAL)
        doc_paned.pack(fill=tk.BOTH, expand=True)

        self.document_list = tk.Listbox(doc_paned, exportselection=False)
        self.document_list.bind("<<ListboxSelect>>", self._on_document_select)
        doc_paned.add(self.document_list, weight=1)

        self.document_text = tk.Text(doc_paned, wrap=tk.WORD)
        self.document_text.configure(state=tk.DISABLED)
        doc_paned.add(self.document_text, weight=3)

        # Appointments tab
        appointments_frame = ttk.Frame(notebook, padding=10)
        notebook.add(appointments_frame, text="Compromissos")

        self.appointments_tree = ttk.Treeview(
            appointments_frame,
            columns=("title", "start_at", "notes"),
            show="headings",
            height=10,
        )
        self.appointments_tree.heading("title", text="Título")
        self.appointments_tree.heading("start_at", text="Início")
        self.appointments_tree.heading("notes", text="Notas")
        self.appointments_tree.column("title", width=200)
        self.appointments_tree.column("start_at", width=160)
        self.appointments_tree.column("notes", width=300)
        self.appointments_tree.bind(
            "<<TreeviewSelect>>", self._on_appointment_select
        )
        self.appointments_tree.pack(fill=tk.BOTH, expand=True)

        form_frame = ttk.LabelFrame(appointments_frame, text="Novo compromisso")
        form_frame.pack(fill=tk.X, pady=10)

        ttk.Label(form_frame, text="Título:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.title_entry = ttk.Entry(form_frame)
        self.title_entry.grid(row=0, column=1, sticky=tk.EW, padx=5, pady=5)

        ttk.Label(form_frame, text="Início (YYYY-MM-DD HH:MM):").grid(
            row=1, column=0, sticky=tk.W, padx=5, pady=5
        )
        self.start_entry = ttk.Entry(form_frame)
        self.start_entry.grid(row=1, column=1, sticky=tk.EW, padx=5, pady=5)

        ttk.Label(form_frame, text="Notas:").grid(row=2, column=0, sticky=tk.NW, padx=5, pady=5)
        self.notes_text = tk.Text(form_frame, height=4)
        self.notes_text.grid(row=2, column=1, sticky=tk.EW, padx=5, pady=5)

        form_frame.columnconfigure(1, weight=1)

        button_frame = ttk.Frame(appointments_frame)
        button_frame.pack(fill=tk.X)

        add_button = ttk.Button(button_frame, text="Adicionar", command=self._add_appointment)
        add_button.pack(side=tk.LEFT, padx=5)

        delete_button = ttk.Button(
            button_frame,
            text="Excluir selecionado",
            command=self._delete_selected_appointment,
        )
        delete_button.pack(side=tk.LEFT, padx=5)

    # ------------------------------------------------------------------
    # Data population helpers
    # ------------------------------------------------------------------
    def _populate_processes(self) -> None:
        for item in self.process_tree.get_children():
            self.process_tree.delete(item)
        self._process_cache = self.db.fetch_processes()
        for process in self._process_cache:
            self.process_tree.insert("", tk.END, iid=str(process.id), values=(process.number, process.title))
        if self.process_tree.get_children():
            first_item = self.process_tree.get_children()[0]
            self.process_tree.selection_set(first_item)
            self.process_tree.focus(first_item)

    def _populate_events(self, events: Iterable[EventRecord]) -> None:
        for item in self.events_tree.get_children():
            self.events_tree.delete(item)
        for event in events:
            self.events_tree.insert(
                "",
                tk.END,
                iid=str(event.id),
                values=(event.event_date, event.description),
            )

    def _populate_documents(self, documents: Sequence[DocumentRecord]) -> None:
        self.document_list.delete(0, tk.END)
        for document in documents:
            self.document_list.insert(tk.END, f"{document.file_name}")
        self._documents_cache = list(documents)
        if documents:
            self.document_list.selection_set(0)
            self._show_document_text(documents[0])
        else:
            self._clear_document_text()

    def _populate_appointments(self, appointments: Sequence[AppointmentRecord]) -> None:
        for item in self.appointments_tree.get_children():
            self.appointments_tree.delete(item)
        for appointment in appointments:
            self.appointments_tree.insert(
                "",
                tk.END,
                iid=str(appointment.id),
                values=(appointment.title, appointment.start_at, appointment.notes or ""),
            )
        self.selected_appointment = None

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _on_process_select(self, event: tk.Event[tk.Misc]) -> None:  # noqa: ARG002 - required signature
        selection = self.process_tree.selection()
        if not selection:
            return
        process_id = int(selection[0])
        processes = {proc.id: proc for proc in self._process_cache}
        process = processes.get(process_id)
        if process is None:
            return
        self.selected_process = process
        events = self.db.fetch_events(process_id)
        documents = self.db.fetch_documents(process_id)
        appointments = self.db.fetch_appointments(process_id)
        self._populate_events(events)
        self._populate_documents(documents)
        self._populate_appointments(appointments)

    def _on_document_select(self, event: tk.Event[tk.Misc]) -> None:  # noqa: ARG002 - required signature
        selection = self.document_list.curselection()
        if not selection:
            return
        document = self._documents_cache[selection[0]]
        self.selected_document = document
        self._show_document_text(document)

    def _on_appointment_select(self, event: tk.Event[tk.Misc]) -> None:  # noqa: ARG002 - required signature
        selection = self.appointments_tree.selection()
        if not selection:
            self.selected_appointment = None
            return
        appointment_id = int(selection[0])
        appointments = self.db.fetch_appointments(self.selected_process.id) if self.selected_process else []
        lookup = {appt.id: appt for appt in appointments}
        self.selected_appointment = lookup.get(appointment_id)

    # ------------------------------------------------------------------
    # Document helpers
    # ------------------------------------------------------------------
    def _show_document_text(self, document: DocumentRecord) -> None:
        self.document_text.configure(state=tk.NORMAL)
        self.document_text.delete("1.0", tk.END)
        self.document_text.insert(tk.END, document.content)
        self.document_text.configure(state=tk.DISABLED)

    def _clear_document_text(self) -> None:
        self.document_text.configure(state=tk.NORMAL)
        self.document_text.delete("1.0", tk.END)
        self.document_text.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------
    # Appointment actions
    # ------------------------------------------------------------------
    def _add_appointment(self) -> None:
        if not self.selected_process:
            messagebox.showinfo("Seleção necessária", "Selecione um processo primeiro.")
            return
        title = self.title_entry.get().strip()
        start_at = self.start_entry.get().strip()
        notes = self.notes_text.get("1.0", tk.END).strip()

        if not title or not start_at:
            messagebox.showerror(
                "Campos obrigatórios",
                "Informe pelo menos o título e a data/hora do compromisso.",
            )
            return
        appointment_id = self.db.add_appointment(
            self.selected_process.id,
            title=title,
            start_at=start_at,
            notes=notes,
        )
        self._populate_appointments(self.db.fetch_appointments(self.selected_process.id))
        self.appointments_tree.selection_set(str(appointment_id))
        self.title_entry.delete(0, tk.END)
        self.start_entry.delete(0, tk.END)
        self.notes_text.delete("1.0", tk.END)

    def _delete_selected_appointment(self) -> None:
        if not self.selected_appointment:
            messagebox.showinfo(
                "Nenhum compromisso",
                "Selecione um compromisso para excluir.",
            )
            return
        confirm = messagebox.askyesno(
            "Confirmação",
            "Deseja realmente excluir o compromisso selecionado?",
        )
        if not confirm:
            return
        self.db.delete_appointment(self.selected_appointment.id)
        if self.selected_process:
            self._populate_appointments(
                self.db.fetch_appointments(self.selected_process.id)
            )


def main() -> None:
    if not DATABASE_PATH.exists():
        messagebox.showerror(
            "Banco de dados ausente",
            "O arquivo db.sqlite3 não foi encontrado. Execute build_db.py primeiro.",
        )
        raise SystemExit(1)

    database = Database(DATABASE_PATH)
    root = tk.Tk()
    CaseManagerApp(root, database)

    def on_close() -> None:
        database.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":  # pragma: no cover - entry point
    main()
