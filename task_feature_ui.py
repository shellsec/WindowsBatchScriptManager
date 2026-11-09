"""Tk integration for task templates, environments, queues and completion rules."""
import json
import os
from pathlib import Path
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from ui_language import translate_inline
from task_features import (FEATURE_FIELDS, TaskQueue, template_item, scan_scripts,
                           discover_environments, environment_vars, migrate_items,
                           parse_codes, link_ids, validate_links)


def choose_environment(parent, script, work, variable, target, language="zh-CN"):
    def label(zh, en):
        return translate_inline(language, zh, en)

    win=tk.Toplevel(parent);win.title(label('运行环境', 'Environment'));win.geometry('780x360');win.transient(parent)
    choices=[];box=tk.Listbox(win,exportselection=False);box.pack(fill='both',expand=True,padx=10,pady=10)
    box.insert('end',label('正在发现环境…', 'Discovering environments…'))
    def apply():
        selection=box.curselection()
        if selection and choices:
            env=choices[selection[0]];variable.set(env['executable'])
            target.update(environment_kind=env['kind'],environment_root=env['root']);win.destroy()
    def browse():
        exe=filedialog.askopenfilename(parent=win,title=label('选择解释器', 'Select interpreter'))
        if exe:
            variable.set(exe);target.update(environment_kind='custom',environment_root='');win.destroy()
    ttk.Button(win,text=label('选择', 'Select'),command=apply).pack(side='left',padx=10,pady=10)
    ttk.Button(win,text=label('浏览文件', 'Browse'),command=browse).pack(side='left',padx=10)
    ttk.Button(win,text=label('使用系统默认', 'Default'),command=lambda:(variable.set(''),target.update(environment_kind='',environment_root=''),win.destroy())).pack(side='left')
    results=[]
    def worker():
        try: results.append(discover_environments(script,work))
        except Exception: results.append([])
    threading.Thread(target=worker,daemon=True).start()
    def poll():
        if not win.winfo_exists(): return
        if not results: win.after(100,poll);return
        choices.extend(results[0]);box.delete(0,'end')
        for env in choices:box.insert('end',env['label']+' — '+env['executable'])
        if not choices:box.insert('end',label('未发现环境，请浏览选择', 'Browse to an interpreter'))
    win.after(100,poll)


