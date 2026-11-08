"""作者：Wangyuan；日期：2026-10-29。职责：Word段内多个run的原文保真回归。"""
import io
import unittest
import zipfile
from ledger import extract_text


class ReviewTests(unittest.TestCase):
    def test_word_runs_are_not_paragraphs(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>请于2030年1月</w:t></w:r><w:r><w:t>3日前完成资料汇总。</w:t></w:r></w:p><w:p><w:r><w:t>下一段</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(extract_text('demo.docx', stream.getvalue()), '请于2030年1月3日前完成资料汇总。\n下一段')


if __name__ == '__main__': unittest.main()
