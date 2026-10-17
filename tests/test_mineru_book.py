import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import mineru_book as tool


class MineruBookTests(unittest.TestCase):
    def test_middle_json_block_anchors_map_pages(self):
        markdown = "# Title\n\nAlpha unique paragraph.\n\nBeta unique paragraph.\n"
        payload = {
            "pages": [
                {"page_idx": 0, "blocks": [{"type": "text", "content": [{"type": "text", "content": "Alpha unique paragraph."}]}]},
                {"page_idx": 1, "blocks": [{"type": "text", "content": [{"type": "text", "content": "Beta unique paragraph."}]}]},
            ]
        }
        with tempfile.TemporaryDirectory() as folder:
            middle = Path(folder) / "middle_json.json"
            middle.write_text(json.dumps(payload), encoding="utf-8")
            annotated, entries = tool.build_mapping(markdown, ["Alpha", "Beta"], middle)
        self.assertIn("<!-- pdf-page: 1 -->", annotated)
        self.assertIn("<!-- pdf-page: 2 -->", annotated)
        self.assertTrue(all(entry.matched for entry in entries))

    def test_verify_detects_broken_image(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            markdown = root / "book.md"
            markdown.write_text("<!-- pdf-page: 1 -->\n![](images/missing.jpg)\n", encoding="utf-8")
            index = {
                "markdown": str(markdown),
                "page_count": 1,
                "pages": [{"pdf_page": 1, "matched": True}],
            }
            (root / "book.page-map.json").write_text(json.dumps(index), encoding="utf-8")
            report = tool.verify_output(root)
        self.assertFalse(report["ok"])
        self.assertEqual(report["broken_images"], ["images/missing.jpg"])

    def test_search_starts_with_unknown_page(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            markdown = root / "book.md"
            markdown.write_text("Preface match before any marker.\n", encoding="utf-8")
            index = {"markdown": str(markdown), "pages": []}
            (root / "book.page-map.json").write_text(json.dumps(index), encoding="utf-8")
            args = type("Args", (), {"output_dir": str(root), "query": "match", "regex": False})()
            output = io.StringIO()
            with redirect_stdout(output):
                tool.search(args)
        self.assertIn("PDF page unknown", output.getvalue())


if __name__ == "__main__":
    unittest.main()
