"""作者：Wangyuan；日期：2026-11-08。职责：通过合成输入复验已确认缺陷。"""
import unittest,tempfile
from pathlib import Path
from ledger import Ledger
class ReleaseTests(unittest.TestCase):
 def create(self,l):return l.create('task','source','Title',received_on='20300101',actor='a',operation_key='create')
 def test_null_revision_rejected_and_compact_date_normalized(self) -> None:
  with tempfile.TemporaryDirectory() as d:
   l=Ledger(Path(d)/'db');r=self.create(l);self.assertEqual(r['received_on'],'2030-01-01');self.assertEqual(r['number'],'2030-01-0001')
   with self.assertRaises(ValueError):l.change(r['id'],1,action='revise',actor='a',operation_key='revise',values={'title':None})
 def test_candidate_time_not_discarded(self) -> None:
  with tempfile.TemporaryDirectory() as d:
   l=Ledger(Path(d)/'db')
   with self.assertRaisesRegex(ValueError,'截止时刻'):l.import_candidates('doc','complete before 10:00',[{'description':'complete','source_quote':'complete','deadline':'2030-01-01','deadline_time':'10:00'}],received_on='2030-01-01',actor='a',operation_key='imp',confirmed=True)
 def test_completed_cannot_be_uncompleted_by_confirmation(self) -> None:
  with tempfile.TemporaryDirectory() as d:
   l=Ledger(Path(d)/'db');r=self.create(l)
   for i,(action,values) in enumerate([('evidence',{'reference':'source','text':'done'}),('confirm',{'confirmed':True}),('complete',{})],1):r=l.change(r['id'],r['version'],action=action,actor='a',operation_key=str(i),values=values)
   with self.assertRaises(ValueError):l.change(r['id'],r['version'],action='confirm',actor='a',operation_key='again',values={'confirmed':True})
