"""
主窗口 - 整合所有面板和功能
"""
import sys
import os
import subprocess
import platform
from threading import Thread
from typing import Optional

from PyQt5.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QSplitter, QMessageBox, QApplication, QScrollArea)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal

# 添加项目根目录到路径
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from gui.panels.connection_panel import ConnectionPanel
from gui.panels.control_panel import ControlPanel
from gui.panels.data_display_panel import DataDisplayPanel
from gui.panels.recording_panel import RecordingPanel

from core.sensor_manager import SensorManager
from core.data_recorder import DataRecorder
from core.ultrasonic_serial import UltrasonicSerial
from gui.widgets.log_widget import LogWidget
from controller.algorithms.threshold import ThresholdSettings
from dataclasses import asdict


class MainWindow(QMainWindow):
    """主窗口"""

    # 由后台线程发回主线程，避免 UI 被阻塞
    liuzhouli_zero_finished = pyqtSignal(bool, str)
    liuzhouli_zero_progress = pyqtSignal(str)
    disconnect_all_finished = pyqtSignal(bool, str)
    
    def __init__(self, config_path: str = "config/default.yaml"):
        super().__init__()
        
        self.config_path = config_path
        self._liuzhouli_zero_busy = False
        self._offline_workbench = None
        
        # 创建核心组件
        self.sensor_manager = SensorManager(config_path)
        self.data_recorder = DataRecorder()
        self.ultrasonic_serial = UltrasonicSerial(self)
        
        # 定时器（更新文件大小等统计信息）
        self.stats_timer = QTimer()
        self.stats_timer.timeout.connect(self._update_statistics)
        self.stats_timer.setInterval(1000)  # 1秒更新一次
        
        self._setup_ui()
        self._connect_signals()
        self.control_panel.set_controller_settings(asdict(self.sensor_manager.controller.settings))
        self.control_panel.update_controller_status(self.sensor_manager.controller.snapshot())
        
        self.setWindowTitle("传感器数据采集系统 v1.2 · 上位机控制器")
        self.setMinimumSize(1200, 800)
        self.resize(1400, 900)
    
    def _setup_ui(self):
        """设置UI"""
        # 创建中心部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 主布局
        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)
        
        # 创建左右分隔器
        horizontal_splitter = QSplitter(Qt.Horizontal)
        
        # === 左侧面板（保持不变）===
        left_widget = QWidget()
        left_layout = QVBoxLayout()
        left_widget.setLayout(left_layout)
        
        # 连接管理面板
        self.connection_panel = ConnectionPanel(self.sensor_manager.config)
        left_layout.addWidget(self.connection_panel)
        
        # 控制面板
        self.control_panel = ControlPanel()
        left_layout.addWidget(self.control_panel)
        
        # 采集控制面板
        self.recording_panel = RecordingPanel()
        left_layout.addWidget(self.recording_panel)
        
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setWidget(left_widget)
        left_scroll.setMinimumWidth(365)
        horizontal_splitter.addWidget(left_scroll)
        
        # === 右侧面板（数据显示 + 日志）===
        right_widget = QWidget()
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_widget.setLayout(right_layout)
        
        # 创建右侧上下分隔器
        right_splitter = QSplitter(Qt.Vertical)
        
        # 数据显示面板
        self.data_display_panel = DataDisplayPanel()
        display_scroll = QScrollArea()
        display_scroll.setWidgetResizable(True)
        display_scroll.setWidget(self.data_display_panel)
        right_splitter.addWidget(display_scroll)
        
        # 日志显示面板
        self.log_widget = LogWidget()
        right_splitter.addWidget(self.log_widget)
        
        # 设置数据显示和日志的比例（数据60%，日志40%）
        right_splitter.setStretchFactor(0, 6)
        right_splitter.setStretchFactor(1, 4)
        
        right_layout.addWidget(right_splitter)
        horizontal_splitter.addWidget(right_widget)
        
        # 设置左右分隔比例（左30%，右70%）
        horizontal_splitter.setStretchFactor(0, 3)
        horizontal_splitter.setStretchFactor(1, 7)
        
        main_layout.addWidget(horizontal_splitter)
        
        # 启动日志捕获
        self.log_widget.start_capture()
        
        # 输出欢迎信息
        print("=" * 60)
        print("[INFO] 传感器数据采集系统 v1.2 已启动；控制器默认停止")
        print("=" * 60)
    
    def _connect_signals(self):
        """连接信号和槽"""
        self.liuzhouli_zero_finished.connect(self._on_liuzhouli_zero_finished)
        self.liuzhouli_zero_progress.connect(self._on_liuzhouli_zero_progress)
        self.disconnect_all_finished.connect(self._on_disconnect_all_finished)

        # ========== 连接管理信号 ==========
        self.connection_panel.connect_sensor.connect(self._on_connect_sensor)
        self.connection_panel.disconnect_sensor.connect(self._on_disconnect_sensor)
        self.connection_panel.connect_all.connect(self._on_connect_all)
        self.connection_panel.disconnect_all.connect(self._on_disconnect_all)
        self.connection_panel.ultrasonic_widget.toggle_requested.connect(self._on_toggle_ultrasonic)
        self.ultrasonic_serial.connection_changed.connect(self._on_ultrasonic_connection_changed)
        self.ultrasonic_serial.sample_received.connect(self._on_ultrasonic_sample)
        self.ultrasonic_serial.data_timeout.connect(self._on_ultrasonic_data_timeout)
        self.ultrasonic_serial.error_occurred.connect(self._on_ultrasonic_error)

        # ========== 数据记录回调（高频直接调用，不通过信号） ==========
        self.sensor_manager.record_callback_nianfujiao = self.data_recorder.write_nianfujiao_data
        self.sensor_manager.record_callback_liuzhouli = self.data_recorder.write_liuzhouli_data
        self.sensor_manager.record_callback_nianfujiao_aux = self.data_recorder.write_nianfujiao_aux
        
        # ========== 传感器状态变化信号 ==========
        self.sensor_manager.connection_status_changed.connect(
            self._on_sensor_status_changed
        )
        self.sensor_manager.error_occurred.connect(self._on_sensor_error)
        
        # ========== 控制面板信号 ==========
        # 粘附脚
        self.control_panel.nianfujiao_reply.connect(
            self.sensor_manager.nianfujiao_reply
        )
        self.control_panel.nianfujiao_engage.connect(
            self.sensor_manager.nianfujiao_engage
        )
        self.control_panel.nianfujiao_zero.connect(
            self.sensor_manager.nianfujiao_zero
        )
        self.control_panel.controller_apply.connect(self._on_controller_apply)
        self.control_panel.controller_start.connect(self._on_controller_start)
        self.control_panel.controller_stop.connect(self._on_controller_stop)
        self.control_panel.controller_reload_calibration.connect(self._on_controller_reload)
        self.sensor_manager.controller_updated.connect(self.control_panel.update_controller_status)
        
        # 六轴力
        self.control_panel.liuzhouli_zero.connect(
            self._on_liuzhouli_zero_requested
        )
        
        # ========== 数据更新信号 ==========
        self.sensor_manager.nianfujiao_data_updated.connect(
            self._on_nianfujiao_data_updated
        )
        self.sensor_manager.liuzhouli_data_updated.connect(
            self._on_liuzhouli_data_updated
        )
        self.sensor_manager.nianfujiao_protocol_updated.connect(
            self.data_display_panel.update_nianfujiao_protocol
        )
        
        # ========== 采集控制信号 ==========
        self.recording_panel.start_recording.connect(self._on_start_recording)
        self.recording_panel.stop_recording.connect(self._on_stop_recording)
        self.recording_panel.open_folder.connect(self._on_open_folder)
        self.recording_panel.offline_requested.connect(self._on_show_offline)
    
    # ==================== 连接管理槽函数 ====================

    def _offline_idle_check(self):
        if self.data_recorder.is_recording:
            return '请先停止数据采集，再运行离线分析或应用系数。'
        if self.sensor_manager.controller.snapshot()['active']:
            return '请先停止控制器，再运行离线分析或应用系数。'
        return None

    def _on_show_offline(self):
        if self._offline_workbench is None:
            try:
                from controller.visualization.offline_workbench import OfflineWorkbench
                self._offline_workbench = OfflineWorkbench(
                    self, current_session=self.data_recorder.get_session_path(),
                    reload_callback=self.sensor_manager.controller.reload_calibration,
                    idle_check=self._offline_idle_check)
                self._offline_workbench.coefficients_applied.connect(
                    lambda:self.control_panel.update_controller_status(
                        self.sensor_manager.controller.snapshot()))
            except Exception as exc:
                QMessageBox.warning(self, '离线工具', f'窗口未能打开：{exc}\n请检查 requirements.txt 中的依赖。')
                return
        self._offline_workbench.show()
        self._offline_workbench.raise_()
        self._offline_workbench.activateWindow()

    def _on_controller_apply(self, values, arm=False):
        try:
            settings = ThresholdSettings(**values)
            settings.save()
            self.sensor_manager.controller.configure(settings)
            if arm:
                self.sensor_manager.start_controller()
            elif self.sensor_manager.connection_status['nianfujiao']:
                self.sensor_manager.apply_controller()
            self.control_panel.update_controller_status(self.sensor_manager.controller.snapshot())
            if arm:
                message = 'STM32 已确认布防'
            elif self.sensor_manager.connection_status['nianfujiao']:
                message = 'STM32 已确认参数，当前撤防'
            else:
                message = '参数已保存，连接 STM32 后应用'
            self.statusBar().showMessage(message)
            return True
        except Exception as exc:
            QMessageBox.warning(self, '控制参数', str(exc))
            return False

    def _on_controller_start(self, values):
        if self._offline_workbench is not None and self._offline_workbench.busy:
            QMessageBox.information(self, '离线分析进行中', '请等待离线分析完成后再启动控制器。')
            return
        self._on_controller_apply(values, arm=True)

    def _on_controller_stop(self):
        try:
            self.sensor_manager.controller.stop()
            self.control_panel.update_controller_status(self.sensor_manager.controller.snapshot())
        except Exception as exc:
            QMessageBox.warning(self, '控制器', str(exc))

    def _on_controller_reload(self):
        try:
            self.sensor_manager.controller.reload_calibration()
            self.control_panel.update_controller_status(self.sensor_manager.controller.snapshot())
            self.statusBar().showMessage('标定参数已重载，点击启动 / 重新布防后生效于控制输出')
        except Exception as exc:
            QMessageBox.warning(self, '标定参数未载入', str(exc))
    
    def _on_connect_sensor(self, sensor_id: str):
        """连接传感器"""
        self.connection_panel.set_all_busy(True)
        
        success = False
        if sensor_id == 'nianfujiao':
            success = self.sensor_manager.connect_nianfujiao()
        elif sensor_id == 'liuzhouli':
            success = self.sensor_manager.connect_liuzhouli()
        
        self.connection_panel.set_all_busy(False)
        
        if success:
            QMessageBox.information(self, "成功", f"{sensor_id} 连接成功！")
        else:
            QMessageBox.warning(self, "失败", f"{sensor_id} 连接失败，请检查连接参数和配置！")
    
    def _on_disconnect_sensor(self, sensor_id: str):
        """断开传感器"""
        if sensor_id == 'nianfujiao':
            self.sensor_manager.disconnect_nianfujiao()
        elif sensor_id == 'liuzhouli':
            self.sensor_manager.disconnect_liuzhouli()
        
        QMessageBox.information(self, "成功", f"{sensor_id} 已断开！")
    
    def _on_connect_all(self):
        """连接所有传感器"""
        self.connection_panel.set_all_busy(True)
        
        results = {
            'nianfujiao': self.sensor_manager.connect_nianfujiao(),
            'liuzhouli': self.sensor_manager.connect_liuzhouli(),
        }
        ultrasonic_error = ""
        if self.connection_panel.ultrasonic_widget.port_name:
            try:
                self._open_ultrasonic()
                results['超声波'] = True
            except OSError as exc:
                results['超声波'] = False
                ultrasonic_error = f"\n超声波串口: {exc}"
        
        self.connection_panel.set_all_busy(False)
        
        success_count = sum(results.values())
        if success_count == len(results):
            QMessageBox.information(self, "成功", "所有传感器连接成功！")
        else:
            failed = [k for k, v in results.items() if not v]
            QMessageBox.warning(
                self, "部分失败", 
                f"成功: {success_count}/{len(results)}\n失败: {', '.join(failed)}{ultrasonic_error}"
            )

    def _on_disconnect_all(self):
        """断开所有传感器"""
        # 如果正在采集，先停止
        if self.data_recorder.is_recording:
            self._on_stop_recording()

        self.ultrasonic_serial.close()
        self.connection_panel.set_all_busy(True)

        def _worker():
            try:
                self.sensor_manager.disconnect_all()
                self.disconnect_all_finished.emit(True, "所有传感器已断开！")
            except Exception as e:
                self.disconnect_all_finished.emit(False, f"断开失败: {e}")

        Thread(target=_worker, daemon=True).start()

    def _on_disconnect_all_finished(self, success: bool, message: str):
        """断开所有传感器完成（主线程）"""
        self.connection_panel.set_all_busy(False)
        if success:
            QMessageBox.information(self, "成功", message)
        else:
            QMessageBox.warning(self, "失败", message)
    
    def _on_sensor_status_changed(self, sensor_id: str, connected: bool):
        """传感器连接状态变化"""
        # 更新连接面板状态
        self.connection_panel.update_sensor_status(sensor_id, connected)
        
        # 更新控制面板按钮状态
        self.control_panel.update_button_states(
            self.sensor_manager.get_all_status()
        )
        self._update_liuzhouli_zero_button()
    
    def _on_sensor_error(self, sensor_id: str, error_msg: str):
        """传感器错误"""
        self.connection_panel.set_sensor_error(sensor_id)
        QMessageBox.critical(self, f"{sensor_id} 错误", error_msg)

    def _open_ultrasonic(self):
        widget = self.connection_panel.ultrasonic_widget
        if not widget.port_name:
            raise OSError("请选择或输入串口号")
        self.ultrasonic_serial.open(widget.port_name, widget.baud_box.value())

    def _on_toggle_ultrasonic(self):
        if self.ultrasonic_serial.is_open:
            self.ultrasonic_serial.close()
            return
        try:
            self._open_ultrasonic()
        except OSError as exc:
            QMessageBox.warning(self, "超声波串口打开失败", str(exc))

    def _on_ultrasonic_connection_changed(self, connected):
        self.connection_panel.ultrasonic_widget.set_connected(connected)
        self.data_display_panel.update_ultrasonic_data(status="等待数据" if connected else "未连接")

    def _on_ultrasonic_sample(self, data):
        self.data_recorder.write_ultrasonic_data(data)
        self.data_display_panel.update_ultrasonic_data(data)

    def _on_ultrasonic_data_timeout(self):
        self.data_display_panel.update_ultrasonic_data(status="未收到串口数据")

    def _on_ultrasonic_error(self, message):
        self.data_display_panel.update_ultrasonic_data(status="串口读取失败")
        self.statusBar().showMessage(f"超声波串口: {message}")

    def _on_liuzhouli_zero_requested(self):
        """异步执行六轴力清零，避免阻塞 GUI 主线程。"""
        if self._liuzhouli_zero_busy:
            return
        if not self.sensor_manager.connection_status.get('liuzhouli', False):
            QMessageBox.warning(self, "提示", "六轴力传感器未连接")
            return
        if self.data_recorder.is_recording:
            QMessageBox.warning(self, "提示", "清零前请先停止数据记录")
            return

        self._liuzhouli_zero_busy = True
        self._update_liuzhouli_zero_button()
        self.connection_panel.setEnabled(False)
        self.recording_panel.start_btn.setEnabled(False)
        self.statusBar().showMessage("正在清零，请保持传感器静止；设备稳定等待至少 2 秒...")

        def _worker():
            success = False
            message = "六轴力传感器清零失败，请查看日志"
            try:
                success = bool(self.sensor_manager.liuzhouli_zero(
                    progress_callback=self.liuzhouli_zero_progress.emit,
                ))
                if success:
                    message = "六轴力传感器清零完成"
            except Exception as exc:
                message = f"六轴力传感器清零失败: {exc}"
            finally:
                self.liuzhouli_zero_finished.emit(success, message)

        try:
            Thread(target=_worker, daemon=True).start()
        except Exception as exc:
            self._on_liuzhouli_zero_finished(False, f"无法启动清零任务: {exc}")

    def _update_liuzhouli_zero_button(self):
        self.control_panel.liuzhouli_zero_btn.setEnabled(
            self.sensor_manager.connection_status.get('liuzhouli', False)
            and not self.data_recorder.is_recording
            and not self._liuzhouli_zero_busy
        )

    def _on_liuzhouli_zero_progress(self, message: str):
        self.statusBar().showMessage(message)

    def _on_liuzhouli_zero_finished(self, success: bool, message: str):
        """六轴力清零结果回调（主线程）。"""
        self._liuzhouli_zero_busy = False
        self.connection_panel.setEnabled(True)
        self.recording_panel.start_btn.setEnabled(not self.data_recorder.is_recording)
        self._update_liuzhouli_zero_button()
        self.statusBar().showMessage(message)

        if success:
            QMessageBox.information(self, "成功", message)
        else:
            QMessageBox.warning(self, "失败", message)
    
    # ==================== 数据更新槽函数 ====================
    
    def _on_nianfujiao_data_updated(self, style: int, data: dict):
        """粘附脚数据更新"""
        self.data_display_panel.update_nianfujiao_data(style, data)
        
    
    def _on_liuzhouli_data_updated(self, data: dict):
        """六轴力数据更新"""
        self.data_display_panel.update_liuzhouli_data(data)
        
    
    # ==================== 采集控制槽函数 ====================
    
    def _on_start_recording(self):
        """开始采集"""
        if self._offline_workbench is not None and self._offline_workbench.busy:
            self.recording_panel.reset()
            QMessageBox.information(self, '离线分析进行中', '请等待离线分析完成后再开始采集。')
            return
        if self._liuzhouli_zero_busy:
            self.recording_panel.reset()
            self.recording_panel.start_btn.setEnabled(False)
            return
        # 检查是否有传感器连接
        if not self.ultrasonic_serial.is_open and not any(self.sensor_manager.get_all_status().values()):
            QMessageBox.warning(self, "警告", "请先连接至少一个传感器！")
            self.recording_panel.reset()
            return
        
        # 启动数据记录
        if self.data_recorder.start_recording():
            self.data_recorder.write_controller_snapshot(self.sensor_manager.controller.snapshot())
            self.recording_panel.update_data_counts(self.data_recorder.data_counts)
            self._update_liuzhouli_zero_button()
            # 更新存储路径
            session_path = self.data_recorder.get_session_path()
            self.recording_panel.update_storage_path(session_path)
            
            # 启动统计定时器
            self.stats_timer.start()
            
            QMessageBox.information(self, "成功", "开始采集数据！")
        else:
            QMessageBox.critical(self, "错误", "启动数据采集失败！")
            self.recording_panel.reset()
    
    def _on_stop_recording(self):
        """停止采集"""
        if self.data_recorder.stop_recording():
            self._update_liuzhouli_zero_button()
            # 停止统计定时器
            self.stats_timer.stop()
            
            # 最后更新一次统计信息
            self.recording_panel.update_data_counts(self.data_recorder.data_counts)
            
            QMessageBox.information(
                self, "成功", 
                f"数据采集已停止！\n存储路径: {self.data_recorder.get_session_path()}"
            )
        else:
            QMessageBox.warning(self, "警告", "未在采集中！")
    
    def _on_open_folder(self, folder_path: str):
        """打开文件夹"""
        try:
            if platform.system() == 'Windows':
                os.startfile(folder_path)
            elif platform.system() == 'Darwin':  # macOS
                subprocess.run(['open', folder_path])
            else:  # Linux
                subprocess.run(['xdg-open', folder_path])
        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法打开文件夹: {str(e)}")
    
    # ==================== 统计信息更新 ====================
    
    def _update_statistics(self):
        """更新统计信息（数据条数、文件大小）"""
        if not self.data_recorder.is_recording:
            return
        
        # 更新数据条数
        self.recording_panel.update_data_counts(self.data_recorder.data_counts)
        
        # 更新文件大小
        session_path = self.data_recorder.get_session_path()
        if session_path and os.path.exists(session_path):
            total_size = 0
            for root, dirs, files in os.walk(session_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    total_size += os.path.getsize(file_path)
            
            size_mb = total_size / (1024 * 1024)
            self.recording_panel.update_file_size(size_mb)
    
    # ==================== 窗口关闭事件 ====================
    
    def closeEvent(self, event):
        """窗口关闭事件"""
        if self._offline_workbench is not None and self._offline_workbench.busy:
            self.statusBar().showMessage('正在导出离线结果，请等待完成后关闭窗口')
            event.ignore()
            return
        if self._liuzhouli_zero_busy:
            self.statusBar().showMessage("正在清零，请等待清零结束后关闭窗口")
            event.ignore()
            return
        # 如果正在采集，提示用户
        if self.data_recorder.is_recording:
            reply = QMessageBox.question(
                self, '确认退出',
                "正在采集数据，确定要退出吗？\n数据将会保存。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.No:
                event.ignore()
                return
            
            # 停止采集
            self.data_recorder.stop_recording()
        
        if self._offline_workbench is not None:
            self._offline_workbench.close()
        # 断开所有传感器
        self.ultrasonic_serial.close()
        self.sensor_manager.disconnect_all()
        
        event.accept()
