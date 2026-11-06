from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from toolbox_shared import section_topology as model
from toolbox.topology_editor import TopologyEditor


class EditorUITests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        self.path_patch = patch.object(model, 'catalog_path', return_value=Path(self.temp.name)/'catalog.json')
        self.path_patch.start()
        self.error_patch = patch.object(TopologyEditor, 'error', side_effect=AssertionError)
        self.error_patch.start()
        self.applied = []
        self.editor = TopologyEditor(self.root, on_apply=self.applied.append)
        self.editor.withdraw()
        self.root.update()

    def tearDown(self):
        self.editor.dirty = False
        self.editor.close()
        self.root.destroy()
        self.path_patch.stop()
        self.error_patch.stop()
        self.temp.cleanup()

    def test_native_canvas_and_voltage_library_are_populated(self):
        editor = self.editor
        self.assertGreater(len(editor.items), 250)
        self.assertEqual(len(editor.tree.get_children('v500')), len(editor.data['nodes']))
        self.assertTrue(editor.tree.exists('v220'))
        editor.geometry('980x620')
        editor.deiconify()
        self.root.update()
        for panel in editor.property_tabs.winfo_children():
            editor.property_tabs.select(panel)
            self.root.update()
            self.assertLessEqual(panel.winfo_reqheight(), panel.winfo_height())
        editor.withdraw()

    def test_edit_undo_redo_save_and_applied_readback(self):
        editor = self.editor
        node = editor.data['nodes'][0]
        original = node['name']
        editor.selected = node['id']
        editor.inspect()
        editor.name.set('测试显示名')
        editor.update_node()
        self.assertEqual(editor.node()['name'], '测试显示名')
        editor.undo()
        self.assertEqual(editor.node()['name'], original)
        editor.redo()
        self.assertEqual(editor.node()['name'], '测试显示名')
        with patch('toolbox.topology_editor.messagebox.showinfo'):
            editor.apply()
        self.assertEqual(self.applied, [model.applied_path()])
        self.assertEqual(model.load_catalog()['nodes'][0]['name'], '测试显示名')
        self.assertEqual(model.load_catalog(model.applied_path())['nodes'][0]['name'], '测试显示名')

    def test_batch_place_and_connect_keep_unique_edges(self):
        editor = self.editor
        editor.data['edges'] = []
        for n in editor.data['nodes'][:2]:
            n['anchor'] = None
        editor.refresh()
        ids = [n['id'] for n in editor.data['nodes'][:2]]
        editor.tree.selection_set(ids)
        editor.place_selected()
        self.assertTrue(all(editor.node(i)['anchor'] for i in ids))
        editor.add_edge(*ids)
        self.assertEqual(len(editor.data['edges']), 1)
        with patch.object(editor, 'error') as error:
            editor.add_edge(*reversed(ids))
            error.assert_called_once()
        self.assertEqual(len(editor.data['edges']), 1)

    def test_connection_toolbar_enters_mode_and_canvas_clicks_create_edge(self):
        editor = self.editor
        editor.data['nodes'] = editor.data['nodes'][:2]
        editor.data['edges'] = []
        for n, x in zip(editor.data['nodes'], (1000, 4000)):
            n['anchor'] = {'x': x, 'y': 1000}
        editor.refresh()
        editor.fit()
        editor.mode_buttons['连接'].invoke()
        self.assertEqual(editor.mode.get(), '连接')
        for n in editor.data['nodes']:
            x, y = editor.xy(n['anchor'])
            editor.press(SimpleNamespace(x=x, y=y))
        self.assertEqual(len(editor.data['edges']), 1)
        self.assertEqual(editor.mode.get(), '连接')
        editor.cancel_connect()
        self.assertEqual(editor.mode.get(), '选择')

    def test_cancel_close_keeps_unsaved_editor_open(self):
        editor = self.editor
        editor.dirty = True
        with patch('toolbox.topology_editor.messagebox.askyesnocancel', return_value=None):
            self.assertFalse(editor.close())
        self.assertTrue(editor.winfo_exists())

    def test_stale_draft_does_not_change_applied_snapshot(self):
        editor = self.editor
        model.save_catalog(editor.data)
        with patch.object(editor, 'error') as error:
            editor.apply()
            error.assert_called_once()
        self.assertFalse(model.applied_path().exists())
        self.assertFalse(self.applied)


if __name__ == '__main__':
    unittest.main()
