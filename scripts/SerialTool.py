#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
串口调试工具
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
import serial
import serial.tools.list_ports
import threading
import queue
import json
import time
from pathlib import Path
from collections import deque
from datetime import datetime
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict


# ============================================================
# 配置管理
# ============================================================

@dataclass
class SerialConfig:
    """串口配置"""
    port: str = ""
    baudrate: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1
    timeout: float = 0.1
    hex_receive: bool = False
    hex_send: bool = False
    auto_scroll: bool = True
    max_lines: int = 1000
    show_timestamp: bool = True
    save_log: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SerialConfig':
        return cls(**{k: v for k, v in data.items() if k in cls.__annotations__})


class ConfigManager:
    """配置文件管理器"""

    def __init__(self, config_file: str = "serial_tool_config.json"):
        self.config_file = Path(config_file)
        self.config = SerialConfig()

    def load(self) -> SerialConfig:
        """加载配置"""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.config = SerialConfig.from_dict(data)
            except Exception:
                pass
        return self.config

    def save(self, config: SerialConfig) -> None:
        """保存配置"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config.to_dict(), f, indent=2)
        except Exception as e:
            print(f"Failed to save config: {e}")


# ============================================================
# 统计信息
# ============================================================

@dataclass
class Statistics:
    """串口统计信息"""
    bytes_received: int = 0
    bytes_sent: int = 0
    packets_received: int = 0
    packets_sent: int = 0
    errors: int = 0
    start_time: float = 0

    def reset(self) -> None:
        """重置统计"""
        self.bytes_received = 0
        self.bytes_sent = 0
        self.packets_received = 0
        self.packets_sent = 0
        self.errors = 0
        self.start_time = time.time()

    def get_rate(self) -> tuple:
        """获取收发速率 (bytes/sec)"""
        elapsed = time.time() - self.start_time
        if elapsed == 0:
            return (0, 0)
        rx_rate = self.bytes_received / elapsed
        tx_rate = self.bytes_sent / elapsed
        return (rx_rate, tx_rate)


# ============================================================
# 主应用类
# ============================================================

class SerialToolOptimized:
    """串口调试工具 - 优化版"""

    def __init__(self, master: tk.Tk):
        self.master = master
        master.title("串口调试工具")
        master.geometry("900x700")

        # 核心状态
        self.running = False
        self.serial_port: Optional[serial.Serial] = None
        self.receive_thread: Optional[threading.Thread] = None
        self.send_thread: Optional[threading.Thread] = None

        # 队列和缓冲
        self.send_queue = queue.Queue()
        self.ui_update_queue = deque()

        # 配置和统计
        self.config_manager = ConfigManager()
        self.config = self.config_manager.load()
        self.stats = Statistics()

        # 日志文件
        self.log_file: Optional[Path] = None

        # 发送历史
        self.send_history = deque(maxlen=50)
        self.history_index = -1

        # UI 更新间隔
        self.UI_UPDATE_INTERVAL = 50  # ms

        # 构建界面
        self._build_ui()
        self._apply_config()
        self._start_stats_update()

    # ========================================
    # UI 构建
    # ========================================

    def _build_ui(self) -> None:
        """构建用户界面"""
        # 创建菜单栏
        self._build_menubar()

        # 主容器
        main_frame = ttk.Frame(self.master)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 左侧面板（配置 + 统计）
        left_frame = ttk.Frame(main_frame, width=250)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))

        # 右侧面板（接收 + 发送）
        right_frame = ttk.Frame(main_frame)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 构建各部分
        self._build_config_frame(left_frame)
        self._build_stats_frame(left_frame)
        self._build_receive_frame(right_frame)
        self._build_send_frame(right_frame)
        self._build_status_bar()

    def _build_menubar(self) -> None:
        """构建菜单栏"""
        menubar = tk.Menu(self.master)
        self.master.config(menu=menubar)

        # 文件菜单
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件", menu=file_menu)
        file_menu.add_command(label="保存日志", command=self.save_log)
        file_menu.add_command(label="导出配置", command=self.export_config)
        file_menu.add_command(label="导入配置", command=self.import_config)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.on_closing)

        # 工具菜单
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="工具", menu=tools_menu)
        tools_menu.add_command(label="清空接收区", command=self.clear_receive)
        tools_menu.add_command(label="重置统计", command=self.reset_stats)
        tools_menu.add_separator()
        tools_menu.add_command(label="ASCII 表", command=self.show_ascii_table)

        # 帮助菜单
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="帮助", menu=help_menu)
        help_menu.add_command(label="使用说明", command=self.show_help)
        help_menu.add_command(label="关于", command=self.show_about)

    def _build_config_frame(self, parent: ttk.Frame) -> None:
        """构建配置面板"""
        config_frame = ttk.LabelFrame(parent, text="串口配置")
        config_frame.pack(fill=tk.X, pady=(0, 10))

        # 串口号
        ttk.Label(config_frame, text="串口号:").grid(row=0, column=0, sticky="w", padx=5, pady=3)
        self.port_combobox = ttk.Combobox(config_frame, state="readonly", width=15)
        self.port_combobox.grid(row=0, column=1, padx=5, pady=3)

        ttk.Button(config_frame, text="刷新", command=self.refresh_ports, width=8).grid(
            row=0, column=2, padx=5, pady=3
        )

        # 波特率
        ttk.Label(config_frame, text="波特率:").grid(row=1, column=0, sticky="w", padx=5, pady=3)
        self.baud_combobox = ttk.Combobox(
            config_frame,
            values=["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"],
            state="readonly",
            width=15
        )
        self.baud_combobox.set("115200")
        self.baud_combobox.grid(row=1, column=1, columnspan=2, padx=5, pady=3, sticky="ew")

        # 数据位
        ttk.Label(config_frame, text="数据位:").grid(row=2, column=0, sticky="w", padx=5, pady=3)
        self.bytesize_combobox = ttk.Combobox(
            config_frame, values=["5", "6", "7", "8"], state="readonly", width=15
        )
        self.bytesize_combobox.set("8")
        self.bytesize_combobox.grid(row=2, column=1, columnspan=2, padx=5, pady=3, sticky="ew")

        # 校验位
        ttk.Label(config_frame, text="校验位:").grid(row=3, column=0, sticky="w", padx=5, pady=3)
        self.parity_combobox = ttk.Combobox(
            config_frame, values=["None", "Even", "Odd", "Mark", "Space"], state="readonly", width=15
        )
        self.parity_combobox.set("None")
        self.parity_combobox.grid(row=3, column=1, columnspan=2, padx=5, pady=3, sticky="ew")

        # 停止位
        ttk.Label(config_frame, text="停止位:").grid(row=4, column=0, sticky="w", padx=5, pady=3)
        self.stopbits_combobox = ttk.Combobox(
            config_frame, values=["1", "1.5", "2"], state="readonly", width=15
        )
        self.stopbits_combobox.set("1")
        self.stopbits_combobox.grid(row=4, column=1, columnspan=2, padx=5, pady=3, sticky="ew")

        # 打开/关闭按钮
        self.open_button = ttk.Button(
            config_frame, text="打开串口", command=self.toggle_port
        )
        self.open_button.grid(row=5, column=0, columnspan=3, padx=5, pady=10, sticky="ew")

        # 选项
        options_frame = ttk.LabelFrame(parent, text="选项")
        options_frame.pack(fill=tk.X, pady=(0, 10))

        self.timestamp_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="显示时间戳", variable=self.timestamp_var).pack(
            anchor="w", padx=5, pady=2
        )

        self.autoscroll_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="自动滚动", variable=self.autoscroll_var).pack(
            anchor="w", padx=5, pady=2
        )

        self.save_log_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            options_frame, text="自动保存日志", variable=self.save_log_var,
            command=self.toggle_log_save
        ).pack(anchor="w", padx=5, pady=2)

        self.refresh_ports()

    def _build_stats_frame(self, parent: ttk.Frame) -> None:
        """构建统计面板"""
        stats_frame = ttk.LabelFrame(parent, text="统计信息")
        stats_frame.pack(fill=tk.BOTH, expand=True)

        # 统计标签
        self.stats_text = tk.Text(stats_frame, height=12, width=25, state=tk.DISABLED, font=("Consolas", 9))
        self.stats_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _build_receive_frame(self, parent: ttk.Frame) -> None:
        """构建接收区"""
        receive_frame = ttk.LabelFrame(parent, text="接收区")
        receive_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        # 文本框
        self.receive_text = scrolledtext.ScrolledText(
            receive_frame, wrap=tk.WORD, font=("Consolas", 10), state=tk.DISABLED
        )
        self.receive_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 配置标签颜色
        self.receive_text.tag_config("info", foreground="blue", font=("Consolas", 9, "bold"))
        self.receive_text.tag_config("error", foreground="red", font=("Consolas", 9, "bold"))
        self.receive_text.tag_config("sent", foreground="green", font=("Consolas", 9))
        self.receive_text.tag_config("timestamp", foreground="gray", font=("Consolas", 8))

        # 控制栏
        control_frame = ttk.Frame(receive_frame)
        control_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

        self.hex_receive_var = tk.BooleanVar()
        ttk.Checkbutton(control_frame, text="Hex显示", variable=self.hex_receive_var).pack(side=tk.LEFT, padx=5)

        ttk.Button(control_frame, text="清空", command=self.clear_receive).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="保存", command=self.save_log).pack(side=tk.LEFT, padx=5)

    def _build_send_frame(self, parent: ttk.Frame) -> None:
        """构建发送区"""
        send_frame = ttk.LabelFrame(parent, text="发送区")
        send_frame.pack(fill=tk.X)

        # 输入框
        input_frame = ttk.Frame(send_frame)
        input_frame.pack(fill=tk.X, padx=5, pady=5)

        self.send_text = ttk.Entry(input_frame, font=("Consolas", 10))
        self.send_text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.send_text.bind("<Return>", lambda e: self.send_data())
        self.send_text.bind("<Up>", self.history_up)
        self.send_text.bind("<Down>", self.history_down)

        ttk.Button(input_frame, text="发送", command=self.send_data, width=10).pack(side=tk.LEFT)

        # 选项
        option_frame = ttk.Frame(send_frame)
        option_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

        self.hex_send_var = tk.BooleanVar()
        ttk.Checkbutton(option_frame, text="Hex发送", variable=self.hex_send_var).pack(side=tk.LEFT, padx=5)

        self.append_newline_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(option_frame, text="追加换行", variable=self.append_newline_var).pack(side=tk.LEFT, padx=5)

    def _build_status_bar(self) -> None:
        """构建状态栏"""
        self.status_bar = ttk.Label(
            self.master, text="就绪", relief=tk.SUNKEN, anchor=tk.W
        )
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    # ========================================
    # 配置应用和保存
    # ========================================

    def _apply_config(self) -> None:
        """应用配置"""
        if self.config.port:
            self.port_combobox.set(self.config.port)
        self.baud_combobox.set(str(self.config.baudrate))
        self.bytesize_combobox.set(str(self.config.bytesize))

        parity_map = {"N": "None", "E": "Even", "O": "Odd", "M": "Mark", "S": "Space"}
        self.parity_combobox.set(parity_map.get(self.config.parity, "None"))

        self.stopbits_combobox.set(str(self.config.stopbits))
        self.hex_receive_var.set(self.config.hex_receive)
        self.hex_send_var.set(self.config.hex_send)
        self.autoscroll_var.set(self.config.auto_scroll)
        self.timestamp_var.set(self.config.show_timestamp)
        self.save_log_var.set(self.config.save_log)

    def _save_config(self) -> None:
        """保存当前配置"""
        parity_map = {"None": "N", "Even": "E", "Odd": "O", "Mark": "M", "Space": "S"}

        self.config.port = self.port_combobox.get()
        self.config.baudrate = int(self.baud_combobox.get())
        self.config.bytesize = int(self.bytesize_combobox.get())
        self.config.parity = parity_map.get(self.parity_combobox.get(), "N")
        self.config.stopbits = float(self.stopbits_combobox.get())
        self.config.hex_receive = self.hex_receive_var.get()
        self.config.hex_send = self.hex_send_var.get()
        self.config.auto_scroll = self.autoscroll_var.get()
        self.config.show_timestamp = self.timestamp_var.get()
        self.config.save_log = self.save_log_var.get()

        self.config_manager.save(self.config)

    # ========================================
    # 串口操作
    # ========================================

    def toggle_port(self) -> None:
        """打开/关闭串口"""
        if self.running:
            self._close_port()
        else:
            self._open_port()

    def _open_port(self) -> None:
        """打开串口"""
        port = self.port_combobox.get()
        if not port:
            self.log_to_receiver("错误: 未选择串口\n", "error")
            return

        try:
            parity_map = {"None": "N", "Even": "E", "Odd": "O", "Mark": "M", "Space": "S"}

            self.serial_port = serial.Serial(
                port=port,
                baudrate=int(self.baud_combobox.get()),
                bytesize=int(self.bytesize_combobox.get()),
                parity=parity_map.get(self.parity_combobox.get(), "N"),
                stopbits=float(self.stopbits_combobox.get()),
                timeout=0.1
            )

            self.running = True
            self.stats.reset()

            # 启动线程
            self.receive_thread = threading.Thread(target=self._receive_worker, daemon=True)
            self.send_thread = threading.Thread(target=self._send_worker, daemon=True)
            self.receive_thread.start()
            self.send_thread.start()

            # 启动 UI 更新
            self.master.after(self.UI_UPDATE_INTERVAL, self._process_ui_updates)

            # 更新界面
            self.open_button.config(text="关闭串口")
            self.status_bar.config(text=f"串口 {port} 已打开")
            self.log_to_receiver(
                f"串口 {port} 已打开 ({self.baud_combobox.get()} bps, "
                f"{self.bytesize_combobox.get()}{self.parity_combobox.get()[0]}"
                f"{self.stopbits_combobox.get()})\n",
                "info"
            )

            # 保存配置
            self._save_config()

        except serial.SerialException as e:
            self.log_to_receiver(f"错误: {e}\n", "error")
            self.status_bar.config(text=f"打开失败: {e}")

    def _close_port(self) -> None:
        """关闭串口"""
        self.running = False
        self.send_queue.put('exit')

        # 等待线程结束
        if self.receive_thread:
            self.receive_thread.join(timeout=0.5)
        if self.send_thread:
            self.send_thread.join(timeout=0.5)

        # 关闭串口
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()

        # 关闭日志
        if self.log_file:
            self.log_file = None

        # 清空队列
        self.ui_update_queue.clear()

        # 更新界面
        self.open_button.config(text="打开串口")
        self.status_bar.config(text="串口已关闭")
        self.log_to_receiver("串口已关闭\n", "info")

    # ========================================
    # 数据收发
    # ========================================

    def _receive_worker(self) -> None:
        """接收线程"""
        while self.running:
            try:
                if self.serial_port and self.serial_port.in_waiting:
                    data = self.serial_port.read(self.serial_port.in_waiting or 1)
                    if data:
                        self.ui_update_queue.append(('rx', data))
                        self.stats.bytes_received += len(data)
                        self.stats.packets_received += 1
                else:
                    time.sleep(0.01)
            except serial.SerialException as e:
                if self.running:
                    self.ui_update_queue.append(('error', f"接收错误: {e}\n"))
                    self.stats.errors += 1
                break
            except Exception as e:
                if self.running:
                    self.ui_update_queue.append(('error', f"未知错误: {e}\n"))
                    self.stats.errors += 1

    def _send_worker(self) -> None:
        """发送线程"""
        while self.running:
            try:
                data = self.send_queue.get(timeout=0.1)
                if data == 'exit':
                    break
                if self.serial_port and self.serial_port.is_open:
                    self.serial_port.write(data)
                    self.stats.bytes_sent += len(data)
                    self.stats.packets_sent += 1
            except queue.Empty:
                continue
            except Exception as e:
                if self.running:
                    self.ui_update_queue.append(('error', f"发送错误: {e}\n"))
                    self.stats.errors += 1

    def send_data(self) -> None:
        """发送数据"""
        if not self.running:
            self.log_to_receiver("错误: 串口未打开\n", "error")
            return

        data_str = self.send_text.get().strip()
        if not data_str:
            return

        try:
            # 解析数据
            if self.hex_send_var.get():
                data = bytes.fromhex(data_str.replace(" ", ""))
                display_data = ' '.join(f'{b:02X}' for b in data)
            else:
                data = data_str.encode('utf-8')
                if self.append_newline_var.get():
                    data += b'\r\n'
                display_data = data_str

            # 发送
            self.send_queue.put(data)

            # 显示
            if self.timestamp_var.get():
                timestamp = datetime.now().strftime("[%H:%M:%S.%f")[:-3] + "] "
                self.log_to_receiver(timestamp, "timestamp")
            self.log_to_receiver(f">>> {display_data}\n", "sent")

            # 保存历史
            self.send_history.append(data_str)
            self.history_index = -1

            # 清空输入框
            self.send_text.delete(0, tk.END)

        except ValueError as e:
            self.log_to_receiver(f"错误: Hex格式不正确 - {e}\n", "error")

    # ========================================
    # UI 更新
    # ========================================

    def _process_ui_updates(self) -> None:
        """处理 UI 更新队列"""
        if not self.running and not self.ui_update_queue:
            return

        # 批量处理
        while self.ui_update_queue:
            msg_type, data = self.ui_update_queue.popleft()

            if msg_type == 'rx':
                self._display_received_data(data)
            elif msg_type == 'error':
                self.log_to_receiver(data, "error")

        if self.running:
            self.master.after(self.UI_UPDATE_INTERVAL, self._process_ui_updates)

    def _display_received_data(self, data: bytes) -> None:
        """显示接收到的数据"""
        # 时间戳
        if self.timestamp_var.get():
            timestamp = datetime.now().strftime("[%H:%M:%S.%f")[:-3] + "] "
            self.log_to_receiver(timestamp, "timestamp")

        # 格式化数据
        if self.hex_receive_var.get():
            formatted_data = ' '.join(f'{b:02X}' for b in data) + '\n'
        else:
            formatted_data = data.decode('utf-8', errors='replace')

        self.log_to_receiver(formatted_data)

        # 保存到日志
        if self.save_log_var.get() and self.log_file:
            try:
                with open(self.log_file, 'ab') as f:
                    f.write(data)
            except Exception:
                pass

    def log_to_receiver(self, message: str, tag: Optional[str] = None) -> None:
        """向接收区添加文本"""
        # 检查是否需要自动滚动
        scroll_at_bottom = (self.receive_text.yview()[1] == 1.0)

        # 临时启用编辑
        self.receive_text.config(state=tk.NORMAL)

        # 插入消息
        self.receive_text.insert(tk.END, message, tag)

        # 限制行数
        current_lines = int(self.receive_text.index('end-1c').split('.')[0])
        if current_lines > self.config.max_lines:
            lines_to_delete = current_lines - self.config.max_lines
            self.receive_text.delete('1.0', f'{lines_to_delete + 1}.0')

        # 自动滚动
        if scroll_at_bottom and self.autoscroll_var.get():
            self.receive_text.see(tk.END)

        # 重新禁用编辑
        self.receive_text.config(state=tk.DISABLED)

    def _start_stats_update(self) -> None:
        """启动统计更新"""
        self._update_stats()

    def _update_stats(self) -> None:
        """更新统计信息"""
        if self.running:
            rx_rate, tx_rate = self.stats.get_rate()
            elapsed = time.time() - self.stats.start_time

            stats_text = (
                f"运行时间: {int(elapsed)}s\n"
                f"\n"
                f"接收:\n"
                f"  字节数: {self.stats.bytes_received}\n"
                f"  包数: {self.stats.packets_received}\n"
                f"  速率: {rx_rate:.1f} B/s\n"
                f"\n"
                f"发送:\n"
                f"  字节数: {self.stats.bytes_sent}\n"
                f"  包数: {self.stats.packets_sent}\n"
                f"  速率: {tx_rate:.1f} B/s\n"
                f"\n"
                f"错误: {self.stats.errors}"
            )
        else:
            stats_text = "未连接"

        self.stats_text.config(state=tk.NORMAL)
        self.stats_text.delete(1.0, tk.END)
        self.stats_text.insert(1.0, stats_text)
        self.stats_text.config(state=tk.DISABLED)

        self.master.after(500, self._update_stats)

    # ========================================
    # 工具方法
    # ========================================

    def refresh_ports(self) -> None:
        """刷新串口列表"""
        ports = [port.device for port in serial.tools.list_ports.comports()]
        self.port_combobox['values'] = ports
        if ports:
            if self.config.port in ports:
                self.port_combobox.set(self.config.port)
            else:
                self.port_combobox.current(0)

    def clear_receive(self) -> None:
        """清空接收区"""
        self.receive_text.config(state=tk.NORMAL)
        self.receive_text.delete(1.0, tk.END)
        self.receive_text.config(state=tk.DISABLED)

    def reset_stats(self) -> None:
        """重置统计"""
        self.stats.reset()

    def save_log(self) -> None:
        """保存日志"""
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=f"serial_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        if filename:
            try:
                content = self.receive_text.get(1.0, tk.END)
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(content)
                messagebox.showinfo("成功", f"日志已保存到:\n{filename}")
            except Exception as e:
                messagebox.showerror("错误", f"保存失败:\n{e}")

    def toggle_log_save(self) -> None:
        """切换自动保存日志"""
        if self.save_log_var.get():
            filename = f"serial_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            self.log_file = Path(filename)
            self.log_to_receiver(f"开始自动保存日志到: {filename}\n", "info")
        else:
            self.log_file = None
            self.log_to_receiver("停止自动保存日志\n", "info")

    def history_up(self, event) -> None:
        """历史记录向上"""
        if not self.send_history:
            return
        if self.history_index < len(self.send_history) - 1:
            self.history_index += 1
            self.send_text.delete(0, tk.END)
            self.send_text.insert(0, self.send_history[-(self.history_index + 1)])

    def history_down(self, event) -> None:
        """历史记录向下"""
        if not self.send_history:
            return
        if self.history_index > 0:
            self.history_index -= 1
            self.send_text.delete(0, tk.END)
            self.send_text.insert(0, self.send_history[-(self.history_index + 1)])
        elif self.history_index == 0:
            self.history_index = -1
            self.send_text.delete(0, tk.END)

    def export_config(self) -> None:
        """导出配置"""
        filename = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if filename:
            self._save_config()
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(self.config.to_dict(), f, indent=2)
                messagebox.showinfo("成功", "配置已导出")
            except Exception as e:
                messagebox.showerror("错误", f"导出失败:\n{e}")

    def import_config(self) -> None:
        """导入配置"""
        filename = filedialog.askopenfilename(
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if filename:
            try:
                with open(filename, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.config = SerialConfig.from_dict(data)
                self._apply_config()
                messagebox.showinfo("成功", "配置已导入")
            except Exception as e:
                messagebox.showerror("错误", f"导入失败:\n{e}")

    def show_ascii_table(self) -> None:
        """显示 ASCII 表"""
        ascii_win = tk.Toplevel(self.master)
        ascii_win.title("ASCII 表")
        ascii_win.geometry("600x400")

        text = scrolledtext.ScrolledText(ascii_win, font=("Consolas", 9))
        text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 生成 ASCII 表
        ascii_table = "DEC  HEX  CHR  | DEC  HEX  CHR  | DEC  HEX  CHR\n"
        ascii_table += "-" * 50 + "\n"
        for i in range(0, 128, 3):
            line = ""
            for j in range(3):
                val = i + j
                if val < 128:
                    char = chr(val) if 32 <= val < 127 else f"<{val:02d}>"
                    line += f"{val:3d}  {val:02X}   {char:4s} | "
            ascii_table += line[:-2] + "\n"

        text.insert(1.0, ascii_table)
        text.config(state=tk.DISABLED)

    def show_help(self) -> None:
        """显示帮助"""
        help_text = """
串口调试工具 - 使用说明

快捷键:
  Enter        - 发送数据
  Up/Down      - 浏览发送历史

功能:
  • 支持多种波特率、数据位、校验位、停止位
  • Hex 收发
  • 时间戳显示
  • 自动滚动
  • 发送历史记录（最多50条）
  • 统计信息实时显示
  • 日志自动保存
  • 配置导入/导出

提示:
  • 发送框支持上下键浏览历史
  • 支持追加换行符（\\r\\n）
  • 接收区自动限制最大行数
        """
        messagebox.showinfo("使用说明", help_text.strip())

    def show_about(self) -> None:
        """显示关于"""
        messagebox.showinfo(
            "关于",
            "串口调试工具\n\n"
            "版本: 2.0\n"
            "作者: RaoYi\n"
            "日期: 2026-05-08"
        )

    def on_closing(self) -> None:
        """关闭窗口"""
        if self.running:
            if messagebox.askokcancel("退出", "串口正在运行，确定要退出吗？"):
                self._close_port()
                self.master.destroy()
        else:
            self.master.destroy()


# ============================================================
# 主程序
# ============================================================

if __name__ == "__main__":
    root = tk.Tk()
    app = SerialToolOptimized(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
