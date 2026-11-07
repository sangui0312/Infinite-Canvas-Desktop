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
from agent_canvas_ops import CanvasOperations, named_agent_outputs
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

    def test_video_delivery_retains_regeneration_source_on_canvas(self):
        operation=self.output()
        operation['outputs']=[{'url':'/output/video.mp4','kind':'video','width':768,'height':1344,
                              'h3_request':{'model':'MiniMax-H3','resolution':'768P','task_id':'source'},'video_warning':''}]
        result=self.ops.apply('c','video-delivery',operation)
        output=next(n for n in result['canvas']['nodes'] if n['id'] in result['node_ids'])['images'][0]
        self.assertEqual(output['h3_request']['task_id'],'source')

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

    def reserve(self):
        return self.ops.reserve_output('c', 'slot', {'run_id':'run', 'step_id':'step-1', 'title':'白底主图',
                                                   'prompt':'fixture', 'ratio':'3:4', 'source_node_ids':['a']})

    def test_placeholder_progress_completion_deduplication_and_undo(self):
        slot = self.reserve()['node_ids'][0]
        self.assertEqual(self.reserve()['node_ids'], [slot])
        waiting = self.ops.update_output('c','slot','waiting',started_at=100)['canvas']['nodes'][1]
        self.assertTrue(waiting['pending'])
        self.assertFalse(waiting['queued'])
        output = [{'url':'/output/result.png','kind':'image','width':1024,'height':1024}]
        finished = self.ops.update_output('c','slot','delivering',outputs=output)['canvas']
        node = finished['nodes'][1]
        self.assertEqual(node['id'],slot)
        self.assertEqual(node['agentStatus'],'completed')
        self.assertFalse(node['pending'])
        self.assertGreater(node['runFinishedAt'],100)
        self.ops.update_output('c','slot','delivering',outputs=output)
        self.assertEqual(len(self.ops.read('c')['nodes']),2)
        self.ops.undo('c','slot')
        self.assertEqual(len(self.ops.read('c')['nodes']),1)

    def test_placeholder_manual_layout_survives_and_deleted_slot_is_not_recreated(self):
        slot = self.reserve()['node_ids'][0]
        canvas = self.ops.read('c')
        canvas['nodes'][1].update(x=777,y=666,w=222,title='手动改名')
        self.ops.save(canvas)
        output = [{'url':'/output/result.png','kind':'image'}]
        node = self.ops.update_output('c','slot','completed',outputs=output)['canvas']['nodes'][1]
        self.assertEqual((node['x'],node['y'],node['w'],node['title']),(777,666,222,'手动改名'))
        canvas = self.ops.read('c');canvas['nodes'] = canvas['nodes'][:1];self.ops.save(canvas)
        with self.assertRaises(HTTPException):self.ops.update_output('c','slot','completed',outputs=output)
        self.assertEqual(len(self.ops.read('c')['nodes']),1)

    def test_placeholder_edited_media_is_preserved_and_extra_outputs_are_reversible(self):
        self.reserve()
        canvas = self.ops.read('c');canvas['nodes'][1]['images']=[{'url':'/assets/manual.png'}];self.ops.save(canvas)
        with self.assertRaises(HTTPException):self.ops.update_output('c','slot','submitting')
        with self.assertRaises(HTTPException):
            self.ops.update_output('c','slot','completed',outputs=[{'url':'/output/result.png'}])
        self.assertEqual(self.ops.read('c')['nodes'][1]['images'][0]['url'],'/assets/manual.png')
        canvas=self.ops.read('c');canvas['nodes'][1]['images']=[];self.ops.save(canvas)
        result=self.ops.update_output('c','slot','completed',outputs=[{'url':'/output/result.mp4','kind':'video'},{'url':'/output/frame.png','kind':'image'}])
        self.assertEqual(len(result['node_ids']),2)
        self.assertEqual(len(result['canvas']['nodes']),3)
        self.ops.undo('c','slot')
        self.assertEqual(len(self.ops.read('c')['nodes']),1)

    def test_agent_names_keep_original_filenames_and_actual_extensions(self):
        outputs=[{'url':'/output/online_123.svg','kind':'image','name':'online_123.svg'},
                 {'url':'/output/online_456.gif','kind':'image'}]
        named=named_agent_outputs(outputs,'02/动力:320W.png')
        self.assertEqual([item['name'] for item in named],['02_动力_320W_01.svg','02_动力_320W_02.gif'])
        self.assertEqual(named[0]['original_name'],'online_123.svg')
        self.assertEqual(named_agent_outputs(named,'02/动力:320W.png'),named)
        self.assertNotIn('agent_name',outputs[0])
        self.assertEqual(named_agent_outputs([{'url':'/output/a.png'}],'CON')[0]['name'],'_CON.png')

    def test_legacy_names_backfill_keeps_manual_names_and_undo_receipts(self):
        operation={**self.output(),'run_id':'run','step_id':'step-1'}
        result=self.ops.apply('c','legacy',operation)
        outputs=named_agent_outputs(operation['outputs'],'02 动力')
        self.ops.sync_output_names('c','run','step-1',outputs)
        node=self.ops.read('c')['nodes'][1]
        self.assertEqual(node['images'][0]['name'],'02 动力.png')
        self.assertEqual(node['images'][0]['original_name'],'test.png')
        self.ops.undo('c','legacy')
        self.assertEqual(len(self.ops.read('c')['nodes']),1)
        self.ops.apply('c','manual-name',operation)
        canvas=self.ops.read('c');canvas['nodes'][1]['images'][0]['name']='用户命名.png';self.ops.save(canvas)
        self.ops.sync_output_names('c','run','step-1',outputs)
        self.assertEqual(self.ops.read('c')['nodes'][1]['images'][0]['name'],'用户命名.png')
        with self.assertRaises(HTTPException):self.ops.undo('c','manual-name')


if __name__ == '__main__': unittest.main()
