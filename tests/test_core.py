"""作者：Wangyuan；日期：2026-10-29；职责：仅以从零合成输入验证核心与异常路径。"""

import io, tempfile, unittest, zipfile
from pathlib import Path
from ledger import Ledger, candidates, coverage, confirmed_reason, extract_text

class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.store = Ledger(Path(self.directory.name) / 'ledger.sqlite')
        self.record = self.store.create('task','source-1','示例事项',received_on='2030-01-01',actor='person-1',operation_key='create',due_at='2030-01-02T00:00:00+00:00')
    def change(self, action, version, values=None):
        return self.store.change(self.record['id'], version, action=action, actor='person-1', operation_key=action+str(version), values=values)
    def test_idempotent_create_and_payload_conflict(self):
        same=self.store.create('task','source-1','示例事项',received_on='2030-01-01',actor='person-1',operation_key='create',due_at='2030-01-02T00:00:00+00:00')
        self.assertEqual(self.record,same)
        with self.assertRaises(ValueError): self.store.create('task','source-2','different',received_on='2030-01-01',actor='person-1',operation_key='create')
    def test_completion_requires_evidence_and_review(self):
        with self.assertRaises(ValueError): self.change('complete',1)
        self.change('evidence',1,{'reference':'ref-1','text':'已完成材料'})
        self.change('confirm',2,{'confirmed':True})
        self.assertEqual(self.change('complete',3)['state'],'completed')
    def test_late_evidence_invalidates_completion_and_confirmation(self):
        self.change('evidence',1,{'reference':'ref-1','text':'记录'})
        self.change('confirm',2,{'confirmed':True}); self.change('complete',3)
        revised=self.change('evidence',4,{'reference':'ref-2','text':'新增记录'})
        self.assertEqual(revised['state'],'needs_review'); self.assertEqual(revised['confirmations'],[])
    def test_optimistic_revision_and_draft_allowlist(self):
        with self.assertRaises(ValueError): self.change('revise',0,{'title':'新标题'})
        draft=self.store.draft(self.record['id'],1,'{title} {state}')
        self.assertEqual(draft['state'],'local_draft')
        with self.assertRaises(ValueError): self.store.draft(self.record['id'],1,'{title.__class__}')
        self.change('revise',1,{'title':'新标题'})
        with self.assertRaises(ValueError): self.store.draft(self.record['id'],1,'{title}')
    def test_overdue_month_filter_and_audit(self):
        result=self.store.report('2030-01-03T00:00:00+00:00','2030-01')
        self.assertEqual(result['overdue_count'],1); self.assertEqual(len(result['audit']),1)
        self.assertEqual(self.store.report('2030-01-03T00:00:00+00:00','2030-02')['records'],[])
    def test_chinese_deadline_and_unknown_metadata(self):
        result=candidates('请资料组于2030年1月3日前反馈核对结果。','2030-01-01')
        self.assertEqual(result['tasks'][0]['deadline'],'2030-01-03')
        self.assertEqual(result['confidentiality'],'待确认')
        self.assertEqual(result['tasks'][0]['status'],'待确认')
    def test_workday_calendar_coverage(self):
        calendar={'calendar_id':'demo','valid_from':'2030-01-01','valid_to':'2030-01-31','holidays':['2030-01-02'],'workdays':[]}
        result=candidates('请资料部在收到后2个工作日内反馈核对结果。','2030-01-01',calendar)
        self.assertTrue(result['tasks']); self.assertEqual(result['tasks'][0]['deadline'],'2030-01-04')
    def test_attachment_and_archive_safety(self):
        self.assertEqual(self.store.attach('a.txt',b'abc'),self.store.attach('a.txt',b'abc'))
        with self.assertRaises(ValueError): self.store.attach('../a.txt',b'abc')
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as z: z.writestr('../bad',b'a'); z.writestr('word/document.xml',b'<x/>')
        with self.assertRaises(ValueError): extract_text('x.docx',raw.getvalue())
        self.assertEqual(extract_text('a.txt','正文'.encode()),'正文')
    def test_atomic_candidate_import_and_idempotency(self):
        text='请资料组于2030年1月3日前反馈核对结果。'
        tasks=candidates(text,'2030-01-01')['tasks']
        args=dict(document_id='doc-1',text=text,tasks=tasks,received_on='2030-01-01',actor='reviewer',operation_key='import',confirmed=True)
        first=self.store.import_candidates(**args)
        self.assertEqual(len(first['records']),1)
        self.assertEqual(self.store.import_candidates(**args),first)
        self.assertEqual(first['records'][0]['deadline_date'],'2030-01-03')
        with self.assertRaises(ValueError): self.store.import_candidates(**{**args,'operation_key':'import-bad','tasks':tasks+[{'description':'bad','source_quote':'杜撰'}]})
        self.assertEqual(len(self.store.report('2030-01-04T00:00:00+00:00')['records']),2)
    def test_coverage_expiry_and_reason_evidence(self):
        result=coverage([{'category':'notes','captured_at':'2030-01-01T00:00:00+00:00','valid_until':'2030-01-02T00:00:00+00:00'}],[{'category':'notes'}],'2030-01-03T00:00:00+00:00')
        self.assertFalse(result['complete'])
        good=confirmed_reason(['other'],{'reason':'other','quotes':[{'source':'a','text':'原句'}]},{'a':'这是原句。'})
        self.assertEqual(good['state'],'candidate')
        with self.assertRaises(ValueError): confirmed_reason(['other'],{'reason':'other','quotes':[{'source':'a','text':'杜撰'}]},{'a':'原句'})

if __name__ == "__main__":
    unittest.main()
