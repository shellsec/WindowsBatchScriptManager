import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import main as app


class RegressionTests(unittest.TestCase):
    def test_api_stop_matches_only_current_installation(self):
        with tempfile.TemporaryDirectory() as td:
            current = Path(td) / "current" / app.API_SERVER_SCRIPT_NAME
            other = Path(td) / "other" / app.API_SERVER_SCRIPT_NAME
            records = [
                {"pid": 101, "script_path": current.name, "cwd": str(current.parent)},
                {"pid": 202, "script_path": str(other)},
                {"pid": 303, "script_path": current.name},
            ]
            with patch.object(app, "HAS_PSUTIL", True), \
                    patch.object(app, "get_api_server_script_path", return_value=str(current)), \
                    patch.object(app, "scan_running_script_processes", return_value=records), \
                    patch.object(app, "get_process_tree_pids", side_effect=lambda pid: [pid]), \
                    patch.object(app, "kill_process", return_value=True) as stop:
                self.assertEqual(app.stop_api_server_processes(), 1)
                stop.assert_called_once_with(101)

    def test_api_status_caches_an_empty_scan(self):
        obj = app.BatchManagerApp.__new__(app.BatchManagerApp)
        obj._api_server_popen = None
        obj._api_status_last_scan = 0
        obj._api_status_cached_pids = []
        with patch.object(app.time, "monotonic", side_effect=[100, 101, 102, 111]), \
                patch.object(app, "find_api_server_pids", return_value=[]) as scan:
            self.assertEqual(obj._get_api_server_pids_for_ui(), [])
            self.assertEqual(obj._get_api_server_pids_for_ui(), [])
            scan.assert_called_once()
            obj._get_api_server_pids_for_ui(force_scan=True)
            obj._get_api_server_pids_for_ui()
            self.assertEqual(scan.call_count, 3)

    def test_ai_diagnostics_use_selected_run_and_mask_credentials(self):
        item = {"id": 1, "name": "Task", "args": "--password hidden"}
        with tempfile.TemporaryDirectory() as td, patch.object(app, "CONFIG_PATH", Path(td) / "tasks.json"):
            logs = Path(td) / "logs"
            logs.mkdir()
            (logs / "Task_1.log").write_text("old failure\nAuthorization: Bearer protected\n", encoding="utf-8")
            (logs / "Task_2.log").write_text("new success\n", encoding="utf-8")
            result = app.build_ai_diagnostic_context(item, {"exit_code": 7, "log_file": "Task_1.log"})
            self.assertEqual(result["run"]["exit_code"], 7)
            self.assertEqual(result["log_tail"], "old failure\n[redacted]")
            self.assertNotIn("hidden", app.json.dumps(result))
            self.assertNotIn("log_tail", app.build_ai_diagnostic_context(item, {"exit_code": 7}))
            self.assertNotIn("log_tail", app.build_ai_diagnostic_context(item, {"log_file": "../Task_1.log"}))

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
