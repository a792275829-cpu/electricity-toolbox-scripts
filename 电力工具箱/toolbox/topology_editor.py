"""Native editor opened from the section analysis module."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import json
import math
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from toolbox_shared import section_topology as model


class TopologyEditor(tk.Toplevel):
    cancel_event = '<<CancelTopologyConnection>>'

    def __init__(self, parent, *, input_path=None, on_apply=None):
        super().__init__(parent)
        self.title('500 / 220kV 拓扑编辑')
        self.geometry('1280x820')
        self.minsize(980, 620)
        self.data = model.load_catalog()
        self.on_apply = on_apply
        self.input_path = input_path
        self.selected = None
        self.edge = None
        self.connect_from = None
        self.mode = tk.StringVar(self, '选择')
        self.search = tk.StringVar(self)
        self.filter = tk.StringVar(self, '全部')
        self.layer500 = tk.BooleanVar(self, True)
        self.layer220 = tk.BooleanVar(self, True)
        self.layerother = tk.BooleanVar(self, True)
        self.status = tk.StringVar(self)
        self.notice = tk.StringVar(self, '选择节点编辑属性；连线时选起点、终点，核对后再确认。')
        self.name = tk.StringVar(self)
        self.city = tk.StringVar(self)
        self.voltage = tk.StringVar(self, '待确认')
        self.edge_kind = tk.StringVar(self, '站间线路')
        self.old_variant = tk.StringVar(self)
        self.undo_stack = []
        self.redo_stack = []
        self.dirty = False
        self.busy = False
        self.scale = .12
        self.offset = [20., 20.]
        self.drag = None
        self.items = {}
        self.events = queue.Queue()
        self.buttons = {}
        self._auto_fit = True
        self._fit_after_id = None
        self._viewport_size = None
        self._inspected = None
        self._hovered = None
        self._import_after_id = None
        self._connection_dialog = None
        self._connection_target = None
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.event_add(self.cancel_event, '<Escape>')
        self.bind(self.cancel_event, lambda _: self.cancel_connect())
        self.bind('<Command-z>', lambda _: self.undo())
        self.bind('<Command-Shift-Z>', lambda _: self.redo())
        self.bind('<Command-s>', lambda _: self.save())
        self._build()
        self.refresh()
        self.queue_fit()
        self.poll_id = self.after(100, self.poll)
        if input_path:
            self._import_after_id = self.after(200, lambda: self.import_excel(input_path))

    def _build(self):
        bar = ttk.Frame(self, padding=10)
        bar.pack(fill='x')
        for title, command in [('导入新电价表', self.import_excel), ('撤销', self.undo), ('重做', self.redo),
                               ('保存草稿', self.save), ('应用到分析', self.apply),
                               ('导出拓扑', self.export), ('导入拓扑', self.import_json)]:
            self.button(bar, title, command).pack(side='left', padx=3)
        ttk.Label(self, textvariable=self.notice, padding=(14, 3)).pack(fill='x')
        # Reserve the footer before giving the expanding panes their space.
        self.status_label = ttk.Label(self, textvariable=self.status, padding=10)
        self.status_label.pack(side='bottom', fill='x')
        panes = ttk.Panedwindow(self, orient='horizontal')
        panes.pack(fill='both', expand=True, padx=10)
        left = ttk.Frame(panes, padding=6)
        center = ttk.Frame(panes)
        right = ttk.Frame(panes, padding=8)
        panes.add(left, weight=0)
        panes.add(center, weight=1)
        panes.add(right, weight=0)
        ttk.Label(left, text='节点库', font=('', 14, 'bold')).pack(anchor='w')
        ttk.Entry(left, textvariable=self.search).pack(fill='x', pady=7)
        self.search.trace_add('write', lambda *_: self.refresh_tree())
        selector = ttk.Combobox(left, textvariable=self.filter, state='readonly', width=24,
                               values=['全部', '未入图', '本次缺失', '待确认', '同站多价格'])
        selector.pack(fill='x')
        selector.bind('<<ComboboxSelected>>', lambda _: self.refresh_tree())
        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill='both', expand=True, pady=8)
        self.tree = ttk.Treeview(tree_frame, show='tree', selectmode='extended', height=20)
        self.tree.column('#0', width=220, minwidth=150)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(tree_frame, command=self.tree.yview)
        scroll.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>', self.tree_select)
        self.tree.bind('<Double-1>', lambda _: self.locate())
        self.button(left, '将选中节点批量放入图中', self.place_selected).pack(fill='x')
        self.button(left, '清理本次缺失节点…', self.clean_missing).pack(fill='x', pady=5)
        tools = ttk.Frame(center, padding=5)
        tools.pack(fill='x')
        self.mode_buttons = {}
        for mode in ('选择', '连接'):
            button = ttk.Radiobutton(tools, text='新增连接' if mode == '连接' else mode,
                                     variable=self.mode, value=mode,
                                     command=self.change_mode)
            button.pack(side='left')
            self.mode_buttons[mode] = button
        for label, var in [('500kV', self.layer500), ('220kV', self.layer220), ('待确认', self.layerother)]:
            ttk.Checkbutton(tools, text=label, variable=var, command=self.layers_changed).pack(side='left', padx=3)
        view_tools = ttk.Frame(center, padding=(5, 0, 5, 4))
        view_tools.pack(fill='x')
        self.button(view_tools, '−', lambda: self.zoom(1/1.25)).pack(side='left')
        self.button(view_tools, '＋', lambda: self.zoom(1.25)).pack(side='left')
        self.button(view_tools, '适应画布', self.fit).pack(side='left', padx=3)
        self.button(view_tools, '定位所选', self.locate).pack(side='left')
        self.button(view_tools, '取消连线', self.cancel_connect).pack(side='left', padx=3)
        self.canvas = tk.Canvas(center, width=400, height=360, background='#f2f6fa', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<ButtonPress-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.motion)
        self.canvas.bind('<ButtonRelease-1>', self.release)
        self.canvas.bind('<MouseWheel>', self.wheel)
        self.canvas.bind('<Configure>', self.canvas_configure)
        self.canvas.bind('<Map>', lambda _: self.queue_fit())
        self.canvas.bind('<Motion>', self.hover)
        self.canvas.bind('<Leave>', lambda _: self.clear_hover())
        self.label_font = tkfont.Font(self.canvas, font=('', 10))
        ttk.Label(center, text='左侧或图上选两端，确认后才连线；Esc 取消。拖动空白平移，密集名称悬停查看。',
                  wraplength=340).pack(fill='x', pady=5)
        ttk.Label(right, text='属性', font=('', 14, 'bold')).pack(anchor='w')
        self.detail = ttk.Label(right, text='选择节点或线路', wraplength=245)
        self.detail.pack(fill='x', pady=8)
        self.property_tabs = ttk.Notebook(right)
        self.property_tabs.pack(fill='both', expand=True)
        node_panel = self.node_panel = ttk.Frame(self.property_tabs, padding=8)
        self.line_panel = ttk.Frame(self.property_tabs, padding=8)
        variant_panel = ttk.Frame(self.property_tabs, padding=8)
        self.property_tabs.add(node_panel, text='节点')
        self.property_tabs.add(self.line_panel, text='线路')
        self.property_tabs.add(variant_panel, text='多价格')
        ttk.Label(node_panel, text='显示名称').pack(anchor='w')
        self.name_entry = ttk.Entry(node_panel, textvariable=self.name, width=25)
        self.name_entry.pack(fill='x')
        self.voltage_box = ttk.Combobox(node_panel, textvariable=self.voltage, state='readonly',
                                       values=['500kV', '220kV', '待确认'])
        self.voltage_box.pack(fill='x', pady=5)
        ttk.Label(node_panel, text='所属地市（人工填写）').pack(anchor='w', pady=(8, 0))
        self.city_entry = ttk.Entry(node_panel, textvariable=self.city)
        self.city_entry.pack(fill='x')
        self.button(node_panel, '更新属性', self.update_node).pack(fill='x', pady=5)
        self.button(node_panel, '从此节点连线', self.start_from_selected).pack(fill='x', pady=5)
        ttk.Label(node_panel, text='然后在左侧搜索并选择终点，或直接点击图上节点。',
                  wraplength=230).pack(fill='x', pady=(0, 5))
        ttk.Label(node_panel, text='所选节点的关联线路').pack(anchor='w')
        connection_frame = ttk.Frame(node_panel)
        connection_frame.pack(fill='both', expand=True)
        self.connections = tk.Listbox(connection_frame, height=3, exportselection=False, width=25)
        self.connections.pack(side='left', fill='both', expand=True)
        connection_scroll = ttk.Scrollbar(connection_frame, command=self.connections.yview)
        connection_scroll.pack(side='right', fill='y')
        self.connections.configure(yscrollcommand=connection_scroll.set)
        self.connections.bind('<<ListboxSelect>>', self.select_connection)
        ttk.Label(self.line_panel, text='连接类型').pack(anchor='w')
        ttk.Combobox(self.line_panel, textvariable=self.edge_kind, state='readonly',
                     values=['站间线路', '站内变压器']).pack(fill='x')
        self.button(self.line_panel, '更新所选线路类型', self.update_edge).pack(fill='x', pady=5)
        self.button(self.line_panel, '更换线路端点…', self.replace_edge).pack(fill='x', pady=5)
        ttk.Label(self.line_panel, text='这里修改已有线路。新增连接的类型在确认框中单独核对。',
                  wraplength=245).pack(fill='x', pady=8)
        ttk.Label(variant_panel, text='同站多价格处理', font=('', 12, 'bold')).pack(anchor='w')
        self.old_box = ttk.Combobox(variant_panel, textvariable=self.old_variant, state='readonly')
        self.old_box.pack(fill='x', pady=5)
        self.button(variant_panel, '对应此旧节点（保留其线路）', self.resolve_old).pack(fill='x')
        self.button(variant_panel, '确认为独立新增节点', lambda: self.resolve(None)).pack(fill='x', pady=5)
        self.button(variant_panel, '对比同站价格曲线', self.compare).pack(fill='x')
        ttk.Label(variant_panel, text='无法唯一对应的新曲线需要指定旧节点，或确认为独立新增。',
                  wraplength=245).pack(fill='x', pady=8)
        self.button(right, '删除所选节点 / 线路…', self.delete).pack(fill='x', pady=8)

    def button(self, parent, text, command):
        widget = ttk.Button(parent, text=text, command=command)
        self.buttons[text] = widget
        return widget

    def node(self, node_id=None):
        return next((n for n in self.data['nodes'] if n['id'] == (node_id or self.selected)), None)

    def labels(self):
        groups = defaultdict(list)
        for n in self.data['nodes']:
            groups[n['station'], n['voltage']].append(n)
        result = {}
        for values in groups.values():
            for index, n in enumerate(values, 1):
                voltage = f"{n['voltage']}kV" if n['voltage'] else '电压待确认'
                suffix = f' 〔{index}〕' if len(values) > 1 else ''
                result[n['id']] = f"{n['name']} · {voltage}{suffix}"
        return result

    def checkpoint(self):
        self.undo_stack.append(deepcopy(self.data))
        self.undo_stack = self.undo_stack[-40:]
        self.redo_stack.clear()
        self.dirty = True

    def undo(self):
        if self.busy or not self.undo_stack:
            return
        self.redo_stack.append(deepcopy(self.data))
        revision = self.data['revision']
        self.data = self.undo_stack.pop()
        self.data['revision'] = revision
        self.dirty = True
        self.refresh()
        self.notice.set('已撤销；如需恢复可点击“重做”。')

    def redo(self):
        if self.busy or not self.redo_stack:
            return
        self.undo_stack.append(deepcopy(self.data))
        revision = self.data['revision']
        self.data = self.redo_stack.pop()
        self.data['revision'] = revision
        self.dirty = True
        self.refresh()
        self.notice.set('已重做。')

    def refresh(self):
        if not self.node():
            self.selected = None
        if self.edge and not any((e['a'], e['b']) == self.edge for e in self.data['edges']):
            self.edge = None
        self.label_map = self.labels()
        self.refresh_tree()
        self.draw()
        self.inspect(force=True)
        self.queue_fit()
        counts = {v: sum(n['voltage'] == v and n['present'] for n in self.data['nodes']) for v in (500, 220, None)}
        self.status.set(f"{'有未保存修改 · ' if self.dirty else ''}{self.data['source']}  |  "
                        f"500kV {counts[500]} · 220kV {counts[220]} · 电压待确认 {counts[None]} · "
                        f"缺失 {sum(not n['present'] for n in self.data['nodes'])} · "
                        f"待对应 {sum(n['present'] and n['pending'] for n in self.data['nodes'])} · "
                        f"连接 {len(self.data['edges'])}")

    def refresh_tree(self):
        if not hasattr(self, 'tree'):
            return
        selection = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        groups = defaultdict(list)
        for n in self.data['nodes']:
            groups[n['station'], n['voltage']].append(n)
        for voltage, title in ((500, '500kV'), (220, '220kV'), (None, '电压待确认')):
            parent = self.tree.insert('', 'end', iid=f'v{voltage}', text=title, open=True)
            for (station, v), values in sorted(groups.items(), key=lambda x: (x[0][0], x[0][1] or 0)):
                if v != voltage:
                    continue
                shown = [n for n in values if self._filter_node(n, len(values))]
                if not shown:
                    continue
                group = parent
                if len(values) > 1:
                    group = self.tree.insert(parent, 'end', text=f'{station}（同站多价格）', open=True)
                for n in shown:
                    flags = (' [缺失]' if not n['present'] else '') + (' [待对应]' if n['pending'] else '')
                    flags += ' [未入图]' if n['anchor'] is None else ''
                    self.tree.insert(group, 'end', iid=n['id'], text=self.label_map[n['id']] + flags)
        keep = [i for i in selection if self.tree.exists(i)] if not self.edge else []
        if keep:
            self.tree.selection_set(keep)

    def _filter_node(self, n, variants):
        if self.search.get().strip().casefold() not in (n['name'] + n['station']).casefold():
            return False
        return {'全部': True, '未入图': n['anchor'] is None, '本次缺失': not n['present'],
                '待确认': n['pending'] or n['voltage'] is None,
                '同站多价格': variants > 1}[self.filter.get()]

    def visible(self, n):
        return n['anchor'] is not None and {500: self.layer500, 220: self.layer220, None: self.layerother}[n['voltage']].get()

    def xy(self, anchor):
        return anchor['x'] * self.scale + self.offset[0], anchor['y'] * self.scale + self.offset[1]

    def world(self, x, y):
        return {'x': max(0, min(self.data['image']['width'], (x-self.offset[0])/self.scale)),
                'y': max(0, min(self.data['image']['height'], (y-self.offset[1])/self.scale))}

    def draw(self):
        self.canvas.delete('all')
        self.items = {}
        self.node_markers = {}
        self.node_labels = {}
        self._hovered = None
        nodes = {n['id']: n for n in self.data['nodes']}
        for e in self.data['edges']:
            a, b = nodes[e['a']], nodes[e['b']]
            if not self.visible(a) or not self.visible(b):
                continue
            key = (e['a'], e['b'])
            active = self.edge == key
            color = '#1769cf' if active else '#9aa8b5' if not a['present'] or not b['present'] else '#526879'
            item = self.canvas.create_line(*self.xy(a['anchor']), *self.xy(b['anchor']),
                                           fill=color, width=4 if active else 2,
                                           dash=(5, 4) if e['kind'] == 'transformer' else ())
            self.items[item] = ('edge', key)
        if self.connect_from in nodes and self._connection_target in nodes:
            a, b = nodes[self.connect_from], nodes[self._connection_target]
            self.canvas.create_line(*self.xy(a['anchor']), *self.xy(b['anchor']),
                                    fill='#1769cf', width=3, dash=(7, 5), tags='connection-preview')
        visible = [n for n in self.data['nodes'] if self.visible(n)]
        for n in visible:
            x, y = self.xy(n['anchor'])
            active = n['id'] in (self.selected, self.connect_from, self._connection_target)
            color = '#b5bec7' if not n['present'] else '#e29b23' if n['pending'] else '#b54a65' if n['voltage'] == 500 else '#258b82'
            radius = 8 if active else 5
            item = self.canvas.create_oval(x-radius, y-radius, x+radius, y+radius,
                                           fill=color, outline='#1769cf' if active else 'white', width=2)
            self.items[item] = ('node', n['id'])
            self.node_markers[n['id']] = item
        # Labels do not cover other labels or node handles. Dense areas stay
        # editable; hover and the node library always reveal the full name.
        occupied = []
        handles = {n['id']: self.canvas.bbox(self.node_markers[n['id']]) for n in visible}
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        for n in sorted(visible, key=lambda n: n['id'] not in (self.selected, self.connect_from)):
            x, y = self.xy(n['anchor'])
            text_width = self.label_font.measure(n['name'])
            text_height = self.label_font.metrics('linespace')
            boxes = [(x+11, y-text_height-4, x+11+text_width, y-4),
                     (x+11, y+5, x+11+text_width, y+5+text_height),
                     (x-11-text_width, y-text_height-4, x-11, y-4),
                     (x-11-text_width, y+5, x-11, y+5+text_height)]
            box = next((b for b in boxes if 2 <= b[0] and b[2] <= width-2
                        and 2 <= b[1] and b[3] <= height-2
                        and not any(self.boxes_overlap(b, other) for other in occupied)
                        and not any(self.boxes_overlap(b, other) for key, other in handles.items()
                                    if key != n['id'])), None)
            if box is None:
                continue
            occupied.append(box)
            text = self.canvas.create_text(box[0], box[1], text=n['name'], anchor='nw',
                                           fill='#344455', font=self.label_font)
            self.items[text] = ('node', n['id'])
            self.node_labels[n['id']] = text

    @staticmethod
    def boxes_overlap(a, b):
        return a[0] < b[2]+3 and b[0] < a[2]+3 and a[1] < b[3]+3 and b[1] < a[3]+3

    def queue_fit(self):
        if self._auto_fit and self._fit_after_id is None:
            self._fit_after_id = self.after_idle(self._fit_when_ready)

    def _fit_when_ready(self):
        self._fit_after_id = None
        if self._auto_fit:
            self.fit()

    def canvas_configure(self, event):
        size = (event.width, event.height)
        old = self._viewport_size
        self._viewport_size = size
        self.status_label.configure(wraplength=max(300, self.winfo_width()-25))
        if self._auto_fit:
            self.queue_fit()
        else:
            if old:
                self.offset[0] += (size[0]-old[0])/2
                self.offset[1] += (size[1]-old[1])/2
            self.draw()

    def fit(self):
        self._auto_fit = True
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        # Tk emits idle callbacks before a new window has its real geometry.
        # A subsequent Map/Configure will retry; never fit against a 1px canvas.
        if not self.canvas.winfo_ismapped() or width < 120 or height < 100:
            return
        anchors = [n['anchor'] for n in self.data['nodes'] if self.visible(n)]
        if not anchors:
            self.draw()
            return
        x0, x1 = min(a['x'] for a in anchors), max(a['x'] for a in anchors)
        y0, y1 = min(a['y'] for a in anchors), max(a['y'] for a in anchors)
        self.scale = min((width-90)/max(300, x1-x0), (height-70)/max(300, y1-y0))
        self.offset = [(width-(x1+x0)*self.scale)/2, (height-(y1+y0)*self.scale)/2]
        self.draw()

    def layers_changed(self):
        self.clear_hover()
        self.draw()
        self.queue_fit()

    def locate(self):
        n = self.node()
        if n and n['anchor']:
            self._auto_fit = False
            {500: self.layer500, 220: self.layer220, None: self.layerother}[n['voltage']].set(True)
            self.scale = max(.5, self.scale)
            self.offset = [self.canvas.winfo_width()/2-n['anchor']['x']*self.scale,
                           self.canvas.winfo_height()/2-n['anchor']['y']*self.scale]
            self.draw()

    def zoom(self, factor, x=None, y=None):
        self._auto_fit = False
        x = self.canvas.winfo_width()/2 if x is None else x
        y = self.canvas.winfo_height()/2 if y is None else y
        before = ((x-self.offset[0])/self.scale, (y-self.offset[1])/self.scale)
        self.scale = max(.025, min(3, self.scale*factor))
        self.offset = [x-before[0]*self.scale, y-before[1]*self.scale]
        self.draw()

    def wheel(self, event):
        if event.delta:
            self.zoom(1.15 if event.delta > 0 else 1/1.15, event.x, event.y)

    def hit_test(self, x, y):
        # Prefer the nearest node handle to a crossing line or a long label.
        nearby = [(math.hypot(x-px, y-py), n['id']) for n in self.data['nodes']
                  if self.visible(n) for px, py in [self.xy(n['anchor'])]
                  if abs(x-px) <= 12 and abs(y-py) <= 12]
        if nearby:
            return 'node', min(nearby)[1]
        return next((self.items[i] for i in reversed(self.canvas.find_overlapping(
            x-4, y-4, x+4, y+4)) if i in self.items), None)

    def clear_hover(self):
        self.canvas.delete('hover')
        if self._connection_target is None:
            self.canvas.delete('connection-preview')
        self._hovered = None

    def hover(self, event):
        if self.drag:
            return
        if self.connect_from and self._connection_target is None:
            self.canvas.delete('connection-preview')
            self.canvas.create_line(*self.xy(self.node(self.connect_from)['anchor']), event.x, event.y,
                                    fill='#1769cf', width=2, dash=(7, 5), tags='connection-preview')
        hit = self.hit_test(event.x, event.y)
        node_id = hit[1] if hit and hit[0] == 'node' else None
        if node_id == self._hovered:
            return
        self.canvas.delete('hover')
        self._hovered = node_id
        if node_id is None:
            return
        x = min(event.x+15, max(4, self.canvas.winfo_width()-self.label_font.measure(self.label_map[node_id])-12))
        y = max(4, min(event.y+15, self.canvas.winfo_height()-30))
        text = self.canvas.create_text(x, y, text=self.label_map[node_id], anchor='nw',
                                       fill='#17324d', font=self.label_font, tags='hover')
        box = self.canvas.bbox(text)
        rect = self.canvas.create_rectangle(box[0]-4, box[1]-3, box[2]+4, box[3]+3,
                                            fill='#ffffff', outline='#a9bacb', tags='hover')
        self.canvas.tag_lower(rect, text)

    def press(self, event):
        if self.busy:
            return
        self.clear_hover()
        hit = self.hit_test(event.x, event.y)
        if hit and hit[0] == 'node':
            node_id = hit[1]
            if self.mode.get() == '连接':
                if self.connect_from and self.connect_from != node_id:
                    self.add_edge(self.connect_from, node_id)
                else:
                    self.begin_connection(node_id)
                self.draw()
                return
            self.selected, self.edge = node_id, None
            self.drag = ('node', event.x, event.y, False)
            if self.tree.exists(node_id):
                self.tree.selection_set(node_id)
                self.tree.see(node_id)
        elif hit:
            if self.mode.get() == '连接':
                self.notice.set('请选择节点作为终点；查看已有线路可先点击“取消连线”。')
                return
            self.edge = hit[1]
            self.selected = None
            self.tree.selection_remove(self.tree.selection())
        else:
            self.drag = ('pan', event.x, event.y, False)
        self.draw()
        self.inspect()

    def motion(self, event):
        if not self.drag:
            return
        mode, x, y, started = self.drag
        self._auto_fit = False
        if mode == 'node':
            if not started:
                if abs(event.x-x)+abs(event.y-y) < 4:
                    return
                self.checkpoint()
            self.node()['anchor'] = self.world(event.x, event.y)
        else:
            self.offset[0] += event.x-x
            self.offset[1] += event.y-y
        self.drag = (mode, event.x, event.y, True)
        self.draw()

    def release(self, _):
        if self.drag and self.drag[0] == 'node' and self.drag[3]:
            self.refresh()
        self.drag = None

    def change_mode(self):
        self.connect_from = None
        if self.mode.get() == '连接' and self.node() and self.node()['anchor']:
            self.begin_connection(self.selected)
            return
        self.notice.set('新增连接：先在左侧节点库或画布选择起点，再选择终点，确认后才写入。'
                        if self.mode.get() == '连接' else '选择模式：点击节点或线路编辑，拖动空白平移。')
        self.sync_actions()
        self.draw()

    def cancel_connect(self):
        if self._connection_dialog is not None:
            self._connection_dialog.destroy()
            self._connection_dialog = None
        self._connection_target = None
        self.connect_from = None
        self.mode.set('选择')
        self.notice.set('已退出连接模式。')
        if self.selected and self.tree.exists(self.selected):
            self.tree.selection_set(self.selected)
        self.sync_actions()
        self.draw()

    def start_from_selected(self):
        if self.selected and not self.busy:
            self.begin_connection(self.selected)

    def begin_connection(self, node_id):
        n = self.node(node_id)
        if n is None or n['anchor'] is None:
            return self.error('请先将起点放入画布，再开始连线。')
        self.mode.set('连接')
        self.connect_from = self.selected = node_id
        self.edge = None
        if self.tree.exists(node_id):
            self.tree.selection_set(node_id)
        self.notice.set(f'起点：{self.label_map[node_id]} → 请在左侧搜索／选择终点，或点图上终点；随后会弹出确认框。')
        self.inspect()
        self.draw()

    def tree_select(self, _):
        ids = [i for i in self.tree.selection() if self.node(i)]
        if ids:
            # selection_set and tree rebuilding queue virtual selection events.
            # Do not wipe typed properties or bounce back from a selected edge.
            if ids[0] == self.selected and self.edge is None:
                self.sync_actions()
                return
            if self.mode.get() == '连接':
                if self._connection_dialog is not None:
                    return
                if self.connect_from and self.connect_from != ids[0]:
                    self.add_edge(self.connect_from, ids[0])
                else:
                    self.begin_connection(ids[0])
                return
            self.selected = ids[0]
            self.edge = None
            self.draw()
            self.inspect()
        else:
            self.sync_actions()

    def inspect(self, force=False):
        n = self.node()
        context = ('node', self.selected) if n else ('edge', self.edge) if self.edge else None
        changed = context != self._inspected
        self._inspected = context
        self.connections.delete(0, 'end')
        self.connection_keys = []
        self.old_choices = {}
        if n:
            if changed or force:
                self.name.set(n['name'])
                self.city.set(n.get('city', ''))
                self.voltage.set(f"{n['voltage']}kV" if n['voltage'] else '待确认')
            if changed:
                self.property_tabs.select(self.node_panel)
            self.detail.configure(text=f"{self.label_map[n['id']]}\n" +
                                  ('本次缺失，保留原位置与线路' if not n['present'] else
                                   '同站多价格待对应，请指定旧节点或确认为新增' if n['pending'] else '节点有效'))
            for e in self.data['edges']:
                if n['id'] in (e['a'], e['b']):
                    other = e['b'] if e['a'] == n['id'] else e['a']
                    self.connections.insert('end', self.label_map[other])
                    self.connection_keys.append((e['a'], e['b']))
            self.old_choices = {self.label_map[o['id']]: o['id'] for o in self.data['nodes']
                                if not o['present'] and (o['station'], o.get('source_voltage', o['voltage']))
                                == (n['station'], n.get('source_voltage', n['voltage']))}
        elif self.edge:
            e = self.current_edge()
            self.detail.configure(text=f"{self.label_map[e['a']]}\n↔ {self.label_map[e['b']]}")
            self.edge_kind.set('站间线路' if e['kind'] == 'line' else '站内变压器')
            self.property_tabs.select(self.line_panel)
        else:
            self.detail.configure(text='选择节点或线路')
            self.name.set('')
            self.city.set('')
        self.old_box.configure(values=list(self.old_choices))
        if self.old_variant.get() not in self.old_choices:
            self.old_variant.set(next(iter(self.old_choices), ''))
        self.sync_actions()

    def sync_actions(self):
        n = self.node()
        ready = not self.busy
        placed = bool(n and n['anchor'] is not None)
        pending = bool(n and n['present'] and n['pending'])
        same_station = [other for other in self.data['nodes'] if n and
                        (other['station'], other['voltage']) == (n['station'], n['voltage'])]
        states = {'撤销': bool(self.undo_stack), '重做': bool(self.redo_stack),
                  '更新属性': bool(n), '从此节点连线': placed,
                  '取消连线': self.mode.get() == '连接',
                  '定位所选': placed, '更新所选线路类型': bool(self.edge),
                  '更换线路端点…': bool(self.edge), '删除所选节点 / 线路…': bool(n or self.edge),
                  '清理本次缺失节点…': any(not node['present'] for node in self.data['nodes']),
                  '将选中节点批量放入图中': any(self.node(i) and self.node(i)['anchor'] is None
                                             for i in self.tree.selection()),
                  '对应此旧节点（保留其线路）': pending and bool(getattr(self, 'old_choices', {})),
                  '确认为独立新增节点': pending,
                  '对比同站价格曲线': any(other['curve'] for other in same_station)}
        for label, button in self.buttons.items():
            button.state(['!disabled'] if ready and states.get(label, True) else ['disabled'])
        for entry in (self.name_entry, self.city_entry):
            entry.configure(state='normal' if ready and n else 'disabled')
        self.voltage_box.configure(state='readonly' if ready and n else 'disabled')
        self.old_box.configure(state='readonly' if ready and pending and self.old_choices else 'disabled')

    def place_selected(self):
        if self.busy:
            return
        nodes = [self.node(i) for i in self.tree.selection()]
        nodes = [n for n in nodes if n and n['anchor'] is None]
        if not nodes:
            return self.notice.set('请在节点库选择尚未入图的节点，可按 Command／Shift 多选。')
        left = max(0, (20-self.offset[0])/self.scale)
        top = max(0, (20-self.offset[1])/self.scale)
        right = min(self.data['image']['width'], (self.canvas.winfo_width()-20-self.offset[0])/self.scale)
        bottom = min(self.data['image']['height'], (self.canvas.winfo_height()-20-self.offset[1])/self.scale)
        if right-left < 100 or bottom-top < 100:
            left, top = 0, 0
            right, bottom = self.data['image']['width'], self.data['image']['height']
        occupied = [n['anchor'] for n in self.data['nodes'] if n['anchor']]
        # Space in world coordinates, rather than clamping all excess nodes to
        # the image edge. Larger batches get a finer grid of distinct positions.
        slots = []
        cells = max(4, len(nodes)*2)
        for _ in range(8):
            cols = max(2, math.ceil(math.sqrt(cells*(right-left)/(bottom-top))))
            rows = max(2, math.ceil(cells/cols))
            spacing = min((right-left)/(cols+1), (bottom-top)/(rows+1))*.4
            slots = [{'x': left+(i+1)*(right-left)/(cols+1),
                      'y': top+(j+1)*(bottom-top)/(rows+1)}
                     for j in range(rows) for i in range(cols)]
            slots = [p for p in slots if all(math.hypot(p['x']-q['x'], p['y']-q['y']) > spacing
                                             for q in occupied)]
            if len(slots) >= len(nodes):
                break
            cells *= 2
        if len(slots) < len(nodes):
            return self.error('当前区域没有足够空位，请先点击“适应画布”再添加。')
        self.checkpoint()
        for n, anchor in zip(nodes, slots):
            n['anchor'] = anchor
        self.notice.set(f'已将 {len(nodes)} 个节点放入画布，可拖动调整并连接。')
        self.refresh()

    def update_node(self):
        n = self.node()
        if not n or self.busy:
            return False
        if not self.name.get().strip():
            self.error('名称不能为空')
            return False
        voltage = {'500kV': 500, '220kV': 220, '待确认': None}[self.voltage.get()]
        patch = dict(name=self.name.get().strip(), city=self.city.get().strip(), voltage=voltage)
        if all(n.get(key) == value for key, value in patch.items()):
            return True
        proposed = deepcopy(self.data)
        target = next(node for node in proposed['nodes'] if node['id'] == n['id'])
        target.setdefault('source_voltage', n['voltage'])
        target.update(patch, voltage_override=voltage)
        if not self.accept_edit(proposed):
            return False
        self.notice.set('节点属性已更新；点击“保存草稿”可持久保存。')
        self.refresh()
        return True

    def accept_edit(self, proposed):
        try:
            model.validate_catalog(proposed)
        except ValueError as exc:
            self.error(f'{exc}。请检查线路页的端点和连接类型；本次修改未应用。')
            return False
        self.checkpoint()
        self.data = proposed
        return True

    def commit_properties(self):
        return self.update_node() if self.node() else True

    def current_edge(self):
        return next(e for e in self.data['edges'] if (e['a'], e['b']) == self.edge)

    def add_edge(self, a, b):
        """Review every proposed edge; no graph mutation precedes confirmation."""
        if self.busy:
            return
        if self._connection_dialog is not None:
            self._connection_dialog.lift()
            return
        if a == b:
            return self.error('不能连接节点自身')
        if any({e['a'], e['b']} == {a, b} for e in self.data['edges']):
            return self.error('这两个节点已经连接')
        na, nb = self.node(a), self.node(b)
        if not na['anchor'] or not nb['anchor']:
            return self.error('请先将两端节点放入画布')
        self.connect_from = a
        self._connection_target = b
        dialog = self._connection_dialog = tk.Toplevel(self)
        dialog.transient(self)
        dialog.title('确认新增连接')
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=18)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='请核对两端节点', font=('', 14, 'bold')).pack(anchor='w', pady=(0, 12))
        ttk.Label(body, text=f'起点：{self.label_map[a]}', wraplength=420).pack(anchor='w', pady=5)
        ttk.Label(body, text=f'终点：{self.label_map[b]}', wraplength=420).pack(anchor='w', pady=5)
        ttk.Label(body, text='连接类型').pack(anchor='w', pady=(12, 4))
        self.confirm_kind = tk.StringVar(dialog, '站间线路')
        ttk.Combobox(body, textvariable=self.confirm_kind, state='readonly', width=28,
                     values=['站间线路', '站内变压器']).pack(fill='x')
        self.confirm_issue = tk.StringVar(dialog)
        ttk.Label(body, textvariable=self.confirm_issue, wraplength=420).pack(fill='x', pady=10)
        actions = ttk.Frame(body)
        actions.pack(fill='x')
        self.cancel_connection_button = ttk.Button(actions, text='取消', command=self.cancel_connect)
        self.cancel_connection_button.pack(side='left')

        def confirm():
            if na['voltage'] != nb['voltage'] and self.confirm_kind.get() == '站间线路':
                return
            proposed = deepcopy(self.data)
            kind = 'transformer' if self.confirm_kind.get() == '站内变压器' else 'line'
            proposed['edges'].append(dict(a=a, b=b, kind=kind, group='人工编辑'))
            if not self.accept_edit(proposed):
                return
            self.edge_kind.set(self.confirm_kind.get())
            self.selected, self.edge = a, None
            self.cancel_connect()
            self.refresh()
            self.property_tabs.select(self.node_panel)
            self.notice.set(f'已确认连接 {na["name"]} ↔ {nb["name"]}，共 {len(self.data["edges"])} 条连接。')

        self.confirm_connection_button = ttk.Button(actions, text='确认连接', command=confirm)
        self.confirm_connection_button.pack(side='right')

        def validate_kind(*_):
            invalid = na['voltage'] != nb['voltage'] and self.confirm_kind.get() == '站间线路'
            self.confirm_issue.set('两端电压不同，请核对并选择“站内变压器”。' if invalid else
                                   '点击“确认连接”才新增线路，取消不会修改拓扑。')
            self.confirm_connection_button.state(['disabled'] if invalid else ['!disabled'])

        self.confirm_kind.trace_add('write', validate_kind)
        validate_kind()
        dialog.protocol('WM_DELETE_WINDOW', self.cancel_connect)
        dialog.bind(self.cancel_event, lambda _: self.cancel_connect())
        dialog.update_idletasks()
        dialog.grab_set()
        dialog.focus_set()
        self.notice.set('待确认：请在确认框核对起点、终点和连接类型；当前尚未新增线路。')
        self.draw()

    def update_edge(self):
        if not self.edge or self.busy:
            return
        proposed = deepcopy(self.data)
        edge = next(e for e in proposed['edges'] if (e['a'], e['b']) == self.edge)
        edge['kind'] = 'line' if self.edge_kind.get() == '站间线路' else 'transformer'
        if not self.accept_edit(proposed):
            return
        self.notice.set('线路类型已更新。')
        self.refresh()

    def select_connection(self, _):
        selection = self.connections.curselection()
        if selection:
            self.edge = self.connection_keys[selection[0]]
            self.selected = None
            self.tree.selection_remove(self.tree.selection())
            self.draw()
            self.inspect()

    def replace_edge(self):
        if not self.edge or self.busy:
            return
        edge = self.current_edge()
        dialog = tk.Toplevel(self)
        dialog.title('更换线路端点')
        options = {self.label_map[n['id']]: n['id'] for n in self.data['nodes'] if n['anchor']}
        a = tk.StringVar(dialog, self.label_map[edge['a']])
        b = tk.StringVar(dialog, self.label_map[edge['b']])
        for title, var in [('端点一', a), ('端点二', b)]:
            ttk.Label(dialog, text=title).pack(anchor='w', padx=15, pady=5)
            ttk.Combobox(dialog, textvariable=var, values=list(options), state='readonly', width=50).pack(padx=15)
        def commit():
            first, second = options[a.get()], options[b.get()]
            if first == second or any(e is not edge and {e['a'], e['b']} == {first, second} for e in self.data['edges']):
                return self.error('端点重复或已经存在这条连接')
            proposed = deepcopy(self.data)
            replacement = next(e for e in proposed['edges'] if (e['a'], e['b']) == self.edge)
            replacement.update(a=first, b=second)
            if not self.accept_edit(proposed):
                return
            self.edge = (first, second)
            self.notice.set('线路端点已更新。')
            dialog.destroy()
            self.refresh()
        ttk.Button(dialog, text='保存端点', command=commit).pack(pady=15)
        dialog.transient(self)
        dialog.grab_set()

    def resolve_old(self):
        old_id = self.old_choices.get(self.old_variant.get())
        if old_id:
            self.resolve(old_id)

    def resolve(self, old_id):
        if not self.node() or self.busy:
            return
        try:
            copy = deepcopy(self.data)
            selected = model.resolve_variant(copy, self.selected, old_id)
        except ValueError as exc:
            return self.error(str(exc))
        self.checkpoint()
        self.data, self.selected = copy, selected
        self.notice.set('价格节点已对应到旧节点，原有位置和线路已保留。' if old_id else '已确认为独立新增节点。')
        self.refresh()

    def compare(self):
        n = self.node()
        if not n:
            return
        nodes = [v for v in self.data['nodes'] if (v['station'], v['voltage']) == (n['station'], n['voltage'])]
        dialog = tk.Toplevel(self)
        dialog.title(f"{n['station']} · 同站价格对比")
        dialog.geometry('850x500')
        columns = ['time'] + [v['id'] for v in nodes]
        tree = ttk.Treeview(dialog, columns=columns, show='headings')
        for key, title in zip(columns, ['时刻']+[self.label_map[v['id']] for v in nodes]):
            tree.heading(key, text=title)
            tree.column(key, width=160, stretch=True)
        tree.tag_configure('different', background='#fff0cf')
        scroll = ttk.Scrollbar(dialog, command=tree.yview)
        scroll.pack(side='right', fill='y')
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(fill='both', expand=True)
        series = [dict(zip(v.get('curve_headers', self.data['headers']), v['curve'])) for v in nodes]
        for t in sorted({t for s in series for t in s}):
            values = [s.get(t) for s in series]
            tree.insert('', 'end', values=[t]+['暂无数据' if v is None else f'{v:g}' for v in values],
                        tags=('different',) if len(set(values)) > 1 else ())
        ttk.Label(dialog, text='浅黄色表示价格或缺失状态不同；缺失节点可能来自上一次导入。', padding=10).pack()

    def delete(self):
        if self.busy:
            return
        if self.edge:
            if messagebox.askyesno('删除连接', '删除所选线路？可撤销。', parent=self):
                self.checkpoint()
                self.data['edges'].remove(self.current_edge())
                self.edge = None
        elif self.selected:
            count = sum(self.selected in (e['a'], e['b']) for e in self.data['edges'])
            if messagebox.askyesno('删除节点', f'删除节点及其 {count} 条连接？可撤销。下次导入仍可能重新出现。', parent=self):
                self.checkpoint()
                self.data['nodes'].remove(self.node())
                self.data['edges'] = [e for e in self.data['edges'] if self.selected not in (e['a'], e['b'])]
                self.selected = None
        self.refresh()

    def clean_missing(self):
        if self.busy:
            return
        missing = {n['id'] for n in self.data['nodes'] if not n['present']}
        edges = sum(e['a'] in missing or e['b'] in missing for e in self.data['edges'])
        if missing and messagebox.askyesno('清理缺失节点', f'将删除 {len(missing)} 个缺失节点及 {edges} 条连接。可撤销。', parent=self):
            self.checkpoint()
            self.data['nodes'] = [n for n in self.data['nodes'] if n['id'] not in missing]
            self.data['edges'] = [e for e in self.data['edges'] if e['a'] not in missing and e['b'] not in missing]
            self.selected = self.edge = None
            self.refresh()

    def import_excel(self, source=None):
        if self.busy:
            return
        source = source or filedialog.askopenfilename(parent=self, title='选择最新节点电价表（替换本次节点清单）',
                                           initialdir=str(Path(self.input_path).parent) if self.input_path else None,
                                           filetypes=[('Excel', '*.xlsx')])
        if not source:
            return
        self.busy = True
        self.sync_actions()
        self.status.set('正在读取电价表并比较节点变化…')
        def work():
            try:
                self.events.put(('import', model.read_catalog(Path(source))))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            kind, value = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            if kind == 'error':
                self.refresh()
                self.error(value)
            else:
                try:
                    proposed = model.synchronize(self.data, value)
                    model.validate_catalog(proposed)
                except (ValueError, KeyError, TypeError) as exc:
                    self.refresh()
                    self.error(f'同步失败，原有拓扑已保留：{exc}')
                else:
                    self.checkpoint()
                    self.data = proposed
                    self.selected = self.edge = None
                    self.refresh()
                    s = self.data['summary']
                    self.notice.set(f"同步完成：新增 {s['added']}，缺失 {s['missing']}，待对应 {s['pending']}。")
                    messagebox.showinfo('同步完成', f"新增 {s['added']} · 更新 {s['updated']} · 缺失 {s['missing']}\n"
                                        f"待对应 {s['pending']} · 合并重复记录 {s['duplicates']}\n"
                                        '已有位置和连接已保留；请处理待对应项后保存。', parent=self)
        self.poll_id = self.after(100, self.poll)

    def save(self):
        if self.busy:
            return False
        if not self.commit_properties():
            return False
        try:
            self.data = model.save_catalog(self.data)
        except (ValueError, OSError) as exc:
            self.error(str(exc))
            return False
        self.dirty = False
        self.refresh()
        self.notice.set('草稿已保存，下次打开会保留节点、位置和连接。')
        return True

    def apply(self):
        if self.busy:
            return
        if not self.commit_properties():
            return
        try:
            nodes, edges = model.analysis_selection(self.data)
            # Applied state is independent of unsaved/subsequent draft edits.
            if not self.save():
                return
            model.save_catalog(self.data, model.applied_path(), check_revision=False)
        except (ValueError, OSError) as exc:
            return self.error(str(exc))
        if self.on_apply:
            self.on_apply(model.applied_path())
        self.notice.set(f'已应用：{len(nodes)} 个500kV节点、{len(edges)} 条站间线路。')
        messagebox.showinfo('已应用', f'后续分析使用当前拓扑：{len(nodes)} 个500kV节点、{len(edges)} 条线路。\n'
                            '220kV及站内变压器连接保留在编辑器中。', parent=self)

    def export(self):
        if self.busy or not self.commit_properties():
            return
        target = filedialog.asksaveasfilename(parent=self, title='导出当前拓扑及节点库',
                                            initialfile='500和220kV拓扑.json', defaultextension='.json')
        if target:
            try:
                model.validate_catalog(self.data)
                Path(target).write_text(json.dumps(self.data, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
                self.notice.set('拓扑备份已导出，包含当前节点、曲线和连接。')
            except (OSError, ValueError) as exc:
                self.error(str(exc))

    def import_json(self):
        if self.busy:
            return
        source = filedialog.askopenfilename(parent=self, title='导入拓扑备份', filetypes=[('JSON', '*.json')])
        if not source:
            return
        try:
            value = json.loads(Path(source).read_text(encoding='utf-8'))
            model.validate_catalog(value)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            return self.error(str(exc))
        if messagebox.askyesno('导入拓扑', '用备份替换当前草稿？可撤销。', parent=self):
            self.checkpoint()
            value['revision'] = self.data['revision']
            self.data = value
            self.selected = self.edge = None
            self.refresh()
            self.fit()
            self.notice.set('拓扑备份已导入；检查后保存草稿或应用到分析。')

    def error(self, text):
        messagebox.showerror('拓扑编辑', text, parent=self)

    def close(self):
        if self.busy:
            self.error('正在读取电价表，请等待同步完成')
            return False
        n = self.node()
        edited_properties = bool(n and (self.name.get().strip() != n['name']
                                 or self.city.get().strip() != n.get('city', '')
                                 or self.voltage.get() != (f"{n['voltage']}kV" if n['voltage'] else '待确认')))
        if self.dirty or edited_properties:
            answer = messagebox.askyesnocancel('保存修改', '关闭前保存当前拓扑草稿？', parent=self)
            if answer is None or (answer and not self.save()):
                return False
        self.after_cancel(self.poll_id)
        if self._fit_after_id is not None:
            self.after_cancel(self._fit_after_id)
        if self._import_after_id is not None:
            self.after_cancel(self._import_after_id)
        self.destroy()
        return True
