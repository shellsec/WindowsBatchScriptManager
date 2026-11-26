import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import main
import WindowsBatchScriptManager_api_server as api

class MCPDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch = patch.object(main, 'CONFIG_PATH', self.root / 'tasks.json')
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.items = [{'id': 1, 'name': '中文任务'}]

    def test_unicode_tail_and_read_only(self):
        logs = self.root / 'logs'
        logs.mkdir()
        path = logs / '中文任务_123.log'
        content = '第一行\n第二行\n第三行\n'
        path.write_text(content, encoding='utf-8')
        result = main.read_task_diagnostics(self.items, '1', lines=2)
        self.assertEqual(result['text'], '第二行\n第三行')
        self.assertTrue(result['truncated'])
        self.assertEqual(path.read_text(encoding='utf-8'), content)

    def test_output_is_bounded(self):
        logs = self.root / 'logs'
        logs.mkdir()
        (logs / '中文任务_123.log').write_bytes(b'x' * 100000 + b'\nlast\n')
        result = main.read_task_diagnostics(self.items, '1')
        self.assertEqual(result['text'], 'last')
        self.assertTrue(result['truncated'])

    def test_absent_log_and_history_are_not_success(self):
        self.assertFalse(main.read_task_diagnostics(self.items, '1')['available'])
        with patch.object(main, 'load_history', return_value={}):
            self.assertIsNone(main.read_task_diagnostics(self.items, '1', result_only=True)['record'])
        self.assertFalse((self.root / 'logs').exists())

    def test_ambiguous_names_and_paths_rejected(self):
        with self.assertRaises(ValueError):
            main.read_task_diagnostics(self.items * 2, '中文任务')
        with self.assertRaises(ValueError):
            main.read_task_diagnostics(self.items, '../secret')
        with self.assertRaises(ValueError):
            main.read_task_diagnostics(self.items, '1', lines=True)

    def test_record_fields_are_filtered(self):
        record = {'exit_code': 2, 'status': 'failed', 'env_vars': 'SECRET'}
        with patch.object(main, 'load_history', return_value={1: [record]}):
            result = main.read_task_diagnostics(self.items, '1', result_only=True)
        self.assertEqual(result['record']['exit_code'], 2)
        self.assertNotIn('env_vars', result['record'])

    def test_mcp_validation_and_cli(self):
        for arguments in ({'id': '1', 'lines': 0}, {'id': '1', 'lines': True}, {'id': '1', 'path': 'secret'}, {'id': 1}):
            with patch.object(api, 'run_cli') as run:
                self.assertTrue(api.execute_mcp_tool('get_task_logs', arguments)[1])
                run.assert_not_called()
        with patch.object(api, 'run_cli', return_value=('{}', '', 0)) as run:
            self.assertFalse(api.execute_mcp_tool('get_task_logs', {'id': '1', 'lines': 20})[1])
            run.assert_called_once_with('--task-log', '1', '--log-lines', '20')

    def test_agpl_source_info_matches_version(self):
        text, error = api.execute_mcp_tool('get_source_info', {})
        self.assertFalse(error)
        info = json.loads(text)
        self.assertEqual(info['license'], 'AGPL-3.0-only')
        self.assertIn(api.__version__, info['source'])
        self.assertTrue(info['source'].endswith('-source.zip'))

    def test_public_docs_do_not_disclose_api_key(self):
        from types import SimpleNamespace
        request = SimpleNamespace(headers={'host': 'localhost:8765'}, url=SimpleNamespace(port=8765))
        with patch.object(api, 'API_KEY', 'private-test-secret'), patch.object(api, 'DISABLE_AUTH', False), patch.object(api, 'get_lan_ip', return_value='127.0.0.1'):
            html = api.build_docs_html(request)
        self.assertNotIn('private-test-secret', html)
        self.assertIn('YOUR_API_KEY', html)

    def test_name_collision_does_not_leak_another_log(self):
        items = [{'id': 1, 'name': 'same?'}, {'id': 2, 'name': 'same*'}]
        with self.assertRaises(ValueError):
            main.read_task_diagnostics(items, '1')

if __name__ == '__main__':
    unittest.main()
