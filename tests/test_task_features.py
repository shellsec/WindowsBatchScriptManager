import tempfile
import unittest
from unittest.mock import patch, Mock
import os
from pathlib import Path
from task_features import migrate_items, template_item, scan_scripts, parse_codes, validate_links, TaskQueue, split_arguments, merge_task_items, discover_environments, environment_vars

class Features(unittest.TestCase):
    def test_ids(self):
        items = migrate_items([{'id': 7}, {}, {'id': 7}])
        self.assertEqual(items[0]['id'], 7)
        self.assertEqual(len({i['id'] for i in items}), 3)
    def test_templates(self):
        self.assertEqual(template_item('python')['script_type'], 'python')
        self.assertFalse(template_item('node')['auto_restart'])
    def test_scan(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td); (p/'a.py').touch(); (p/'node_modules').mkdir(); (p/'node_modules/x.js').touch()
            self.assertEqual(scan_scripts([p,p/'a.py']), [str(p/'a.py')])
    def test_codes(self):
        self.assertEqual(parse_codes('0, 2,-1'), [0,2,-1])
        with self.assertRaises(ValueError): parse_codes('bad')
    def test_links(self):
        with self.assertRaises(ValueError): validate_links([{'id':1,'on_success':[2]}, {'id':2,'on_failure':[1]}])
        with self.assertRaises(ValueError): validate_links([{'id':1,'on_success':[9]}])
    def test_quoted_arguments(self):
        self.assertEqual(split_arguments('--name "hello world" --empty ""'), ['--name','hello world','--empty',''])
    def test_merge_remaps_links(self):
        merged=merge_task_items([{'id':1,'path':'existing.py'}],[{'id':1,'path':'a.py','on_success':[2]},{'id':2,'path':'b.py'}])
        self.assertEqual(merged[1]['on_success'],[merged[2]['id']])
        self.assertEqual(len({i['id'] for i in merged}),3)
    def test_dependency_chain_cycle_rejected(self):
        with self.assertRaises(ValueError):
            validate_links([{'id':1,'depends_on':'2','on_success':[2]},{'id':2}])
    def test_chain_cannot_require_completed_predecessor_running(self):
        with self.assertRaises(ValueError):
            validate_links([{'id':1,'on_success':[2]},{'id':2,'depends_on':'1'}])
    def test_conda_discovery_and_environment(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'env';root.mkdir();(root/'python.exe').touch()
            with patch.dict(os.environ,{'CONDA_EXE':str(Path(td)/'conda.exe')}),patch('task_features.shutil.which',return_value=None),patch('task_features.subprocess.run',return_value=Mock(stdout=__import__('json').dumps({'envs':[str(root)]}))):
                found=discover_environments()
            self.assertTrue(any(e['kind']=='conda' and e['root']==str(root) for e in found))
            raw=environment_vars({'environment_kind':'conda','environment_root':str(root),'env_vars':'CUSTOM=1'})
            self.assertIn('CONDA_PREFIX='+str(root),raw);self.assertIn('CUSTOM=1',raw)
    def test_queue(self):
        q=TaskQueue(); self.assertTrue(q.submit(1)); self.assertFalse(q.submit(1)); q.submit(2)
        self.assertEqual(q.take(1, set()), [1]); self.assertEqual(q.take(1,{1}), [])
        self.assertEqual(q.take(1,set()), [2])
    def test_blocked_does_not_block_ready(self):
        q=TaskQueue(); q.submit(1); q.submit(2)
        self.assertEqual(q.take(1,set(),lambda i:i==2), [2]); q.cancel(1); self.assertFalse(q.pending)
