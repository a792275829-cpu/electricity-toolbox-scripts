"""Native editor opened from the section analysis module."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from toolbox_shared import section_topology as model


class TopologyEditor(tk.Toplevel):
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
        self.name = tk.StringVar(self)
        self.city = tk.StringVar(self)
        self.voltage = tk.StringVar(self, '待确认')
        self.edge_kind = tk.StringVar(self, '站间线路')
        self.neighbor = tk.StringVar(self)
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
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _: self.cancel_connect())
        self.bind('<Command-z>', lambda _: self.undo())
        self.bind('<Command-Shift-Z>', lambda _: self.redo())
        self.bind('<Command-s>', lambda _: self.save())
        self._build()
        self.refresh()
        self.after_idle(self.fit)
        self.poll_id = self.after(100, self.poll)
        if input_path:
            self.after(200, lambda: self.import_excel(input_path))

    def _build(self):
        bar = ttk.Frame(self, padding=10)
        bar.pack(fill='x')
        for title, command in [('导入新电价表', self.import_excel), ('撤销', self.undo), ('重做', self.redo),
                               ('保存草稿', self.save), ('应用到分析', self.apply),
                               ('导出拓扑', self.export), ('导入拓扑', self.import_json)]:
            ttk.Button(bar, text=title, command=command).pack(side='left', padx=3)
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
        ttk.Button(left, text='将选中节点批量放入图中', command=self.place_selected).pack(fill='x')
        ttk.Button(left, text='清理本次缺失节点…', command=self.clean_missing).pack(fill='x', pady=5)
        tools = ttk.Frame(center, padding=5)
        tools.pack(fill='x')
        self.mode_buttons = {}
        for mode in ('选择', '连接'):
            button = ttk.Radiobutton(tools, text=mode, variable=self.mode, value=mode,
                                     command=self.change_mode)
            button.pack(side='left')
            self.mode_buttons[mode] = button
        for label, var in [('500kV', self.layer500), ('220kV', self.layer220), ('待确认', self.layerother)]:
            ttk.Checkbutton(tools, text=label, variable=var, command=self.draw).pack(side='left', padx=3)
        ttk.Button(tools, text='适应画布', command=self.fit).pack(side='right')
        self.canvas = tk.Canvas(center, background='#f2f6fa', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<ButtonPress-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.motion)
        self.canvas.bind('<ButtonRelease-1>', self.release)
        self.canvas.bind('<MouseWheel>', self.wheel)
        ttk.Label(center, text='拖动节点调整位置 · 拖动空白平移 · 滚轮缩放 · 连接模式依次点击两端 · Esc 退出',
                  wraplength=600).pack(fill='x', pady=5)
        ttk.Label(right, text='属性', font=('', 14, 'bold')).pack(anchor='w')
        self.detail = ttk.Label(right, text='选择节点或线路', wraplength=245)
        self.detail.pack(fill='x', pady=8)
        self.property_tabs = ttk.Notebook(right)
        self.property_tabs.pack(fill='both', expand=True)
        node_panel = ttk.Frame(self.property_tabs, padding=8)
        self.line_panel = ttk.Frame(self.property_tabs, padding=8)
        variant_panel = ttk.Frame(self.property_tabs, padding=8)
        self.property_tabs.add(node_panel, text='节点')
        self.property_tabs.add(self.line_panel, text='线路')
        self.property_tabs.add(variant_panel, text='多价格')
        ttk.Label(node_panel, text='显示名称').pack(anchor='w')
        ttk.Entry(node_panel, textvariable=self.name, width=25).pack(fill='x')
        ttk.Combobox(node_panel, textvariable=self.voltage, state='readonly',
                     values=['500kV', '220kV', '待确认']).pack(fill='x', pady=5)
        ttk.Label(node_panel, text='所属地市（人工填写）').pack(anchor='w', pady=(8, 0))
        ttk.Entry(node_panel, textvariable=self.city).pack(fill='x')
        ttk.Button(node_panel, text='更新属性', command=self.update_node).pack(fill='x', pady=7)
        ttk.Label(node_panel, text='快速连接：输入站名筛选').pack(anchor='w', pady=(8, 0))
        self.neighbor_box = ttk.Combobox(node_panel, textvariable=self.neighbor)
        self.neighbor_box.pack(fill='x')
        self.neighbor_box.bind('<KeyRelease>', self.filter_neighbors)
        ttk.Button(node_panel, text='连接到此节点', command=self.quick_connect).pack(fill='x', pady=5)
        ttk.Label(node_panel, text='所选节点的关联线路').pack(anchor='w')
        self.connections = tk.Listbox(node_panel, height=5, exportselection=False, width=27)
        self.connections.pack(fill='x')
        self.connections.bind('<<ListboxSelect>>', self.select_connection)
        ttk.Label(self.line_panel, text='连接类型').pack(anchor='w')
        ttk.Combobox(self.line_panel, textvariable=self.edge_kind, state='readonly',
                     values=['站间线路', '站内变压器']).pack(fill='x')
        ttk.Button(self.line_panel, text='更新所选线路类型', command=self.update_edge).pack(fill='x', pady=5)
        ttk.Button(self.line_panel, text='更换线路端点…', command=self.replace_edge).pack(fill='x', pady=5)
        ttk.Label(self.line_panel, text='跨电压连接请选择站内变压器。连接模式也使用这里选择的类型。',
                  wraplength=245).pack(fill='x', pady=8)
        ttk.Label(variant_panel, text='同站多价格处理', font=('', 12, 'bold')).pack(anchor='w')
        self.old_box = ttk.Combobox(variant_panel, textvariable=self.old_variant, state='readonly')
        self.old_box.pack(fill='x', pady=5)
        ttk.Button(variant_panel, text='对应此旧节点（保留其线路）', command=self.resolve_old).pack(fill='x')
        ttk.Button(variant_panel, text='确认为独立新增节点', command=lambda: self.resolve(None)).pack(fill='x', pady=5)
        ttk.Button(variant_panel, text='对比同站价格曲线', command=self.compare).pack(fill='x')
        ttk.Label(variant_panel, text='无法唯一对应的新曲线需要指定旧节点，或确认为独立新增。',
                  wraplength=245).pack(fill='x', pady=8)
        ttk.Button(right, text='删除所选节点 / 线路…', command=self.delete).pack(fill='x', pady=8)
        ttk.Label(self, textvariable=self.status, padding=10).pack(fill='x')

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

    def redo(self):
        if self.busy or not self.redo_stack:
            return
        self.undo_stack.append(deepcopy(self.data))
        revision = self.data['revision']
        self.data = self.redo_stack.pop()
        self.data['revision'] = revision
        self.dirty = True
        self.refresh()

    def refresh(self):
        if not self.node():
            self.selected = None
        if self.edge and not any((e['a'], e['b']) == self.edge for e in self.data['edges']):
            self.edge = None
        self.label_map = self.labels()
        self.refresh_tree()
        self.draw()
        self.inspect()
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
        for n in self.data['nodes']:
            if not self.visible(n):
                continue
            x, y = self.xy(n['anchor'])
            active = n['id'] in (self.selected, self.connect_from)
            color = '#b5bec7' if not n['present'] else '#e29b23' if n['pending'] else '#b54a65' if n['voltage'] == 500 else '#258b82'
            radius = 8 if active else 5
            item = self.canvas.create_oval(x-radius, y-radius, x+radius, y+radius,
                                           fill=color, outline='#1769cf' if active else 'white', width=2)
            self.items[item] = ('node', n['id'])
            text = self.canvas.create_text(x+9, y-9, text=n['name'] if self.scale < .2 else self.label_map[n['id']],
                                           anchor='w', fill='#344455', font=('', 10))
            self.items[text] = ('node', n['id'])

    def fit(self):
        anchors = [n['anchor'] for n in self.data['nodes'] if self.visible(n)]
        if not anchors:
            return
        x0, x1 = min(a['x'] for a in anchors), max(a['x'] for a in anchors)
        y0, y1 = min(a['y'] for a in anchors), max(a['y'] for a in anchors)
        self.scale = min((max(100, self.canvas.winfo_width())-70)/max(300, x1-x0),
                         (max(100, self.canvas.winfo_height())-70)/max(300, y1-y0))
        self.offset = [35-x0*self.scale, 35-y0*self.scale]
        self.draw()

    def locate(self):
        n = self.node()
        if n and n['anchor']:
            self.scale = max(.5, self.scale)
            self.offset = [self.canvas.winfo_width()/2-n['anchor']['x']*self.scale,
                           self.canvas.winfo_height()/2-n['anchor']['y']*self.scale]
            self.draw()

    def wheel(self, event):
        before = self.world(event.x, event.y)
        self.scale = max(.04, min(3, self.scale * (1.15 if event.delta > 0 else 1/1.15)))
        self.offset = [event.x-before['x']*self.scale, event.y-before['y']*self.scale]
        self.draw()

    def press(self, event):
        if self.busy:
            return
        hit = next((self.items[i] for i in reversed(self.canvas.find_overlapping(
            event.x-5, event.y-5, event.x+5, event.y+5)) if i in self.items), None)
        if hit and hit[0] == 'node':
            node_id = hit[1]
            if self.mode.get() == '连接':
                if self.connect_from:
                    self.add_edge(self.connect_from, node_id)
                    self.connect_from = None
                else:
                    self.connect_from = node_id
                self.draw()
                return
            self.selected, self.edge = node_id, None
            self.drag = ('node', event.x, event.y, False)
            if self.tree.exists(node_id):
                self.tree.selection_set(node_id)
                self.tree.see(node_id)
        elif hit:
            self.edge = hit[1]
            self.selected = None
        else:
            self.drag = ('pan', event.x, event.y, False)
        self.draw()
        self.inspect()

    def motion(self, event):
        if not self.drag:
            return
        mode, x, y, started = self.drag
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
        self.draw()

    def cancel_connect(self):
        self.connect_from = None
        self.mode.set('选择')
        self.draw()

    def tree_select(self, _):
        ids = [i for i in self.tree.selection() if self.node(i)]
        if ids:
            self.selected = ids[0]
            self.edge = None
            self.draw()
            self.inspect()

    def inspect(self):
        n = self.node()
        self.connections.delete(0, 'end')
        self.connection_keys = []
        self.old_choices = {}
        self.neighbors = {label: node_id for node_id, label in self.label_map.items() if node_id != self.selected}
        self.neighbor_box.configure(values=list(self.neighbors))
        if n:
            self.name.set(n['name'])
            self.city.set(n.get('city', ''))
            self.voltage.set(f"{n['voltage']}kV" if n['voltage'] else '待确认')
            self.detail.configure(text=f"{self.label_map[n['id']]}\n" +
                                  ('本次缺失，保留原位置与线路' if not n['present'] else
                                   '同站多价格待对应，请指定旧节点或确认为新增' if n['pending'] else '节点有效'))
            for e in self.data['edges']:
                if n['id'] in (e['a'], e['b']):
                    other = e['b'] if e['a'] == n['id'] else e['a']
                    self.connections.insert('end', self.label_map[other])
                    self.connection_keys.append((e['a'], e['b']))
            self.old_choices = {self.label_map[o['id']]: o['id'] for o in self.data['nodes']
                                if not o['present'] and (o['station'], o['voltage']) == (n['station'], n['voltage'])}
        elif self.edge:
            e = self.current_edge()
            self.detail.configure(text=f"{self.label_map[e['a']]}\n↔ {self.label_map[e['b']]}")
            self.edge_kind.set('站间线路' if e['kind'] == 'line' else '站内变压器')
            self.property_tabs.select(self.line_panel)
        else:
            self.detail.configure(text='选择节点或线路')
        self.old_box.configure(values=list(self.old_choices))
        self.old_variant.set(next(iter(self.old_choices), ''))

    def place_selected(self):
        if self.busy:
            return
        nodes = [self.node(i) for i in self.tree.selection()]
        nodes = [n for n in nodes if n and n['anchor'] is None]
        if not nodes:
            return
        self.checkpoint()
        center = self.world(self.canvas.winfo_width()/2, self.canvas.winfo_height()/2)
        for i, n in enumerate(nodes):
            n['anchor'] = {'x': min(self.data['image']['width'], center['x']+(i%4)*140),
                           'y': min(self.data['image']['height'], center['y']+(i//4)*100)}
        self.refresh()

    def update_node(self):
        n = self.node()
        if not n or self.busy:
            return
        if not self.name.get().strip():
            return self.error('名称不能为空')
        self.checkpoint()
        voltage = {'500kV': 500, '220kV': 220, '待确认': None}[self.voltage.get()]
        n.setdefault('source_voltage', n['voltage'])
        n.update(name=self.name.get().strip(), city=self.city.get().strip(), voltage=voltage,
                 voltage_override=voltage)
        self.refresh()

    def current_edge(self):
        return next(e for e in self.data['edges'] if (e['a'], e['b']) == self.edge)

    def add_edge(self, a, b):
        if a == b:
            return self.error('不能连接节点自身')
        if any({e['a'], e['b']} == {a, b} for e in self.data['edges']):
            return self.error('这两个节点已经连接')
        na, nb = self.node(a), self.node(b)
        if not na['anchor'] or not nb['anchor']:
            return self.error('请先将两端节点放入画布')
        kind = 'transformer' if self.edge_kind.get() == '站内变压器' else 'line'
        if na['voltage'] != nb['voltage'] and kind == 'line':
            return self.error('跨电压连接请明确选择“站内变压器”')
        self.checkpoint()
        self.data['edges'].append(dict(a=a, b=b, kind=kind, group='人工编辑'))
        self.refresh()

    def update_edge(self):
        if not self.edge or self.busy:
            return
        self.checkpoint()
        self.current_edge()['kind'] = 'line' if self.edge_kind.get() == '站间线路' else 'transformer'
        self.refresh()

    def filter_neighbors(self, _):
        query = self.neighbor.get().casefold()
        self.neighbor_box.configure(values=[s for s in self.neighbors if query in s.casefold()])

    def quick_connect(self):
        if self.selected and self.neighbor.get() in self.neighbors and not self.busy:
            self.add_edge(self.selected, self.neighbors[self.neighbor.get()])

    def select_connection(self, _):
        selection = self.connections.curselection()
        if selection:
            self.edge = self.connection_keys[selection[0]]
            self.selected = None
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
            self.checkpoint()
            edge.update(a=first, b=second)
            self.edge = (first, second)
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
                self.checkpoint()
                self.data = model.synchronize(self.data, value)
                self.selected = self.edge = None
                self.refresh()
                s = self.data['summary']
                messagebox.showinfo('同步完成', f"新增 {s['added']} · 更新 {s['updated']} · 缺失 {s['missing']}\n"
                                    f"待对应 {s['pending']} · 合并重复记录 {s['duplicates']}\n"
                                    '已有位置和连接已保留；请处理待对应项后保存。', parent=self)
        self.poll_id = self.after(100, self.poll)

    def save(self):
        if self.busy:
            return False
        try:
            self.data = model.save_catalog(self.data)
        except (ValueError, OSError) as exc:
            self.error(str(exc))
            return False
        self.dirty = False
        self.refresh()
        return True

    def apply(self):
        if self.busy:
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
        messagebox.showinfo('已应用', f'后续分析使用当前拓扑：{len(nodes)} 个500kV节点、{len(edges)} 条线路。\n'
                            '220kV及站内变压器连接保留在编辑器中。', parent=self)

    def export(self):
        target = filedialog.asksaveasfilename(parent=self, title='导出当前拓扑及节点库',
                                            initialfile='500和220kV拓扑.json', defaultextension='.json')
        if target:
            try:
                model.validate_catalog(self.data)
                Path(target).write_text(json.dumps(self.data, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
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

    def error(self, text):
        messagebox.showerror('拓扑编辑', text, parent=self)

    def close(self):
        if self.busy:
            self.error('正在读取电价表，请等待同步完成')
            return False
        if self.dirty:
            answer = messagebox.askyesnocancel('保存修改', '关闭前保存当前拓扑草稿？', parent=self)
            if answer is None or (answer and not self.save()):
                return False
        self.after_cancel(self.poll_id)
        self.destroy()
        return True
