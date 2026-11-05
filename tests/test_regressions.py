import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import main as app


class RegressionTests(unittest.TestCase):
    def test_shared_dependency_is_not_cycle(self):
        items = [{'id': 1, 'depends_on': '2,3'}, {'id': 2, 'depends_on': '3'}, {'id': 3}]
        self.assertIsNone(app._check_circular_deps(items, 1))

    def test_real_cycle_is_reported(self):
        self.assertIsNotNone(app._check_circular_deps([{'id': 1, 'depends_on': '2'}, {'id': 2, 'depends_on': '1'}], 1))

    def test_kill_denied_is_failure(self):
        with patch.object(app, 'HAS_PSUTIL', True), patch.object(app.psutil, 'Process', side_effect=app.psutil.AccessDenied(123)):
            self.assertFalse(app.kill_process(123))

    def test_process_info_contains_executable_path(self):
        process = Mock()
        process.name.return_value = 'worker.exe'
        process.exe.return_value = r'C:\tools\worker.exe'
        process.cmdline.return_value = ['worker.exe']
        with patch.object(app, 'HAS_PSUTIL', True), patch.object(app.psutil, 'Process', return_value=process):
            self.assertEqual(app.get_process_info(123)['exe'], r'C:\tools\worker.exe')

    def test_cli_update_preserves_other_settings(self):
        original = {'name': 'old', 'path': 'job.py', 'schedule_enabled': True, 'schedule_time': '12:34', 'args': '--port 9000', 'env_vars': 'MODE=test', 'depends_on': '2', 'delay_after_dep': 9, 'launch_on_app_start': True}
        with tempfile.TemporaryDirectory() as td, patch.object(app, 'CONFIG_PATH', Path(td) / 'tasks.json'), patch.object(app, 'scan_running_script_processes', return_value=[]), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(app.save_config([original]))
            self.assertEqual(app.cli_main(['--update', '1', '--name', 'new']), 0)
            saved = app.load_config()[0]
            for key, value in original.items():
                if key != 'name':
                    self.assertEqual(saved.get(key), value, key)

    def test_cli_replace_import_preserves_schedule(self):
        original = {'name': 'job', 'path': 'job.py', 'schedule_enabled': True, 'schedule_time': '12:34', 'args': '--port 9000'}
        with tempfile.TemporaryDirectory() as td, patch.object(app, 'CONFIG_PATH', Path(td) / 'tasks.json'), patch.object(app, 'scan_running_script_processes', return_value=[]), contextlib.redirect_stdout(io.StringIO()):
            source = Path(td) / 'import.json'
            source.write_text(app.json.dumps([original]), encoding='utf-8')
            self.assertEqual(app.cli_main(['--import', str(source), '--replace']), 0)
            saved = app.load_config()[0]
            for key, value in original.items():
                self.assertEqual(saved.get(key), value, key)

    def test_cli_reports_failed_save(self):
        with patch.object(app, 'load_config', return_value=[{'name': 'job', 'path': 'job.py'}]), patch.object(app, 'save_config', return_value=False), patch.object(app, 'scan_running_script_processes', return_value=[]), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertNotEqual(app.cli_main(['--update', '1', '--name', 'changed']), 0)

    def test_string_false_does_not_enable_gui_schedule(self):
        instance = app.BatchManagerApp.__new__(app.BatchManagerApp)
        instance._sync_tree = Mock()
        with patch.object(app, 'load_config', return_value=[{'path': 'job.py', 'schedule_enabled': 'false', 'auto_restart': 'false', 'log_output': 'false'}]):
            instance._load_config()
        for key in ('schedule_enabled', 'auto_restart', 'log_output'):
            self.assertFalse(instance.items[0][key], key)


if __name__ == '__main__':
    unittest.main()
