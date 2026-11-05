import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import main
from task_features import template_item

class TkSmoke(unittest.TestCase):
    def test_task_refresh_keeps_scroll_position(self):
        root = tk.Tk()
        root.withdraw()
        try:
            from tkinter import ttk

            app = main.BatchManagerApp.__new__(main.BatchManagerApp)
            app.tree = ttk.Treeview(root, height=6)
            app.tree.pack()
            app.items = [{"id": index} for index in range(1, 81)]
            app._iter_visible_items = lambda: enumerate(app.items)
            app._compute_row_values = lambda item: ((item["id"],), "")
            app._search_keywords = lambda: []
            app._update_visit_button_state = Mock()
            app._update_search_match_label = Mock()
            app._update_group_filter_options = Mock()
            app._sync_tree()
            app.tree.yview_moveto(0.25)
            position = app.tree.yview()[0]

            app._sync_tree()
            self.assertEqual(app.tree.yview()[0], position)

            app.tree.selection_set(app.tree.get_children()[-1])
            app._sync_tree(force_rebuild=True)
            self.assertEqual(app.tree.yview()[0], position)
            self.assertEqual(app._item_id_from_tree_iid(app.tree.selection()[0]), 80)
        finally:
            root.update_idletasks()
            root.destroy()

    def test_editor_preserves_environment_and_completion_fields(self):
        root=tk.Tk();root.withdraw()
        try:
            with tempfile.TemporaryDirectory() as td:
                script=Path(td)/'worker.py';script.write_text('pass',encoding='utf-8')
                initial=template_item('python');initial.update(path=str(script),success_exit_codes=[0,2],on_success=[9],environment_kind='conda',environment_root=td)
                dialog=main.AddEditDialog(root,'add',initial);dialog.withdraw();dialog._do_on_ok()
                self.assertIsNotNone(dialog.result)
                self.assertEqual(dialog.result['success_exit_codes'],[0,2]);self.assertEqual(dialog.result['on_success'],[9])
                self.assertEqual(dialog.result['environment_kind'],'conda')
        finally:
            root.update_idletasks();root.destroy()
    def test_feature_menu_and_dialogs_construct(self):
        root=tk.Tk();root.withdraw()
        try:
            root.config(menu=tk.Menu(root))
            app=main.BatchManagerApp.__new__(main.BatchManagerApp);app.root=root
            app.items=[{'id':1,'name':'one','path':'one.py'},{'id':2,'name':'two','path':'two.py'}]
            app._get_selected_rows=lambda:[0];app._item_at=lambda row:app.items[row];app._app_config={}
            app._sync_tree=Mock();app._save_config=Mock()
            with patch.object(main,'load_app_config',return_value={}):app._init_task_features()
            app._edit_completion_rules();app._show_task_queue();app._choose_task_environment();app._preview_script_import([])
            root.update_idletasks()
            self.assertGreaterEqual(len(root.winfo_children()),5)
            app._task_closing=True
        finally:
            root.update_idletasks();root.destroy()

if __name__=='__main__':unittest.main()