class TaskFeatureMixin:
    def _init_task_features(self):
        import main as core
        self._task_queue=TaskQueue();self._task_active={};self._task_requests={}
        try: self._task_limit=max(0,int(core.load_app_config().get('max_concurrent_tasks',0)))
        except (ValueError, TypeError): self._task_limit=0
        self._task_closing=False
        self._refresh_task_menu_language()
        self.root.after(250, self._task_tick)

    def _task_text(self, zh, en):
        return translate_inline(getattr(self, '_current_language', 'en-US'), zh, en)

    def _refresh_task_menu_language(self):
        menubar = self.root.nametowidget(self.root.cget('menu'))
        previous_menu = getattr(self, '_task_tools_menu', None)
        if previous_menu is not None:
            menubar.delete(self._task_tools_menu_index)
            previous_menu.destroy()
        menu=tk.Menu(self.root,tearoff=0)
        templates=tk.Menu(menu,tearoff=0)
        for kind,label in [('python','Python'),('node','Node.js'),('powershell','PowerShell'),('batch',self._task_text('批处理', 'Batch'))]:
            templates.add_command(label=label,command=lambda k=kind:self._add_template(k))
        menu.add_cascade(label=self._task_text('从模板创建', 'Templates'),menu=templates)
        menu.add_command(label=self._task_text('批量导入文件夹', 'Import folder'),command=self._import_folder)
        menu.add_command(label=self._task_text('运行规则与后续任务', 'Completion rules'),command=self._edit_completion_rules)
        menu.add_command(label=self._task_text('选择任务运行环境', 'Environment'),command=self._choose_task_environment)
        menu.add_separator()
        menu.add_command(label=self._task_text('并发上限', 'Concurrency'),command=self._set_task_limit)
        menu.add_command(label=self._task_text('查看/取消排队', 'Queue'),command=self._show_task_queue)
        menubar.add_cascade(label=self._task_text('任务增强', 'Task tools'), menu=menu)
        self._task_tools_menu = menu
        self._task_tools_menu_index = menubar.index('end')

    def _selected_feature_item(self):
        rows=self._get_selected_rows()
        if not rows:
            messagebox.showinfo(self._task_text('任务', 'Task'),self._task_text('请先选择一项任务', 'Select a task'),parent=self.root);return None
        return self._item_at(rows[0])

    def _add_template(self,kind):
        import main as core
        initial=template_item(kind)
        dialog=core.AddEditDialog(self.root,'add',initial,get_next_run=self._get_next_schedule_run)
        self.root.wait_window(dialog)
        if not dialog.result:return
        item=core.config_item_to_save(dialog.result);item.update(id=max(self._next_id,core.next_item_id(self.items)),pid=None)
        self.items.append(item);self._next_id=core.next_item_id(self.items);self._save_config();self._sync_tree()

    def _import_folder(self):
        folder=filedialog.askdirectory(parent=self.root,title=self._task_text('选择脚本文件夹', 'Select script folder'))
        if folder:self._preview_script_import([folder])

    def _preview_script_import(self,paths):
        import main as core
        win=tk.Toplevel(self.root);win.title(self._task_text('批量导入', 'Batch import'));win.geometry('850x520');win.transient(self.root)
        recursive=tk.BooleanVar(value=True);status=tk.StringVar(value=self._task_text('扫描中', 'Scanning…'))
        results=[];candidates=[];selected=set();generation=[0]
        tree=ttk.Treeview(win,columns=('selected','type','path','state'),show='headings',selectmode='extended')
        for key,label,width in [('selected',self._task_text('导入', 'Add'),65),('type',self._task_text('类型', 'Type'),85),('path',self._task_text('路径', 'Path'),520),('state',self._task_text('状态', 'State'),120)]:
            tree.heading(key,text=label);tree.column(key,width=width)
        tree.pack(fill='both',expand=True,padx=10,pady=10)
        bar=ttk.Frame(win);bar.pack(fill='x',padx=10,pady=8)
        def scan():
            generation[0]+=1;token=generation[0];recurse=recursive.get();status.set(self._task_text('扫描中', 'Scanning…'));candidates.clear();selected.clear();tree.delete(*tree.get_children())
            def work():
                try:results.append((token,scan_scripts(paths,recurse),None))
                except Exception as exc:results.append((token,[],str(exc)))
            threading.Thread(target=work,daemon=True).start()
        def poll():
            if not win.winfo_exists():return
            while results:
                token,files,error=results.pop(0)
                if token!=generation[0]:continue
                existing={os.path.normcase(core.resolve_script_file_path(i)) for i in self.items}
                for file in files:
                    duplicate=os.path.normcase(file) in existing;index=len(candidates);candidates.append((file,duplicate))
                    if not duplicate:selected.add(index)
                    tree.insert('', 'end',iid=str(index),values=('☑' if not duplicate else '',core.get_script_type(file),file,self._task_text('已存在', 'Exists') if duplicate else self._task_text('可导入', 'New')))
                status.set(error or self._task_text(f'{len(files)} 个脚本，勾选 {len(selected)} 项', f'{len(files)} scripts, {len(selected)} selected'))
            win.after(100,poll)
        def toggle(event=None):
            for iid in tree.selection():
                index=int(iid)
                if candidates[index][1]:continue
                if index in selected:selected.remove(index)
                else:selected.add(index)
                tree.set(iid,'selected','☑' if index in selected else '')
        tree.bind('<Double-1>',toggle);tree.bind('<space>',toggle)
        def select_all():
            for index,(_,duplicate) in enumerate(candidates):
                if not duplicate:selected.add(index);tree.set(str(index),'selected','☑')
        def commit():
            existing={os.path.normcase(core.resolve_script_file_path(i)) for i in self.items};added=[]
            for index in sorted(selected):
                file,_=candidates[index]
                if os.path.normcase(file) in existing:continue
                kind=core.get_script_type(file);item=core.config_item_to_save({'name':Path(file).stem,'path':file,'work_dir':str(Path(file).parent),'script_type':kind})
                item.update(id=max(self._next_id,core.next_item_id(self.items+added)),pid=None);added.append(item);existing.add(os.path.normcase(file))
            if not added:return
            if not core.save_config([core.config_item_to_save(i) for i in self.items+added]):
                messagebox.showerror(self._task_text('保存失败', 'Save failed'),self._task_text('配置未写入', 'Configuration was not saved'),parent=win);return
            self.items.extend(added);self._next_id=core.next_item_id(self.items);self._sync_tree();win.destroy()
        ttk.Checkbutton(bar,text=self._task_text('递归扫描', 'Recursive'),variable=recursive,command=scan).pack(side='left')
        ttk.Button(bar,text=self._task_text('全选', 'All'),command=select_all).pack(side='left',padx=5)
        ttk.Button(bar,text=self._task_text('切换勾选', 'Toggle'),command=toggle).pack(side='left')
        ttk.Button(bar,text=self._task_text('导入勾选项', 'Import'),command=commit).pack(side='right')
        ttk.Label(win,textvariable=status).pack(fill='x',padx=10,pady=5)
        scan();win.after(100,poll)

    def _choose_task_environment(self):
        item=self._selected_feature_item()
        if not item:return
        win=tk.Toplevel(self.root);win.title(self._task_text('运行环境', 'Environment'));win.transient(self.root)
        var=tk.StringVar(value=item.get('interpreter',''));pending=dict(item)
        ttk.Entry(win,textvariable=var,width=80).pack(padx=12,pady=12)
        ttk.Button(win,text=self._task_text('发现或浏览', 'Discover or browse'),command=lambda:choose_environment(win,item.get('path',''),item.get('work_dir',''),var,pending,self._current_language)).pack(pady=5)
        def save():
            item.update(interpreter=var.get().strip(),python_exe=var.get().strip() if item.get('script_type')=='python' else '',environment_kind=pending.get('environment_kind',''),environment_root=pending.get('environment_root',''))
            self._save_config();win.destroy()
        ttk.Button(win,text=self._task_text('保存', 'Save'),command=save).pack(pady=12)

    def _edit_completion_rules(self):
        item=self._selected_feature_item()
        if not item:return
        win=tk.Toplevel(self.root);win.title(self._task_text('运行规则', 'Completion rules'));win.geometry('650x520');win.transient(self.root)
        ttk.Label(win,text=self._task_text('成功退出码（逗号分隔）', 'Successful exit codes')).pack(anchor='w',padx=12,pady=(12,0))
        codes=tk.StringVar(value=','.join(map(str,parse_codes(item.get('success_exit_codes',[0])))))
        ttk.Entry(win,textvariable=codes).pack(fill='x',padx=12,pady=5)
        choices=[x for x in self.items if x['id']!=item['id']];boxes=[]
        for field,label in [('on_success',self._task_text('成功后执行', 'On success')),('on_failure',self._task_text('失败后执行', 'On failure'))]:
            ttk.Label(win,text=label).pack(anchor='w',padx=12)
            box=tk.Listbox(win,selectmode='multiple',exportselection=False,height=7);box.pack(fill='both',expand=True,padx=12,pady=5)
            for index,target in enumerate(choices):
                box.insert('end',f"#{target['id']}  {target.get('name','')}")
                if target['id'] in link_ids(item.get(field)):box.selection_set(index)
            boxes.append((field,box))
        def save():
            candidate=dict(item)
            try:
                candidate['success_exit_codes']=parse_codes(codes.get())
                for field,box in boxes:candidate[field]=[choices[index]['id'] for index in box.curselection()]
                validate_links([candidate if i['id']==item['id'] else i for i in self.items])
            except ValueError as exc:messagebox.showerror(self._task_text('规则无效', 'Invalid rules'),str(exc),parent=win);return
            item.update({key:candidate[key] for key in ('success_exit_codes','on_success','on_failure')});self._save_config();win.destroy()
        ttk.Label(win,text=self._task_text('手动停止不触发后续任务，自动重启优先于失败分支。', 'Manual stops skip follow-up tasks. Retries run before failure tasks.')).pack(padx=12,pady=5)
        ttk.Button(win,text=self._task_text('保存', 'Save'),command=save).pack(pady=10)

    def _set_task_limit(self):
        import main as core
        value=simpledialog.askinteger(self._task_text('并发上限', 'Concurrency'),self._task_text('同时运行的任务数（0 表示不限）', 'Maximum concurrent tasks (0 means unlimited)'),initialvalue=self._task_limit,minvalue=0,parent=self.root)
        if value is None:return
        saved=core.load_app_config();saved['max_concurrent_tasks']=value
        if core.save_app_config(saved):self._task_limit=value;self._app_config['max_concurrent_tasks']=value;self._drain_task_queue()
        else:messagebox.showerror(self._task_text('保存失败', 'Save failed'),'无法保存并发设置',parent=self.root)

    def _show_task_queue(self):
        win=tk.Toplevel(self.root);win.title(self._task_text('执行队列', 'Queue'));win.geometry('600x380');win.transient(self.root)
        box=tk.Listbox(win,selectmode='extended');box.pack(fill='both',expand=True,padx=10,pady=10);ids=[]
        def refresh():
            if not win.winfo_exists():return
            selected={ids[i] for i in box.curselection() if i<len(ids)};ids[:]=self._task_queue.pending;box.delete(0,'end')
            by_id={i['id']:i for i in self.items}
            for n,ident in enumerate(ids):
                box.insert('end',f"#{ident}  {by_id.get(ident,{}).get('name','')} — {self._task_text('排队', 'Queued')}")
                if ident in selected:box.selection_set(n)
            win.after(500,refresh)
        def cancel():
            targets=[ids[i] for i in box.curselection()]
            for item in self.items:
                if item['id'] in targets:self._cancel_task(item)
            self._sync_tree()
        ttk.Button(win,text=self._task_text('取消选中', 'Cancel selected'),command=cancel).pack(pady=10);refresh()

    def _clean_task_links(self):
        valid = {item['id'] for item in self.items}
        for item in self.items:
            for field in ('on_success', 'on_failure'):
                item[field] = [ident for ident in link_ids(item.get(field)) if ident in valid]
            item['depends_on'] = ','.join(str(ident) for ident in link_ids(item.get('depends_on')) if ident in valid)

    def _cancel_task(self,item):
        if not hasattr(self,'_task_queue'):return
        ident=item['id'];self._task_queue.cancel(ident);self._task_requests.pop(ident,None);item.pop('_queued',None);item.pop('_deps_ready_at',None)
        if ident in self._task_active:self._task_active[ident]['cancelled']=True

    def _start_single_script(self,item,trigger='manual'):
        import main as core
        if core.is_system_power_task(item):return self._launch_single_script(item,trigger)
        if not hasattr(self,'_task_queue'):
            self._task_queue=TaskQueue();self._task_active={};self._task_requests={};self._task_limit=0
        ident=item['id']
        if ident in self._task_active and self._task_active[ident].get('path', self._task_active[ident]['item'].get('path')) != item.get('path'):
            self._detached_processes = getattr(self, '_detached_processes', []) + [self._task_active.pop(ident)['process']]
        if ident in self._task_active:
            run=self._task_active[ident]
            if run['process'].poll() is None:return run['process']
            # Completion is processed on the next tick; retain a deferred restart request.
            if run.get('cancelled'):
                self._task_requests[ident]={'trigger':trigger,'trail':set()}
                self._task_queue.submit(ident);item['_queued']=True
            return None
        if item.get('pid') and core.is_process_running(item['pid']):return None
        try:
            validate_links(self.items, start_id=ident)
            if core._check_circular_deps(self.items,ident):raise ValueError(self._task_text('循环依赖', 'Circular dependencies'))
        except (ValueError, RecursionError) as exc:
            self.status_var.set(str(exc))
            self._show_http_response(item, str(exc))
            return None
        if not self._enqueue_task(item,trigger,set()):
            self._show_http_response(item, self.status_var.get())
            return None
        self._drain_task_queue()
        return self._task_active.get(ident,{}).get('process')

    def _show_http_response(self, item, response):
        import main as core
        if not core._is_http_task_item(item):
            return
        item['_http_response'] = response
        if hasattr(self, 'detail_text'):
            selected = self._get_selected_rows()
            if selected and self._item_at(selected[0]) is item:
                self._on_select(None)

    def _enqueue_task(self,item,trigger,trail):
        import main as core
        ident=item['id']
        if ident in self._task_active or ident in self._task_queue.pending:return True
        if item.get('pid') and core.is_process_running(item['pid']):return True
        by_id={i['id']:i for i in self.items}
        for dependency in core._parse_dep_ids(item.get('depends_on')):
            if dependency not in by_id:
                self.status_var.set(self._task_text(f'依赖任务不存在：{dependency}', f'Missing dependency: {dependency}'))
                return False
            if not self._enqueue_task(by_id[dependency],'dependency',trail):
                return False
        if self._task_queue.submit(ident):
            self._task_requests[ident]={'trigger':trigger,'trail':trail};item['_queued']=True
            self._show_http_response(item, self._task_text('已加入队列，等待执行', 'Queued, waiting to run'))
        return True

    def _drain_task_queue(self):
        import main as core
        by_id={i['id']:i for i in self.items}
        active={i['id'] for i in self.items if i.get('pid') and core.is_process_running(i['pid'])}|set(self._task_active)
        def ready(ident):
            item=by_id.get(ident)
            if not item:return False
            if not core._are_deps_running(item,self.items):return False
            delay=max(0,float(item.get('delay_after_dep') or 0))
            if delay:
                now=time.time();item.setdefault('_deps_ready_at',now)
                return now-item['_deps_ready_at']>=delay
            return True
        for ident in list(self._task_queue.pending):
            if ident not in by_id:
                self._task_queue.cancel(ident);self._task_requests.pop(ident,None)
            elif ident in active and ident not in self._task_active:
                self._task_queue.cancel(ident);self._task_requests.pop(ident,None);by_id[ident].pop('_queued',None)
        for ident in self._task_queue.take(self._task_limit,active,ready):
            item=by_id[ident];request=self._task_requests.pop(ident);item.pop('_queued',None);item.pop('_deps_ready_at',None)
            process=self._launch_single_script(item,request['trigger'])
            if process:
                self._task_active[ident]=dict(process=process,item=item,start=time.time(),trail=request['trail'],trigger=request['trigger'],cancelled=False,path=item.get('path'))
                self._show_http_response(item, self._task_text('请求中，等待远端返回', 'Request in progress'))
            else:
                error = core.get_last_start_error()
                self.status_var.set(f"{item.get('name','')}: {error}")
                self._show_http_response(item, self._task_text('启动失败', 'Start failed') + f': {error}')

    def _task_tick(self):
        import main as core
        if getattr(self,'_task_closing',False):return
        try:
            by_id={i['id']:i for i in self.items}
            for ident,run in list(self._task_active.items()):
                process=run['process'];item=by_id.get(ident)
                if item is not None and run.get('path',run['item'].get('path')) != item.get('path'):
                    self._detached_processes=getattr(self,'_detached_processes',[])+[process]
                    del self._task_active[ident]
                    continue
                rc=process.poll()
                if rc is None:continue
                del self._task_active[ident]
                response = getattr(process, 'http_response', None)
                response_text = None
                if response is not None:
                    with response:
                        response.seek(0)
                        output = response.read(65537)
                        response.seek(0, 2)
                        size = response.tell()
                        response.seek(max(0, size - 12))
                        trailer = response.read()
                    body_tail, separator, status = trailer.rpartition(b'\nHTTP ')
                    status = status.rstrip(b'\r\n')
                    has_status = separator and len(status) == 3 and status.isdigit()
                    body_size = size - len(trailer) + len(body_tail) if has_status else size
                    response_text = output[:min(body_size, 65536)].decode('utf-8', errors='replace').strip()
                    if body_size > 65536:
                        response_text += self._task_text('\n… [预览已截断]', '\n… [Preview truncated]')
                    if has_status:
                        if rc == 0 and 400 <= int(status) < 600:
                            # Older curl versions lack --fail-with-body.
                            rc = core.HTTP_ERROR_EXIT_CODE
                        if body_size <= 65536:
                            try:
                                response_text = json.dumps(json.loads(response_text), ensure_ascii=False, indent=2)
                            except ValueError:
                                pass
                        response_text = f'HTTP {status.decode("ascii")}\n{response_text}'.rstrip()
                    if not response_text:
                        response_text = self._task_text('请求进程退出，未收到返回内容', 'Request process exited without a response') + f' ({rc})'
                if item is None:continue
                cancelled=run['cancelled'] or item.get('pid')!=process.pid
                item['pid']=None;item.pop('_popen',None);item.pop('pid_set_at',None)
                if cancelled:
                    self._show_http_response(item, self._task_text('已停止', 'Stopped'))
                    item['_last_result']=self._task_text('已停止', 'Stopped')
                    for record in reversed(self._execution_history.get(ident,[])):
                        if record.get('status')=='running':
                            record.update(status='stopped',stop_time=time.time(),duration=time.time()-run['start'],exit_code=rc);break
                    self._save_history();continue
                if response_text is not None:
                    self._show_http_response(item, response_text)
                self._record_execution_stop(ident,rc,time.time()-run['start'])
                success=rc in parse_codes(item.get('success_exit_codes',[0]))
                item['_last_result']=self._task_text('成功', 'Success') if success else self._task_text('失败', 'Failed')
                is_http=item.get('script_type')=='http_get' or item.get('path','').startswith(('http://','https://'))
                retry=bool(item.get('auto_restart')) and not is_http
                if retry:
                    now=time.time();history=[t for t in item.get('_restart_history',[]) if now-t<60]+[now];item['_restart_history']=history
                    if len(history)>5:
                        item['auto_restart']=False;item['_crash_loop']=True;retry=False;self._save_config()
                if success:self._dispatch_task_chain(item,'on_success',run['trail'])
                if retry:self._enqueue_task(item,'restart',run['trail'])
                elif not success:self._dispatch_task_chain(item,'on_failure',run['trail'])
            self._detached_processes=[p for p in getattr(self,'_detached_processes',[]) if p.poll() is None]
            self._drain_task_queue()
            self._sync_tree()
        except Exception as exc:
            import logging
            logging.exception('Task scheduler failed')
            self.status_var.set(self._task_text(f'调度错误：{exc}', f'Scheduler error: {exc}'))
        self.root.after(250,self._task_tick)

    def _dispatch_task_chain(self,item,field,trail):
        try:
            validate_links(self.items, start_id=item['id'])
        except (ValueError, RecursionError) as exc:
            self.status_var.set(str(exc))
            return
        visited=trail;visited.add(item['id']);by_id={i['id']:i for i in self.items}
        for ident in link_ids(item.get(field)):
            if ident not in visited and ident in by_id:
                visited.add(ident)
                self._enqueue_task(by_id[ident],'chain',visited)
