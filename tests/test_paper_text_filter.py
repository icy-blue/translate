from __future__ import annotations

import unittest

import fitz

from backend.domain.paper_text_filter import extract_filtered_pdf_text, filter_arxiv_html_text, filter_plain_paper_text


class PaperTextFilterTest(unittest.TestCase):
    def test_filter_plain_text_removes_artifacts_but_keeps_running_references(self):
        source = "\n".join(
            [
                "1 Introduction",
                "Figure 1: Overview of the proposed pipeline.",
                "Legend: blue means source points.",
                "Figure 1 shows the overall architecture.",
                "Table 1: Quantitative results.",
                "Method   Accuracy   Runtime",
                "Ours     95.2       1.0",
                "The method improves accuracy.",
            ]
        )

        filtered = filter_plain_paper_text(source)

        self.assertIn("1 Introduction", filtered)
        self.assertIn("Figure 1 shows the overall architecture.", filtered)
        self.assertIn("The method improves accuracy.", filtered)
        self.assertNotIn("Overview of the proposed pipeline", filtered)
        self.assertNotIn("blue means source points", filtered)
        self.assertNotIn("Quantitative results", filtered)
        self.assertNotIn("Method Accuracy Runtime", filtered)
        self.assertNotIn("Ours 95.2", filtered)

    def test_filter_arxiv_html_skips_figure_table_and_caption_nodes(self):
        html = """
        <article>
          <h1>Abstract</h1>
          <p>This paper introduces a registration method.</p>
          <figure>
            <figcaption>Figure 1: A visual overview.</figcaption>
            <p>Hidden figure text.</p>
          </figure>
          <div class="ltx_table">
            <div class="ltx_caption">Table 1: Results.</div>
            <table><tr><td>Ours</td><td>95.2</td></tr></table>
          </div>
          <p>Figure 1 shows the pipeline in context.</p>
        </article>
        """

        filtered = filter_arxiv_html_text(html)

        self.assertIn("Abstract", filtered)
        self.assertIn("This paper introduces a registration method.", filtered)
        self.assertIn("Figure 1 shows the pipeline in context.", filtered)
        self.assertNotIn("A visual overview", filtered)
        self.assertNotIn("Hidden figure text", filtered)
        self.assertNotIn("Results", filtered)
        self.assertNotIn("95.2", filtered)

    def test_extract_filtered_pdf_text_removes_caption_blocks(self):
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "1 Introduction")
        page.insert_text((72, 100), "Figure 1: Overview of the proposed pipeline.")
        page.insert_text((72, 128), "Figure 1 shows the overall architecture.")
        page.insert_text((72, 156), "Table 1: Quantitative results.")
        page.insert_text((72, 184), "Method   Accuracy   Runtime")
        page.insert_text((72, 212), "Ours     95.2       1.0")
        page.insert_text((72, 240), "The method improves accuracy.")
        pdf_bytes = document.tobytes()
        document.close()

        filtered = extract_filtered_pdf_text(pdf_bytes)

        self.assertIn("1 Introduction", filtered)
        self.assertIn("Figure 1 shows the overall architecture.", filtered)
        self.assertIn("The method improves accuracy.", filtered)
        self.assertNotIn("Overview of the proposed pipeline", filtered)
        self.assertNotIn("Quantitative results", filtered)
        self.assertNotIn("Method Accuracy Runtime", filtered)
        self.assertNotIn("Ours 95.2", filtered)


if __name__ == "__main__":
    unittest.main()
