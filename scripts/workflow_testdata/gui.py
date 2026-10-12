"""Native Windows desktop interface; database work never blocks Tk's event loop."""
from __future__ import annotations

from datetime import datetime
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import webbrowser

from .database import DataError, load_connection_and_flows
from .generation import BATCHES, DEFAULT_OPTIONS, prepare, undo_batch, write_batch

KINDS = {'全部工作流': None, '产品质检': 'quality', '设备巡检': 'inspection', '设备保养': 'maintenance', '设备维修': 'repair'}
LABELS = {value: key for key, value in KINDS.items()}
MODES = {'全部合格 / 正常': 'pass', '全部不合格 / 异常': 'fail', '随机混合': 'mixed'}


class DataGeneratorApp:
    def __init__(self, root, *, auto_connect=True):
        self.root = root
        root.title('工作流测试数据生成器 · 本地 szh_new')
        root.geometry(f"1240x{min(1000, root.winfo_screenheight() - 100)}")
        root.minsize(1060, 720)
        root.configure(bg='#edf2f8')
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.credentials, self.flows, self.batch = None, [], None
        self.options = dict(DEFAULT_OPTIONS)
        self.busy = self.faulted = False
        self.queue = queue.Queue()
        self.kind = tk.StringVar(value='全部工作流')
        self.mode = tk.StringVar(value='随机混合')
        self.count = tk.StringVar(value='2')
        self.ratio = tk.StringVar(value='50')
        self.supplement = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value='正在核对本机数据库…' if auto_connect else '等待加载')
        self.summary = tk.StringVar(value='先选择工作流并生成预览')
        self.selection_summary = tk.StringVar(value='尚未选择工作流')
        self.style()
        self.layout()
        for value in (self.mode, self.count, self.ratio, self.supplement):
            value.trace_add('write', lambda *args: self.invalidate())
        root.after(80, self.poll)
        if auto_connect:
            root.after(150, self.connect)

    def style(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10), background='#ffffff', foreground='#20314c')
        style.configure('Treeview', rowheight=31, fieldbackground='#ffffff', background='#ffffff', borderwidth=0)
        style.configure('Treeview.Heading', background='#eef3f9', font=('Microsoft YaHei UI', 10, 'bold'), padding=8)
        style.map('Treeview', background=[('selected', '#e3edff')], foreground=[('selected', '#154dc3')])
        style.configure('TButton', padding=(13, 9), borderwidth=1)
        style.configure('Primary.TButton', background='#2563eb', foreground='white', font=('Microsoft YaHei UI', 10, 'bold'))
        style.map('Primary.TButton', background=[('disabled', '#cbd5e1'), ('active', '#1d4ed8')])
        style.configure('Write.TButton', background='#087b69', foreground='white', font=('Microsoft YaHei UI', 10, 'bold'))
        style.map('Write.TButton', background=[('disabled', '#cbd5e1'), ('active', '#056253')])
        style.configure('Muted.TLabel', foreground='#697b93')
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 13, 'bold'))
        style.configure('TCombobox', padding=7)
        style.configure('TSpinbox', padding=7)

    def layout(self):
        header = tk.Frame(self.root, bg='#142942', padx=24, pady=18)
        header.pack(fill='x')
        tk.Label(header, text='工作流测试数据生成器', bg='#142942', fg='white', font=('Microsoft YaHei UI', 19, 'bold')).pack(anchor='w')
        tk.Label(header, text='本机 szh_new  ·  预览后写入  ·  新增数据由下一轮定时工作流采集',
                 bg='#142942', fg='#b8d1eb', font=('Microsoft YaHei UI', 10)).pack(anchor='w', pady=(7, 0))
        status_bar = ttk.Frame(self.root, padding=(18, 9))
        status_bar.pack(fill='x')
        ttk.Label(status_bar, textvariable=self.status, style='Muted.TLabel').pack(side='left')
        self.connect_button = ttk.Button(status_bar, text='重新加载配置', command=self.connect)
        self.connect_button.pack(side='right')
        self.history_button = ttk.Button(status_bar, text='历史批次 / 撤销', command=self.history)
        self.history_button.pack(side='right', padx=8)

        body = ttk.Panedwindow(self.root, orient='horizontal')
        body.pack(fill='both', expand=True, padx=16, pady=(5, 14))
        left = ttk.Frame(body, padding=14)
        body.add(left, weight=1)
        ttk.Label(left, text='选择工作流', style='Title.TLabel').pack(anchor='w', pady=(0, 10))
        self.filter = ttk.Combobox(left, textvariable=self.kind, values=list(KINDS), state='readonly')
        self.filter.pack(fill='x')
        self.filter.bind('<<ComboboxSelected>>', lambda event: self.populate_flows())
        ttk.Label(left, text='Ctrl / Shift 可多选', style='Muted.TLabel').pack(anchor='w', pady=8)
        flow_frame = ttk.Frame(left)
        flow_frame.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(flow_frame, show='tree', selectmode='extended', height=17)
        self.tree.column('#0', width=285, minwidth=230)
        scroll = ttk.Scrollbar(flow_frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.tree.bind('<<TreeviewSelect>>', lambda event: self.invalidate())
        actions = ttk.Frame(left)
        actions.pack(fill='x', pady=(10, 5))
        ttk.Button(actions, text='全选当前分类', command=self.select_all).pack(side='left')
        ttk.Button(actions, text='清空', command=lambda: self.tree.selection_remove(self.tree.selection())).pack(side='left', padx=6)
        ttk.Label(left, textvariable=self.selection_summary, style='Muted.TLabel', wraplength=285).pack(anchor='w', pady=5)

        right = ttk.Frame(body, padding=16)
        body.add(right, weight=3)
        ttk.Label(right, text='生成设置', style='Title.TLabel').pack(anchor='w')
        form = ttk.Frame(right)
        form.pack(fill='x', pady=(12, 8))
        for column, name in enumerate(('预期结果', '每条工作流生成数量', '混合模式不合格占比')):
            ttk.Label(form, text=name, style='Muted.TLabel').grid(row=0, column=column, sticky='w', padx=(0, 14), pady=(0, 6))
        self.mode_box = ttk.Combobox(form, textvariable=self.mode, values=list(MODES), state='readonly', width=21)
        self.mode_box.grid(row=1, column=0, sticky='ew', padx=(0, 14))
        self.count_box = ttk.Spinbox(form, from_=1, to=20, textvariable=self.count, width=12)
        self.count_box.grid(row=1, column=1, sticky='ew', padx=(0, 14))
        self.ratio_box = ttk.Combobox(form, textvariable=self.ratio, values=['0', '10', '25', '50', '75', '90', '100'], width=12, state='readonly')
        self.ratio_box.grid(row=1, column=2, sticky='ew')
        ttk.Label(right, text='数量指质检单或设备任务；不合格占比按份数取整，每批最多 50 份。', style='Muted.TLabel').pack(anchor='w', pady=(0, 8))
        settings = ttk.Frame(right)
        settings.pack(fill='x')
        self.supplement_box = ttk.Checkbutton(settings, text='缺少依据时，补充测试标准、模板或独立计划', variable=self.supplement)
        self.supplement_box.pack(side='left')
        self.settings_button = ttk.Button(settings, text='查看测试参数', command=self.edit_options)
        self.settings_button.pack(side='right')
        ttk.Label(right, text='只新增测试记录，不修改已有模板、计划或工作流。补充内容会在预览中标明。',
                  style='Muted.TLabel').pack(anchor='w', pady=(7, 14))
        buttons = ttk.Frame(right)
        buttons.pack(fill='x')
        self.preview_button = ttk.Button(buttons, text='1. 生成预览', style='Primary.TButton', command=self.preview)
        self.preview_button.pack(side='left')
        self.write_button = ttk.Button(buttons, text='2. 写入本地数据库', style='Write.TButton', command=self.write)
        self.write_button.pack(side='left', padx=10)
        self.open_button = ttk.Button(buttons, text='查看工作流', command=self.open_workflow)
        self.open_button.pack(side='right')
        ttk.Label(right, textvariable=self.summary, font=('Microsoft YaHei UI', 11, 'bold')).pack(anchor='w', pady=(18, 10))
        footer = ttk.Frame(right)
        footer.pack(side='bottom', fill='x')
        ttk.Label(footer, text='所选记录详情 / 操作结果', style='Muted.TLabel').pack(anchor='w', pady=(8, 4))
        detail_frame = ttk.Frame(footer)
        detail_frame.pack(fill='x')
        self.details = tk.Text(detail_frame, height=4, wrap='word', bd=0, padx=10, pady=7, background='#f2f6fb',
                               foreground='#334760', font=('Microsoft YaHei UI', 10), state='disabled')
        detail_scroll = ttk.Scrollbar(detail_frame, orient='vertical', command=self.details.yview)
        self.details.configure(yscrollcommand=detail_scroll.set)
        self.details.pack(side='left', fill='both', expand=True)
        detail_scroll.pack(side='right', fill='y')
        preview_frame = ttk.Frame(right)
        preview_frame.pack(fill='both', expand=True)
        self.preview_tree = ttk.Treeview(preview_frame, columns=('flow', 'subject', 'result', 'rules'), show='headings', height=7)
        for key, label, width in [('flow', '工作流', 225), ('subject', '单据 / 设备', 210), ('result', '预期结果', 120), ('rules', '判断依据', 125)]:
            self.preview_tree.heading(key, text=label)
            self.preview_tree.column(key, width=width, minwidth=95)
        preview_scroll = ttk.Scrollbar(preview_frame, orient='vertical', command=self.preview_tree.yview)
        horizontal = ttk.Scrollbar(preview_frame, orient='horizontal', command=self.preview_tree.xview)
        self.preview_tree.configure(yscrollcommand=preview_scroll.set, xscrollcommand=horizontal.set)
        self.preview_tree.grid(row=0, column=0, sticky='nsew')
        preview_scroll.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        preview_frame.rowconfigure(0, weight=1)
        preview_frame.columnconfigure(0, weight=1)
        self.preview_tree.tag_configure('pass', foreground='#087b69')
        self.preview_tree.tag_configure('fail', foreground='#b42332')
        self.preview_tree.bind('<<TreeviewSelect>>', self.show_details)
        self.controls()

    def controls(self):
        ready = bool(self.credentials) and not self.busy and not self.faulted
        self.preview_button.configure(state='normal' if ready else 'disabled')
        self.write_button.configure(state='normal' if ready and self.batch and self.batch['status'] == 'preview' else 'disabled')
        self.connect_button.configure(state='normal' if not self.busy and not self.faulted else 'disabled')
        self.history_button.configure(state='normal' if ready else 'disabled')
        self.settings_button.configure(state='disabled' if self.busy else 'normal')
        self.filter.configure(state='disabled' if self.busy else 'readonly')
        self.mode_box.configure(state='disabled' if self.busy else 'readonly')
        self.count_box.configure(state='disabled' if self.busy else 'normal')
        self.ratio_box.configure(state='readonly' if not self.busy and self.mode.get() == '随机混合' else 'disabled')
        self.supplement_box.configure(state='disabled' if self.busy else 'normal')

    def detail(self, text):
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', text)
        self.details.configure(state='disabled')

    def run(self, title, worker, done):
        if self.busy or self.faulted:
            return
        self.busy = True
        self.status.set(title)
        self.controls()
        def work():
            try:
                self.queue.put((True, worker(), done))
            except Exception as error:
                self.queue.put((False, error, done))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            ok, value, done = self.queue.get_nowait()
            self.busy = False
            if ok:
                done(value)
            else:
                if not isinstance(value, (DataError, ValueError)):
                    self.faulted = True
                    message = '本次数据库操作未完成，已停止后续操作。请检查本机数据库后重新打开工具。'
                    if self.batch and self.batch['status'] == 'unconfirmed':
                        message += '\n写入未获确认，批次记录已保留，请先核对该批次，避免重复生成。'
                else:
                    message = str(value)
                self.status.set('操作停止 · ' + message.split('\n')[0])
                self.detail(message)
                messagebox.showerror('操作未完成', message, parent=self.root)
            self.controls()
        except queue.Empty:
            pass
        self.root.after(80, self.poll)

    def connect(self):
        self.run('正在核对本机数据库并加载已发布工作流…', load_connection_and_flows, self.loaded)

    def loaded(self, result):
        self.credentials, self.flows = result
        self.populate_flows()
        self.status.set(f'已连接本机 szh_new · 已加载 {len(self.flows)} 条工作流 · 尚未写入数据')

    def populate_flows(self):
        self.tree.delete(*self.tree.get_children())
        selected_kind = KINDS[self.kind.get()]
        for flow in self.flows:
            if selected_kind is None or selected_kind == flow['kind']:
                self.tree.insert('', 'end', iid=flow['id'], text=flow['name'])
        preferred = next((f['id'] for f in self.flows if f['name'] == '石灰石进厂质检工作流' and self.tree.exists(f['id'])), None)
        first = preferred or next(iter(self.tree.get_children()), None)
        if first:
            self.tree.selection_set(first)
            self.tree.see(first)
        self.invalidate()

    def select_all(self):
        if not self.busy:
            self.tree.selection_set(self.tree.get_children())

    def invalidate(self):
        if self.busy:
            return
        if self.batch and self.batch['status'] == 'preview':
            self.batch = None
            self.preview_tree.delete(*self.preview_tree.get_children())
            self.summary.set('参数已变更，请重新生成预览')
        selected = len(self.tree.selection())
        try:
            total = selected * int(self.count.get())
            self.selection_summary.set(f'已选 {selected} 条工作流 · 共 {total} 份数据' + ('（超过每批 50 份上限）' if total > 50 else ''))
        except ValueError:
            self.selection_summary.set('请输入有效生成数量')
        self.controls()

    def preview(self):
        try:
            flows = [f for f in self.flows if f['id'] in self.tree.selection()]
            count, ratio = int(self.count.get()), int(self.ratio.get())
            if not flows or not 1 <= count <= 20 or len(flows) * count > 50:
                raise DataError('请选择工作流，每条 1–20 份，每批合计最多 50 份')
            mode = MODES[self.mode.get()]
            options = {**self.options, 'supplement': self.supplement.get()}
        except (ValueError, DataError) as error:
            messagebox.showinfo('检查生成设置', str(error), parent=self.root)
            return
        self.batch = None
        self.run('正在读取实际标准并准备预览，尚未写入…',
                 lambda: prepare(self.credentials, flows, count, mode, ratio, options), self.prepared)

    def prepared(self, batch):
        self.batch = batch
        self.preview_tree.delete(*self.preview_tree.get_children())
        for index, row in enumerate(batch['preview']):
            self.preview_tree.insert('', 'end', iid=str(index), values=(row['workflow'], row['subject'], row['outcome'], row['rules']),
                                     tags=('pass' if row['passed'] else 'fail',))
        summary = batch['summary']
        self.summary.set(f"预览 {summary['records']} 份 · 合格/正常 {summary['passed']} · 不合格/异常 {summary['failed']} · 写入 {summary['rows']} 行")
        self.status.set('预览完成 · 尚未写入 · 点击“写入本地数据库”才会新增')
        self.detail('\n'.join(batch['warnings']) or '已按现有标准生成预览。采集时间将在写入时设为当前时间。\n预期结果针对本批记录，工作流也可能同时检查其他历史任务。')

    def show_details(self, event=None):
        if self.batch and self.preview_tree.selection():
            row = self.batch['preview'][int(self.preview_tree.selection()[0])]
            self.detail(f"{row['workflow']}\n{row['subject']} · {row['outcome']} · {row['rules']}\n{row['detail']}")

    def write(self):
        if not self.batch or self.batch['status'] != 'preview':
            return
        batch = self.batch
        self.run('正在写入本批测试数据，请等待完成…', lambda: write_batch(self.credentials, batch), self.written)

    def written(self, receipt):
        self.status.set('写入成功 · ' + self.batch['id'])
        self.detail(f"已于 {receipt['writeTime']} 写入 {receipt['rows']} 行测试数据。\n采集时间：{receipt['collectionTime']}，后续定时工作流按时间窗口获取。\n批次：{self.batch['id']}\n需要清理时，进入“历史批次 / 撤销”。")

    def open_workflow(self):
        selected = self.tree.selection()
        if selected:
            webbrowser.open('http://127.0.0.1:8011/workflows/' + selected[0])

    def edit_options(self):
        window = tk.Toplevel(self.root)
        window.title('缺少原始依据时使用的测试参数')
        window.transient(self.root)
        window.grab_set()
        frame = ttk.Frame(window, padding=20)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='仅用于新增测试单据 / 测试计划，不改原模板。', style='Title.TLabel').grid(row=0, column=0, columnspan=2, pady=(0, 14))
        fields = [('gasVolume', '发气量下限（L/kg）'), ('phosphine', '磷化氢上限（%）'), ('sulfide', '硫化氢上限（%）'),
                  ('inspectionHours', '巡检周期（小时：2/4/8/12/24/168）'), ('maintenanceDays', '润滑点周期（天）'),
                  ('oil', '测试油品'), ('amount', '测试用量（例如 1kg）'), ('method', '测试保养方式')]
        values = {}
        for index, (key, label) in enumerate(fields, 1):
            values[key] = tk.StringVar(value=self.options[key])
            ttk.Label(frame, text=label).grid(row=index, column=0, sticky='w', pady=7, padx=(0, 14))
            ttk.Entry(frame, textvariable=values[key], width=26).grid(row=index, column=1, sticky='ew', pady=7)
        ttk.Label(frame, text='新增保养计划的总周期为每周，润滑点周期按上方设置。', style='Muted.TLabel').grid(row=9, column=0, columnspan=2, pady=12)
        def save():
            self.options.update({key: value.get().strip() for key, value in values.items()})
            self.invalidate()
            window.destroy()
        ttk.Button(frame, text='保存测试参数', style='Primary.TButton', command=save).grid(row=10, column=1, sticky='e')

    def history(self):
        window = tk.Toplevel(self.root)
        window.title('本工具生成的测试批次')
        window.geometry('850x460')
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='撤销只删除本批新增记录，已保存的工作流报告仍会保留。', style='Muted.TLabel').pack(anchor='w', pady=(0, 12))
        tree = ttk.Treeview(frame, columns=('batch', 'status', 'records'), show='headings')
        for key, label, width in [('batch', '批次', 490), ('status', '状态', 150), ('records', '单据/任务数', 100)]:
            tree.heading(key, text=label)
            tree.column(key, width=width)
        tree.pack(fill='both', expand=True)
        batches = {}
        names = {'written': '已写入', 'undone': '已撤销', 'writing': '需核对写入状态', 'unconfirmed': '需核对写入状态'}
        for path in sorted(BATCHES.glob('GUI-*.json'), reverse=True)[:100]:
            try:
                batch = json.loads(path.read_text('utf-8'))
                batches[batch['id']] = batch
                tree.insert('', 'end', iid=batch['id'], values=(batch['id'], names.get(batch['status'], batch['status']), batch['summary']['records']))
            except (ValueError, KeyError):
                continue
        def undo():
            if not tree.selection():
                return
            batch = batches[tree.selection()[0]]
            if batch['status'] == 'undone':
                return
            if messagebox.askyesno('撤销选中测试批次', f"删除 {batch['id']} 新增的 {len(batch['rows'])} 行测试数据？\n已有业务记录和运行报告保持原样。", parent=window):
                window.destroy()
                self.run('正在撤销本批新增测试数据…', lambda: undo_batch(self.credentials, batch),
                         lambda _: self.status.set('已撤销测试批次 · ' + batch['id']))
        ttk.Button(frame, text='撤销选中批次', command=undo).pack(side='right', pady=(12, 0))
        ttk.Button(frame, text='打开批次记录目录', command=lambda: (BATCHES.mkdir(parents=True, exist_ok=True), os.startfile(BATCHES))).pack(side='left', pady=(12, 0))

    def close(self):
        if self.busy:
            messagebox.showinfo('操作进行中', '请等待本次操作结束后再关闭窗口。', parent=self.root)
            return
        self.root.destroy()
