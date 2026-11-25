import hashlib
import io
import unittest
from unittest.mock import Mock, patch
import main
import program_update

class UpdateProgressTests(unittest.TestCase):
    def test_download_reports_bytes_and_verification(self):
        data = b'update package'
        response = io.BytesIO(data)
        response.headers = {'Content-Length': str(len(data))}
        events = []
        with patch.object(program_update, '_open', return_value=response):
            output = io.BytesIO()
            program_update._download_package('https://github.com/test', output, hashlib.sha256(data).hexdigest(), progress=lambda stage, details: events.append((stage, details)))
        self.assertEqual(output.getvalue(), data)
        self.assertEqual(events[0][0], 'download')
        self.assertEqual(events[0][1]['bytes'], len(data))
        self.assertEqual(events[0][1]['total'], len(data))
        self.assertEqual(events[-1][0], 'verifying')

    def test_failure_is_visible_in_status_and_log(self):
        app = main.BatchManagerApp.__new__(main.BatchManagerApp)
        app.status_var = Mock()
        app._append_disk_cleanup_log = Mock()
        app._show_update_progress('failed', {'error': 'timed out'})
        message = app.status_var.set.call_args.args[0]
        self.assertIn('timed out', message)
        app._append_disk_cleanup_log.assert_called_once_with(message)

if __name__ == '__main__':
    unittest.main()
