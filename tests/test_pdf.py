"""作者：Wangyuan；日期：2026-10-29。职责：有限PDF提取和拒绝路径回归。"""
import unittest
from ledger import extract_text


class PdfTests(unittest.TestCase):
    def test_literal_escape_and_line_continuation(self):
        data = b'%PDF-1.4\n(Hello\\040world) (line\\\r\ncontinued)\n%%EOF'
        self.assertEqual(extract_text('sample.pdf', data), 'Hello world\nlinecontinued')

    def test_nested_literals(self):
        self.assertEqual(extract_text('sample.pdf', b'%PDF-1.4\n(outer (inner))\n%%EOF'), 'outer (inner)')

    def test_unsupported_and_active_documents_rejected(self):
        for marker in [b'/Filter /FlateDecode', b'/ObjStm', b'/ToUnicode', b'<4142>', b'/Encrypt', b'/JavaScript']:
            with self.subTest(marker=marker), self.assertRaises(ValueError):
                extract_text('sample.pdf', b'%PDF-1.4\n' + marker + b'\n(sample)\n%%EOF')

    def test_corrupt_and_depth_rejected(self):
        for data in [b'not pdf', b'%PDF-1.4\n(unclosed\n%%EOF', b'%PDF-1.4\n' + b'('*40 + b')'*40 + b'\n%%EOF']:
            with self.assertRaises(ValueError): extract_text('sample.pdf', data)


if __name__ == '__main__': unittest.main()
