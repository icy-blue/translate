from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.modules.assets import download_pdf_bytes


class AssetPdfDownloadTest(unittest.TestCase):
    def test_download_pdf_bytes_reads_local_file_url(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "conv-1" / "file-1.pdf"
            pdf_path.parent.mkdir(parents=True)
            pdf_path.write_bytes(b"%PDF-local")

            with patch("backend.platform.local_files.LOCAL_FILES_DIR", Path(tmpdir)):
                self.assertEqual(download_pdf_bytes("/files/conv-1/file-1.pdf"), b"%PDF-local")

    def test_download_pdf_bytes_decodes_data_url(self):
        encoded = base64.b64encode(b"%PDF-data").decode("ascii")

        self.assertEqual(download_pdf_bytes(f"data:application/pdf;base64,{encoded}"), b"%PDF-data")


if __name__ == "__main__":
    unittest.main()
