import contextlib
import io
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import main
from task_features import TaskQueue, migrate_items

class Runtime(unittest.TestCase):
    def make_app(self, items, limit=1):
        app=main.BatchManagerApp.__new__(main.BatchManagerApp)
        app.items=items;app._task_queue=TaskQueue();app._task_active={};app._task_requests={};app._task_limit=limit
        app._task_closing=False;app._execution_history={};app.status_var=Mock();app.root=Mock()
        app._sync_tree=Mock();app._save_config=Mock();app._save_history=Mock();app._check_and_notify=Mock()
        app._make_log_path=lambda item:None
        return app
    def tick_until_done(self,app):
        end=time.monotonic()+8
        while app._task_active or app._task_queue.pending:
            app._task_tick()
            if time.monotonic()>end:self.fail('Scheduler did not finish')
            time.sleep(.03)
    def test_real_process_chain_and_limit(self):
        items=[{'id':1,'name':'first','path':'ignored','success_exit_codes':[2],'on_success':[2]}, {'id':2,'name':'second','path':'ignored'}]
        app=self.make_app(items);launches=[]
        def launch(item,trigger='manual'):
            self.assertLess(len(app._task_active),1)
            code=2 if item['id']==1 else 0
            p=subprocess.Popen([sys.executable,'-c',f'import time;time.sleep(.12);raise SystemExit({code})'])
            item.update(pid=p.pid,_popen=p,pid_set_at=time.time());app._record_execution_start(item['id'],trigger);launches.append(item['id']);return p
        app._launch_single_script=launch
        app._start_single_script(items[0]);self.tick_until_done(app)
        self.assertEqual(launches,[1,2]);self.assertEqual(app._execution_history[1][0]['exit_code'],2)
        self.assertEqual(app._execution_history[1][0]['status'],'success')
    def test_cancelled_run_never_chains(self):
        items=[{'id':1,'path':'ignored','on_success':[2]}, {'id':2,'path':'ignored'}];app=self.make_app(items)
        process=Mock();process.pid=7654321;process.poll.return_value=0
        process.http_response = None
        items[0]['pid']=process.pid
        app._task_active[1]={'process':process,'item':items[0],'start':time.time(),'trail':set(),'cancelled':False}
        app._cancel_task(items[0]);app._task_tick()
        self.assertFalse(app._task_queue.pending);self.assertFalse(app._task_active)
    def test_cli_id_survives_delete(self):
        with tempfile.TemporaryDirectory() as td,patch.object(main,'CONFIG_PATH',Path(td)/'config.json'),patch.object(main,'scan_running_script_processes',return_value=[]),contextlib.redirect_stdout(io.StringIO()):
            main.save_config([{'id':2,'name':'keep','path':'keep.py'},{'id':8,'name':'remove','path':'remove.py'}])
            self.assertEqual(main.cli_main(['--delete','8']),0)
            self.assertEqual(main.load_config()[0]['id'],2)
    def test_failure_branch_and_queue_cancellation(self):
        items=[{'id':1,'name':'fail','path':'ignored','on_failure':[2]},{'id':2,'name':'recover','path':'ignored'},{'id':3,'name':'cancel','path':'ignored'}]
        app=self.make_app(items);launches=[]
        def launch(item,trigger='manual'):
            p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(.08);raise SystemExit(3)' if item['id']==1 else 'pass'])
            item.update(pid=p.pid,_popen=p,pid_set_at=time.time());app._record_execution_start(item['id'],trigger);launches.append(item['id']);return p
        app._launch_single_script=launch;app._start_single_script(items[0]);app._start_single_script(items[2]);app._cancel_task(items[2]);self.tick_until_done(app)
        self.assertEqual(launches,[1,2]);self.assertEqual(app._execution_history[1][0]['status'],'failed')

    def test_deleted_max_id_is_not_reused(self):
        with tempfile.TemporaryDirectory() as td,patch.object(main,'CONFIG_PATH',Path(td)/'config.json'):
            main.save_config([{'id':8,'path':'old.py'}]);main.save_config([])
            self.assertGreater(main.next_item_id([]),8)

    def test_adopted_pending_job_clears_queue_state(self):
        item={'id':1,'path':'ignored','pid':123,'_queued':True};app=self.make_app([item])
        app._task_queue.submit(1);app._task_requests[1]={'trigger':'manual','trail':set()}
        with patch.object(main,'is_process_running',return_value=True):app._drain_task_queue()
        self.assertFalse(item.get('_queued'));self.assertNotIn(1,app._task_requests)

    def test_real_script_arguments_environment_and_log(self):
        with tempfile.TemporaryDirectory() as td:
            script=Path(td)/'worker.py';log=Path(td)/'worker.log'
            script.write_text('import sys,os,json;print(json.dumps([sys.argv[1:],os.environ.get("FEATURE_TEST")]))',encoding='utf-8')
            process=main.start_script(str(script),td,'python',sys.executable,log_path=str(log),extra_args='--title "hello world"',env_vars='FEATURE_TEST=ok')
            self.assertIsNotNone(process);self.assertEqual(process.wait(timeout=8),0)
            self.assertEqual(main.json.loads(log.read_text(encoding='utf-8')),[['--title','hello world'],'ok'])


if __name__=='__main__':unittest.main()
