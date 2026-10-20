import copy
import json
from pathlib import Path
import sys
import tempfile
from threading import RLock
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend/static/agent-runtime'))
from agent_canvas_ops import CanvasOperations
from fastapi import HTTPException


class CanvasOpsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'tests', prefix='backend-test-agent-')
        self.root = Path(self.tmp.name)
        self.ops = CanvasOperations(lambda key: str(self.root / (key + '.json')), RLock())
        self.canvas = {'id':'c','kind':'smart','nodes':[{'id':'a','type':'smart-prompt','text':'original','x':1,'y':2}], 'connections':[]}
        self.ops.save(self.canvas)

    def tearDown(self):
        self.tmp.cleanup()

    def output(self):
        return {'type':'create_outputs','outputs':[{'url':'/assets/output/test.png','kind':'image'}],'source_node_ids':['a'],'title':'结果'}

    def test_delivery_receipt_is_atomic_deduped_and_preserved(self):
        first = self.ops.apply('c','op1',self.output())
        second = self.ops.apply('c','op1',self.output())
        self.assertEqual(first['node_ids'], second['node_ids'])
        self.assertEqual(len(second['canvas']['nodes']), 2)
        self.assertIn('op1', self.ops.read('c')['agent_operation_receipts'])
        changed = self.output(); changed['title']='different'
        with self.assertRaises(HTTPException): self.ops.apply('c','op1',changed)

    def test_compare_and_write_rejects_stale_snapshot(self):
        old = self.ops.read('c')
        self.ops.apply('c','op1',self.output())
        with self.assertRaises(HTTPException) as error: self.ops.save(old)
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(len(self.ops.read('c')['nodes']),2)

    def test_undo_keeps_user_changes_and_refuses_changed_target(self):
        result=self.ops.apply('c','op1',self.output()); node_id=result['node_ids'][0]
        current=self.ops.read('c');current['nodes'][0]['x']=55;self.ops.save(current)
        undone=self.ops.undo('c','op1')['canvas']
        self.assertEqual(undone['nodes'][0]['x'],55)
        self.assertIn(node_id,undone['agent_deleted_nodes'])
        self.assertTrue(self.ops.undo('c','op1')['undone'])
        replay=self.ops.apply('c','op1',self.output());self.assertTrue(replay['undone'])
        self.assertEqual(len(replay['canvas']['nodes']),1)

    def test_user_edit_to_generated_node_blocks_undo(self):
        result=self.ops.apply('c','op1',self.output());current=self.ops.read('c');current['nodes'][1]['title']='manual';self.ops.save(current)
        with self.assertRaises(HTTPException): self.ops.undo('c','op1')
        self.assertEqual(len(self.ops.read('c')['nodes']),2)

    def test_loader_cache_does_not_block_undo(self):
        self.ops.apply('c','op1',self.output());current=self.ops.read('c')
        current['nodes'][1]['images'][0].update(natural_w=256,natural_h=256,_naturalSizeLoading=False)
        self.ops.save(current)
        self.ops.undo('c','op1')
        self.assertEqual(len(self.ops.read('c')['nodes']),1)

    def test_changed_generation_settings_prevents_undo(self):
        self.ops.apply('c','op1',self.output());current=self.ops.read('c')
        current['nodes'][1]['runSettings']['background']='transparent';self.ops.save(current)
        with self.assertRaises(HTTPException):self.ops.undo('c','op1')

    def test_field_undo_preserves_later_position_change(self):
        op={'type':'write_prompt','node_id':'a','text':'agent','expected_fields':{'text':'original'}}
        self.ops.apply('c','edit',op);current=self.ops.read('c');current['nodes'][0]['x']=99;self.ops.save(current)
        self.ops.undo('c','edit');node=self.ops.read('c')['nodes'][0]
        self.assertEqual(node['text'],'original');self.assertEqual(node['x'],99)
        with self.assertRaises(HTTPException): self.ops.apply('c','stale',{**op,'expected_fields':{'text':'agent'}})

    def test_failed_atomic_replace_keeps_old_canvas(self):
        before=(self.root/'c.json').read_bytes()
        with patch('agent_canvas_ops.os.replace',side_effect=OSError('locked')):
            with self.assertRaises(OSError): self.ops.apply('c','op1',self.output())
        self.assertEqual((self.root/'c.json').read_bytes(),before)
        self.assertEqual(list(self.root.glob('*.tmp')),[])

    def test_connect_cycle_rejected_and_edge_only_undo(self):
        c=self.ops.read('c');c['nodes'].append({'id':'b','type':'smart-image'});self.ops.save(c)
        self.ops.apply('c','edge',{'type':'connect','from':'a','to':'b'})
        with self.assertRaises(HTTPException):self.ops.apply('c','cycle',{'type':'connect','from':'b','to':'a'})
        self.ops.undo('c','edge');self.assertEqual(self.ops.read('c')['connections'],[])


if __name__ == '__main__': unittest.main()
