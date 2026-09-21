#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
费用分类汇总系统图形用户界面

功能：提供直观的文件目录选择功能，允许用户选择数据源文件所在的目录和输出文件目录，
      将费用分类汇总报告自动保存到用户指定的目录中。

线程模型：处理管线在后台线程运行（统一调用引擎 run_pipeline），
worker 只向 queue 投递消息，所有 tkinter 控件更新均由主线程 after 轮询完成，
杜绝子线程直接操作 UI（含 root.update()/messagebox）导致的 Tcl 崩溃。
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import sys
import queue
import threading
from datetime import datetime

# 添加当前目录到系统路径，以便导入expense_classification模块
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from expense_classification import ExpenseClassifier, find_data_files, _cfg

class ExpenseClassifierGUI:
    def __init__(self, root):
        """初始化GUI

        Args:
            root: 主窗口
        """
        self.root = root
        self.root.title("费用分类汇总系统")
        self.root.geometry("800x600")
        self.root.resizable(True, True)

        # 设置窗口图标（如果有）
        # self.root.iconbitmap("icon.ico")

        # 初始化变量
        self.source_directory = tk.StringVar()
        self.output_directory = tk.StringVar()
        self.voucher_file = None
        self.bank_file = None
        self.processing = False
        self.ui_queue = queue.Queue()

        # 创建界面控件
        self.create_widgets()

    def create_widgets(self):
        """创建界面控件"""
        # 设置窗口最小尺寸
        self.root.minsize(600, 500)

        # 主框架
        main_frame = ttk.Frame(self.root, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 标题
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(fill=tk.X, pady=(0, 20))

        title_label = ttk.Label(title_frame, text="费用分类汇总系统", font=("微软雅黑", 20, "bold"))
        title_label.pack(anchor=tk.CENTER)

        subtitle_label = ttk.Label(title_frame, text="专业的财务费用分类与汇总工具", font=("微软雅黑", 10))
        subtitle_label.pack(anchor=tk.CENTER, pady=(8, 0))

        # 创建滚动条框架
        canvas = tk.Canvas(main_frame)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(main_frame, orient=tk.VERTICAL, command=canvas.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        canvas.configure(yscrollcommand=scrollbar.set)

        # 鼠标滚轮滚动（Windows：delta 为 120 的倍数）
        canvas.bind_all('<MouseWheel>',
                        lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), 'units'))

        # 内容框架
        content_frame = ttk.Frame(canvas)

        # 创建窗口并绑定配置事件
        canvas.create_window((0, 0), window=content_frame, anchor=tk.NW, tags='content')

        # 绑定画布配置事件，确保内容框架宽度自适应
        def on_canvas_configure(event):
            canvas.itemconfig('content', width=event.width - 2)  # 减去2像素的边距
            canvas.configure(scrollregion=canvas.bbox('all'))

        canvas.bind('<Configure>', on_canvas_configure)

        # 绑定内容框架配置事件，确保滚动区域正确更新
        def on_content_configure(event):
            canvas.configure(scrollregion=canvas.bbox('all'))

        content_frame.bind('<Configure>', on_content_configure)

        # 数据源选择
        source_frame = ttk.LabelFrame(content_frame, text="数据源设置", padding="15")
        source_frame.pack(fill=tk.X, pady=(0, 15))

        # 数据源目录选择
        source_dir_frame = ttk.Frame(source_frame)
        source_dir_frame.pack(fill=tk.X, pady=(5, 10))

        source_dir_label = ttk.Label(source_dir_frame, text="数据源目录:", width=12)
        source_dir_label.pack(side=tk.LEFT, padx=(0, 10))

        source_dir_entry = ttk.Entry(source_dir_frame, textvariable=self.source_directory)
        source_dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        source_dir_button = ttk.Button(source_dir_frame, text="浏览", command=self.select_source_directory, width=8)
        source_dir_button.pack(side=tk.RIGHT)

        # 输出目录选择
        output_dir_frame = ttk.Frame(source_frame)
        output_dir_frame.pack(fill=tk.X, pady=(5, 10))

        output_dir_label = ttk.Label(output_dir_frame, text="输出目录:", width=12)
        output_dir_label.pack(side=tk.LEFT, padx=(0, 10))

        output_dir_entry = ttk.Entry(output_dir_frame, textvariable=self.output_directory)
        output_dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        output_dir_button = ttk.Button(output_dir_frame, text="浏览", command=self.select_output_directory, width=8)
        output_dir_button.pack(side=tk.RIGHT)

        # 扫描按钮
        scan_button = ttk.Button(source_frame, text="扫描文件", command=self.scan_files, width=12)
        scan_button.pack(anchor=tk.CENTER, pady=(10, 0))

        # 文件识别结果
        file_result_frame = ttk.LabelFrame(content_frame, text="文件识别结果", padding="15")
        file_result_frame.pack(fill=tk.X, pady=(0, 15))

        # 凭证列表文件
        voucher_file_frame = ttk.Frame(file_result_frame)
        voucher_file_frame.pack(fill=tk.X, pady=(5, 8))

        voucher_file_label = ttk.Label(voucher_file_frame, text="凭证列表:", width=12)
        voucher_file_label.pack(side=tk.LEFT, padx=(0, 10))

        self.voucher_file_var = tk.StringVar(value="未识别")
        voucher_file_entry = ttk.Entry(voucher_file_frame, textvariable=self.voucher_file_var, state="readonly")
        voucher_file_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 银行存款辅助明细账文件
        bank_file_frame = ttk.Frame(file_result_frame)
        bank_file_frame.pack(fill=tk.X, pady=(5, 8))

        bank_file_label = ttk.Label(bank_file_frame, text="银行存款辅助明细账:", width=12)
        bank_file_label.pack(side=tk.LEFT, padx=(0, 10))

        self.bank_file_var = tk.StringVar(value="未识别")
        bank_file_entry = ttk.Entry(bank_file_frame, textvariable=self.bank_file_var, state="readonly")
        bank_file_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 处理区域
        process_frame = ttk.LabelFrame(content_frame, text="处理控制", padding="15")
        process_frame.pack(fill=tk.X, pady=(0, 15))

        # 进度条
        self.progress_var = tk.DoubleVar()
        progress_bar = ttk.Progressbar(process_frame, variable=self.progress_var, maximum=100)
        progress_bar.pack(fill=tk.X, pady=(5, 10))

        # 状态标签
        self.status_var = tk.StringVar(value="就绪")
        status_label = ttk.Label(process_frame, textvariable=self.status_var, font=("微软雅黑", 9))
        status_label.pack(anchor=tk.CENTER, pady=(0, 10))

        # 操作按钮
        button_frame = ttk.Frame(process_frame)
        button_frame.pack(fill=tk.X)

        # 按钮容器，确保按钮居中显示
        button_container = ttk.Frame(button_frame)
        button_container.pack(anchor=tk.CENTER)

        start_button = ttk.Button(button_container, text="开始处理", command=self.start_processing, width=12)
        start_button.pack(side=tk.LEFT, padx=(0, 15))

        cancel_button = ttk.Button(button_container, text="取消", command=self.cancel_processing, width=12)
        cancel_button.pack(side=tk.LEFT)

        # 底部信息
        footer_frame = ttk.Frame(content_frame)
        footer_frame.pack(fill=tk.X, pady=(15, 0))

        version_label = ttk.Label(footer_frame, text="版本 2.2.0", font=("微软雅黑", 8))
        version_label.pack(side=tk.LEFT)

        copyright_label = ttk.Label(footer_frame, text="© 2026 财务系统", font=("微软雅黑", 8))
        copyright_label.pack(side=tk.RIGHT)

    def select_source_directory(self):
        """选择数据源目录"""
        directory = filedialog.askdirectory(title="选择数据源目录")
        if directory:
            self.source_directory.set(directory)
            self.voucher_file = None
            self.bank_file = None
            self.voucher_file_var.set("未识别")
            self.bank_file_var.set("未识别")
            self.status_var.set("已更新数据源目录，请点击扫描文件")

    def select_output_directory(self):
        """选择输出目录"""
        directory = filedialog.askdirectory(title="选择输出目录")
        if directory:
            self.output_directory.set(directory)
            self.status_var.set("已更新输出目录")

    def scan_files(self):
        """扫描文件，识别凭证列表和银行存款辅助明细账"""
        source_dir = self.source_directory.get()
        if not source_dir:
            messagebox.showwarning("警告", "请先选择数据源目录")
            return

        if not os.path.exists(source_dir):
            messagebox.showwarning("警告", "所选数据源目录不存在")
            return

        try:
            self.status_var.set("正在扫描文件...")

            # 识别凭证列表和银行存款辅助明细账（统一逻辑，与 CLI 共用 find_data_files）
            voucher_file, bank_file = find_data_files(source_dir)

            # 更新识别结果
            if voucher_file:
                self.voucher_file = voucher_file
                self.voucher_file_var.set(os.path.basename(voucher_file))
            else:
                self.voucher_file = None
                self.voucher_file_var.set("未识别")

            if bank_file:
                self.bank_file = bank_file
                self.bank_file_var.set(os.path.basename(bank_file))
            else:
                self.bank_file = None
                self.bank_file_var.set("未识别")

            # 检查识别结果
            if not voucher_file and not bank_file:
                messagebox.showinfo("信息", "未识别到凭证列表或银行存款辅助明细账文件")
                self.status_var.set("就绪")
            else:
                message = "文件扫描完成"
                if voucher_file:
                    message += f"\n已识别凭证列表: {os.path.basename(voucher_file)}"
                if bank_file:
                    message += f"\n已识别银行存款辅助明细账: {os.path.basename(bank_file)}"
                self.status_var.set("文件扫描完成，就绪")
                messagebox.showinfo("信息", message)

        except Exception as e:
            messagebox.showerror("错误", f"扫描文件时出错: {str(e)}")
            self.status_var.set("就绪")

    @staticmethod
    def _is_dir_writable(path):
        """校验目录存在且可写"""
        if not os.path.isdir(path):
            return False
        try:
            probe = os.path.join(path, f".write_probe_{os.getpid()}")
            with open(probe, 'w'):
                pass
            os.remove(probe)
            return True
        except OSError:
            return False

    def _build_output_path(self):
        """按 OUTPUT_CONFIG.filename_format 生成输出路径，同秒重跑自动追加序号防覆盖"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename_format = _cfg('OUTPUT_CONFIG.filename_format', '费用分类汇总报告_v2_{timestamp}.xlsx')
        output_file = os.path.join(self.output_directory.get(), filename_format.format(timestamp=timestamp))
        base, ext = os.path.splitext(output_file)
        seq = 1
        while os.path.exists(output_file):
            output_file = f"{base}_{seq}{ext}"
            seq += 1
        return output_file

    def start_processing(self):
        """开始处理"""
        # 检查必要条件
        if not self.source_directory.get():
            messagebox.showwarning("警告", "请选择数据源目录")
            return

        if not self.output_directory.get():
            messagebox.showwarning("警告", "请选择输出目录")
            return

        if not self._is_dir_writable(self.output_directory.get()):
            messagebox.showwarning("警告", "输出目录不存在或不可写，请重新选择")
            return

        if not self.voucher_file and not self.bank_file:
            messagebox.showwarning("警告", "未识别到数据源文件")
            return

        if self.processing:
            messagebox.showinfo("信息", "处理已在进行中")
            return

        # 开始处理（在新线程中）
        self.processing = True
        self.status_var.set("开始处理...")
        self.progress_var.set(0)

        # 创建并启动处理线程
        process_thread = threading.Thread(target=self.process_data)
        process_thread.daemon = True
        process_thread.start()

        # 主线程轮询 worker 消息队列
        self.root.after(100, self._poll_ui_queue)

    def process_data(self):
        """处理数据（worker 线程）：只跑管线并向队列投递消息，不触碰任何 UI 控件"""
        try:
            classifier = ExpenseClassifier()
            output_file = self._build_output_path()

            # 统一调用引擎管线（与 CLI 同一条路径），进度/取消通过回调桥接
            result_file = classifier.run_pipeline(
                self.voucher_file,
                self.bank_file,
                output_file,
                progress_callback=lambda step, pct: self.ui_queue.put(('progress', step, pct)),
                cancel_check=lambda: not self.processing,
            )

            if not self.processing:
                self.ui_queue.put(('cancelled',))
            elif result_file:
                self.ui_queue.put(('done', result_file))
            else:
                self.ui_queue.put(('failed',))
        except Exception as e:
            self.ui_queue.put(('error', str(e)))

    def _poll_ui_queue(self):
        """主线程消费 worker 消息：所有控件更新/messagebox 只在这里发生"""
        terminal = False
        try:
            while True:
                msg = self.ui_queue.get_nowait()
                kind = msg[0]
                if kind == 'progress':
                    _, step, pct = msg
                    self.status_var.set(step)
                    self.progress_var.set(pct)
                elif kind == 'done':
                    self.status_var.set("处理完成")
                    self.progress_var.set(100)
                    messagebox.showinfo("成功", f"处理完成！\n报告已保存至: {msg[1]}")
                    terminal = True
                elif kind == 'failed':
                    self.status_var.set("处理失败")
                    messagebox.showwarning("警告", "处理完成，但导出报告失败！请查看日志 expense_classification.log")
                    terminal = True
                elif kind == 'cancelled':
                    self.status_var.set("已取消")
                    self.progress_var.set(0)
                    messagebox.showinfo("信息", "处理已取消")
                    terminal = True
                elif kind == 'error':
                    self.status_var.set("处理出错")
                    messagebox.showerror("错误", f"处理数据时出错: {msg[1]}")
                    terminal = True
        except queue.Empty:
            pass

        if terminal:
            self.processing = False
            if self.status_var.get() not in ("已取消", "处理失败", "处理出错"):
                pass  # 保留"处理完成"状态
        elif self.processing:
            self.root.after(100, self._poll_ui_queue)
        else:
            # worker 尚未投递终止消息但用户已取消：继续轮询直到收到 cancelled
            self.root.after(100, self._poll_ui_queue)

    def cancel_processing(self):
        """取消处理：置标志位，worker 在下一阶段边界检查并中止（引擎 run_pipeline 的 cancel_check）"""
        if self.processing:
            self.processing = False
            self.status_var.set("正在取消...")
        else:
            messagebox.showinfo("信息", "没有正在进行的处理")

def main():
    """主函数"""
    root = tk.Tk()
    app = ExpenseClassifierGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
