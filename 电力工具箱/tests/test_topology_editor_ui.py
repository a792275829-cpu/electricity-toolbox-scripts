from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import tkinter as tk
import time
import json
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
        self.callback_errors = []
        self.root.report_callback_exception = lambda kind, value, _trace: self.callback_errors.append(f'{kind.__name__}: {value}')
        self.path_patch = patch.object(model, 'catalog_path', return_value=Path(self.temp.name)/'catalog.json')
        self.path_patch.start()
        self.error_patch = patch.object(TopologyEditor, 'error', side_effect=AssertionError)
        self.error_patch.start()
        self.applied = []
        self.editor = TopologyEditor(self.root, on_apply=self.applied.append)
        self.root.update()

    def tearDown(self):
        self.editor.dirty = False
        self.editor.selected = None
        self.editor.close()
        self.root.destroy()
        self.path_patch.stop()
        self.error_patch.stop()
        self.temp.cleanup()
        self.assertEqual(self.callback_errors, [], 'Tk回调异常不能被作为测试通过忽略')

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
        self.assertEqual(len(editor.data['edges']), 0)
        editor.confirm_connection_button.invoke()
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
        self.assertEqual(len(editor.data['edges']), 0)
        self.assertEqual(editor.mode.get(), '连接')
        self.assertIsNotNone(editor._connection_dialog)
        editor.confirm_connection_button.invoke()
        self.assertEqual(len(editor.data['edges']), 1)
        editor.cancel_connect()
        self.assertEqual(editor.mode.get(), '选择')

    def assert_fitted(self):
        editor = self.editor
        points = [editor.xy(n['anchor']) for n in editor.data['nodes'] if editor.visible(n)]
        width, height = editor.canvas.winfo_width(), editor.canvas.winfo_height()
        self.assertGreater(width, 120)
        self.assertGreater(height, 100)
        xs, ys = zip(*points)
        self.assertGreater(max((max(xs)-min(xs))/(width-90), (max(ys)-min(ys))/(height-70)), .95)
        self.assertGreaterEqual(min(xs), 30)
        self.assertLessEqual(max(xs), width-30)
        self.assertGreaterEqual(min(ys), 20)
        self.assertLessEqual(max(ys), height-20)

    def test_opening_and_resizing_fit_real_canvas_without_changing_coordinates(self):
        editor = self.editor
        original = deepcopy(editor.data)
        self.assert_fitted()
        for geometry in ('980x620', '1440x900', '1100x720'):
            editor.geometry(geometry)
            self.root.update()
            self.assert_fitted()
        self.assertEqual(editor.data, original)
        boxes = [editor.canvas.bbox(item) for item in editor.node_labels.values()]
        self.assertGreater(len(boxes), 10)
        for i, a in enumerate(boxes):
            for b in boxes[i+1:]:
                self.assertFalse(a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])

    def test_first_fit_waits_for_canvas_mapping(self):
        editor = self.editor
        editor.withdraw()
        self.root.update()
        editor.scale = .12
        editor.fit()
        self.assertEqual(editor.scale, .12)
        editor.deiconify()
        self.root.update()
        self.assert_fitted()

    def test_tree_selection_switches_tab_and_queued_events_preserve_typed_name(self):
        editor = self.editor
        editor.property_tabs.select(editor.line_panel)
        node_id = editor.data['nodes'][0]['id']
        editor.tree.selection_set(node_id)
        self.root.update()
        self.assertEqual(editor.property_tabs.select(), str(editor.node_panel))
        self.assertFalse(editor.name_entry.instate(['disabled']))
        editor.name_entry.delete(0, 'end')
        editor.name_entry.insert(0, '窗口真实输入')
        editor.tree.selection_set(node_id)
        self.root.update()
        self.assertEqual(editor.name.get(), '窗口真实输入')
        editor.buttons['保存草稿'].invoke()
        self.assertEqual(model.load_catalog()['nodes'][0]['name'], '窗口真实输入')
        self.assertIn('已保存', editor.notice.get())

    def test_canvas_events_select_drag_pan_and_zoom(self):
        editor = self.editor
        node = editor.data['nodes'][0]
        original = deepcopy(node['anchor'])
        x, y = map(round, editor.xy(node['anchor']))
        editor.canvas.event_generate('<ButtonPress-1>', x=x, y=y)
        self.root.update()
        self.assertEqual(editor.selected, node['id'])
        editor.canvas.event_generate('<Motion>', x=x+25, y=y+20, state=0x100)
        editor.canvas.event_generate('<ButtonRelease-1>', x=x+25, y=y+20)
        self.root.update()
        moved = deepcopy(editor.node()['anchor'])
        self.assertNotEqual(moved, original)
        editor.buttons['撤销'].invoke()
        self.root.update()
        self.assertEqual(editor.node()['anchor'], original)
        editor.buttons['重做'].invoke()
        self.root.update()
        self.assertEqual(editor.node()['anchor'], moved)
        before = editor.offset[:]
        editor.canvas.event_generate('<ButtonPress-1>', x=2, y=2)
        editor.canvas.event_generate('<Motion>', x=22, y=17, state=0x100)
        editor.canvas.event_generate('<ButtonRelease-1>', x=22, y=17)
        self.root.update()
        self.assertEqual(editor.offset, [before[0]+20, before[1]+15])
        point = ((80-editor.offset[0])/editor.scale, (80-editor.offset[1])/editor.scale)
        editor.canvas.event_generate('<MouseWheel>', delta=120, x=80, y=80)
        self.root.update()
        self.assertAlmostEqual((80-editor.offset[0])/editor.scale, point[0])
        self.assertAlmostEqual((80-editor.offset[1])/editor.scale, point[1])
        editor.buttons['适应画布'].invoke()
        self.root.update()
        self.assert_fitted()

    def test_line_click_survives_queued_tree_events_and_disables_node_edits(self):
        editor = self.editor
        editor.data['nodes'] = editor.data['nodes'][:2]
        editor.data['edges'] = []
        for n, x in zip(editor.data['nodes'], (1000, 4000)):
            n['anchor'] = {'x': x, 'y': 1000}
        a, b = editor.data['nodes']
        editor.data['edges'] = [dict(a=a['id'], b=b['id'], kind='line')]
        editor.refresh()
        self.root.update()
        editor.tree.selection_set(a['id'])
        self.root.update()
        x1, y1 = editor.xy(a['anchor'])
        x2, y2 = editor.xy(b['anchor'])
        editor.canvas.event_generate('<ButtonPress-1>', x=round((x1+x2)/2), y=round((y1+y2)/2))
        editor.canvas.event_generate('<ButtonRelease-1>', x=round((x1+x2)/2), y=round((y1+y2)/2))
        self.root.update()
        self.assertIsNone(editor.selected)
        self.assertEqual(editor.edge, (a['id'], b['id']))
        self.assertEqual(editor.property_tabs.select(), str(editor.line_panel))
        self.assertTrue(editor.buttons['更新属性'].instate(['disabled']))
        self.assertFalse(editor.buttons['更新所选线路类型'].instate(['disabled']))
        editor.edge_kind.set('站内变压器')
        editor.buttons['更新所选线路类型'].invoke()
        self.assertEqual(editor.current_edge()['kind'], 'transformer')

    def test_unavailable_actions_are_disabled_until_a_node_is_selected(self):
        editor = self.editor
        for label in ('更新属性', '从此节点连线', '删除所选节点 / 线路…', '撤销',
                      '确认为独立新增节点', '对比同站价格曲线'):
            self.assertTrue(editor.buttons[label].instate(['disabled']), label)
        editor.tree.selection_set(editor.data['nodes'][0]['id'])
        self.root.update()
        self.assertFalse(editor.buttons['更新属性'].instate(['disabled']))
        self.assertFalse(editor.buttons['从此节点连线'].instate(['disabled']))

    def test_invalid_voltage_edit_preserves_node_and_incident_lines(self):
        editor = self.editor
        node = editor.data['nodes'][0]
        editor.tree.selection_set(node['id'])
        self.root.update()
        before = deepcopy(editor.data)
        editor.voltage.set('220kV')
        with patch.object(editor, 'error') as error:
            editor.buttons['更新属性'].invoke()
            error.assert_called_once()
        self.assertEqual(editor.data, before)

    def connection_nodes(self):
        editor = self.editor
        editor.data['nodes'] = editor.data['nodes'][:2]
        editor.data['edges'] = []
        editor.refresh()
        self.root.update()
        return editor.data['nodes']

    def test_library_search_and_connection_require_explicit_confirmation(self):
        editor = self.editor
        a, b = self.connection_nodes()
        editor.tree.selection_set(a['id'])
        self.root.update()
        editor.buttons['从此节点连线'].invoke()
        self.assertEqual(editor.connect_from, a['id'])
        editor.search.set(b['station'])
        self.root.update()
        editor.tree.selection_set(b['id'])
        self.root.update()
        self.assertEqual(editor.data['edges'], [])
        self.assertEqual(editor.undo_stack, [])
        self.assertEqual(editor._connection_dialog.title(), '确认新增连接')
        self.assertEqual(editor._connection_target, b['id'])
        self.assertTrue(editor.canvas.find_withtag('connection-preview'))
        editor.canvas.event_generate('<Leave>')
        self.root.update()
        self.assertTrue(editor.canvas.find_withtag('connection-preview'))
        editor.confirm_connection_button.invoke()
        self.root.update()
        self.assertEqual(len(editor.data['edges']), 1)
        self.assertEqual({editor.data['edges'][0]['a'], editor.data['edges'][0]['b']}, {a['id'], b['id']})
        self.assertEqual(editor.mode.get(), '选择')
        self.assertIsNone(editor._connection_dialog)
        self.assertIn('已确认连接', editor.notice.get())

    def test_cancel_confirmation_does_not_change_graph_or_history(self):
        editor = self.editor
        a, b = self.connection_nodes()
        original = deepcopy(editor.data)
        editor.begin_connection(a['id'])
        editor.add_edge(a['id'], b['id'])
        editor.cancel_connection_button.invoke()
        self.root.update()
        self.assertEqual(editor.data, original)
        self.assertEqual(editor.undo_stack, [])
        self.assertIsNone(editor._connection_dialog)
        self.assertFalse(editor.canvas.find_withtag('connection-preview'))
        self.assertEqual(editor.mode.get(), '选择')

    def test_escape_dismisses_preview_without_adding_edge(self):
        editor = self.editor
        a, b = self.connection_nodes()
        editor.begin_connection(a['id'])
        editor.add_edge(a['id'], b['id'])
        self.root.update()
        self.assertIn('<Key-Escape>', editor.event_info(editor.cancel_event))
        editor._connection_dialog.event_generate(editor.cancel_event)
        self.root.update()
        self.assertEqual(editor.data['edges'], [])
        self.assertIsNone(editor._connection_dialog)
        self.assertEqual(editor.mode.get(), '选择')

    def test_cross_voltage_requires_confirming_transformer_type(self):
        editor = self.editor
        a, b = self.connection_nodes()
        b['voltage'] = 220
        editor.refresh()
        editor.begin_connection(a['id'])
        editor.add_edge(a['id'], b['id'])
        self.assertEqual(editor.data['edges'], [])
        self.assertTrue(editor.confirm_connection_button.instate(['disabled']))
        editor.confirm_connection_button.invoke()
        self.assertEqual(editor.data['edges'], [])
        editor.confirm_kind.set('站内变压器')
        self.assertFalse(editor.confirm_connection_button.instate(['disabled']))
        editor.confirm_connection_button.invoke()
        self.assertEqual(editor.data['edges'][0]['kind'], 'transformer')

    def test_confirmed_connection_can_be_undone_and_redone_once(self):
        editor = self.editor
        a, b = self.connection_nodes()
        editor.begin_connection(a['id'])
        editor.add_edge(a['id'], b['id'])
        editor.confirm_connection_button.invoke()
        self.assertEqual(len(editor.undo_stack), 1)
        editor.buttons['撤销'].invoke()
        self.assertEqual(editor.data['edges'], [])
        editor.buttons['重做'].invoke()
        self.assertEqual(len(editor.data['edges']), 1)

    def test_excel_import_export_and_reopen_retain_positions_and_connections(self):
        from openpyxl import Workbook
        editor = self.editor
        original = deepcopy(editor.data)
        book = Workbook()
        sheet = book.active
        sheet.append(['节点名称', '00:00', '00:15'])
        for n in original['nodes'][:2]:
            sheet.append([n['station']+'站500kV', 10, 20])
        sheet.append(['新节点站220kV', 30, 40])
        source = Path(self.temp.name)/'prices.xlsx'
        book.save(source)
        book.close()
        with patch('toolbox.topology_editor.filedialog.askopenfilename', return_value=str(source)), \
             patch('toolbox.topology_editor.messagebox.showinfo'):
            editor.buttons['导入新电价表'].invoke()
            self.assertTrue(editor.busy)
            self.assertTrue(editor.buttons['保存草稿'].instate(['disabled']))
            deadline = time.monotonic()+5
            while editor.busy and time.monotonic() < deadline:
                self.root.update()
                time.sleep(.01)
        self.assertFalse(editor.busy)
        self.assertEqual(editor.data['edges'], original['edges'])
        for n in original['nodes']:
            self.assertEqual(editor.node(n['id'])['anchor'], n['anchor'])
        added = next(n for n in editor.data['nodes'] if n['station'] == '新节点')
        self.assertEqual(added['voltage'], 220)
        editor.tree.selection_set(added['id'])
        self.root.update()
        editor.buttons['将选中节点批量放入图中'].invoke()
        self.assertIsNotNone(editor.node(added['id'])['anchor'])
        editor.buttons['保存草稿'].invoke()
        backup = Path(self.temp.name)/'backup.json'
        with patch('toolbox.topology_editor.filedialog.asksaveasfilename', return_value=str(backup)):
            editor.buttons['导出拓扑'].invoke()
        self.assertEqual(json.loads(backup.read_text())['nodes'], editor.data['nodes'])
        saved = deepcopy(editor.data)
        editor.dirty = False
        editor.close()
        self.editor = TopologyEditor(self.root, on_apply=self.applied.append)
        self.root.update()
        self.assertEqual(self.editor.data, saved)
        self.assert_fitted()

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
