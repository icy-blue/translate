from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlmodel import SQLModel, Session, create_engine

from backend.platform.models import Conversation, FileRecord
from scripts.backfill_local_files import backfill_record


class BackfillLocalFilesTest(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        self.addCleanup(Path(self.db_file.name).unlink, missing_ok=True)
        self.files_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.files_dir.cleanup)
        self.engine = create_engine(f"sqlite:///{self.db_file.name}")
        SQLModel.metadata.create_all(self.engine)

    def _seed_file_record(self, poe_url: str = "data:application/pdf;base64,JVBERg==") -> None:
        with Session(self.engine) as session:
            session.add(Conversation(id="conv-1", title="Paper", original_filename="paper.pdf"))
            session.add(
                FileRecord(
                    id="file-1",
                    conversation_id="conv-1",
                    filename="paper.pdf",
                    fingerprint="fp-1",
                    poe_url=poe_url,
                    content_type="application/pdf",
                    poe_name="paper.pdf",
                )
            )
            session.commit()

    def test_backfill_data_url_writes_local_file_and_updates_record(self):
        encoded = base64.b64encode(b"%PDF-data").decode("ascii")
        self._seed_file_record(f"data:application/pdf;base64,{encoded}")

        with patch("backend.platform.local_files.LOCAL_FILES_DIR", Path(self.files_dir.name)):
            with Session(self.engine) as session:
                record = session.get(FileRecord, "file-1")
                status = backfill_record(session, record, timeout=60, overwrite=False, dry_run=False)

            self.assertEqual(status, "updated")
            self.assertEqual((Path(self.files_dir.name) / "conv-1" / "file-1.pdf").read_bytes(), b"%PDF-data")
            with Session(self.engine) as session:
                self.assertEqual(session.get(FileRecord, "file-1").poe_url, "/files/conv-1/file-1.pdf")

    def test_backfill_existing_local_file_skips(self):
        local_path = Path(self.files_dir.name) / "conv-1" / "file-1.pdf"
        local_path.parent.mkdir(parents=True)
        local_path.write_bytes(b"%PDF-local")
        self._seed_file_record("/files/conv-1/file-1.pdf")

        with patch("backend.platform.local_files.LOCAL_FILES_DIR", Path(self.files_dir.name)):
            with Session(self.engine) as session:
                record = session.get(FileRecord, "file-1")
                status = backfill_record(session, record, timeout=60, overwrite=False, dry_run=False)

        self.assertEqual(status, "skipped-existing")
        self.assertEqual(local_path.read_bytes(), b"%PDF-local")

    def test_backfill_dry_run_does_not_write_or_update(self):
        encoded = base64.b64encode(b"%PDF-data").decode("ascii")
        original_url = f"data:application/pdf;base64,{encoded}"
        self._seed_file_record(original_url)

        with patch("backend.platform.local_files.LOCAL_FILES_DIR", Path(self.files_dir.name)):
            with Session(self.engine) as session:
                record = session.get(FileRecord, "file-1")
                status = backfill_record(session, record, timeout=60, overwrite=False, dry_run=True)

            self.assertEqual(status, "would-update")
            self.assertFalse((Path(self.files_dir.name) / "conv-1" / "file-1.pdf").exists())
            with Session(self.engine) as session:
                self.assertEqual(session.get(FileRecord, "file-1").poe_url, original_url)

    def test_backfill_clear_unrecoverable_text_url(self):
        self._seed_file_record("text://conv-1")

        with patch("backend.platform.local_files.LOCAL_FILES_DIR", Path(self.files_dir.name)):
            with Session(self.engine) as session:
                record = session.get(FileRecord, "file-1")
                status = backfill_record(
                    session,
                    record,
                    timeout=60,
                    overwrite=False,
                    dry_run=False,
                    clear_unrecoverable=True,
                )

        self.assertEqual(status, "cleared")
        with Session(self.engine) as session:
            self.assertEqual(session.get(FileRecord, "file-1").poe_url, "")

    def test_backfill_dry_run_reports_clear_without_updating(self):
        self._seed_file_record("text://conv-1")

        with patch("backend.platform.local_files.LOCAL_FILES_DIR", Path(self.files_dir.name)):
            with Session(self.engine) as session:
                record = session.get(FileRecord, "file-1")
                status = backfill_record(
                    session,
                    record,
                    timeout=60,
                    overwrite=False,
                    dry_run=True,
                    clear_unrecoverable=True,
                )

        self.assertEqual(status, "would-clear")
        with Session(self.engine) as session:
            self.assertEqual(session.get(FileRecord, "file-1").poe_url, "text://conv-1")

    def test_backfill_empty_url_skips(self):
        self._seed_file_record("")

        with Session(self.engine) as session:
            record = session.get(FileRecord, "file-1")
            status = backfill_record(
                session,
                record,
                timeout=60,
                overwrite=False,
                dry_run=False,
                clear_unrecoverable=True,
            )

        self.assertEqual(status, "skipped-empty")


if __name__ == "__main__":
    unittest.main()
