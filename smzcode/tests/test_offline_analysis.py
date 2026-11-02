"""离线重建、拟合、配置应用和GUI流程；使用合成数据，不连接设备。"""
import csv
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from controller.algorithms.calibration import CALIBRATION_PATH, Calibration
from controller.offline.workflow import (
    ReconstructionOptions, apply_result, discover_sessions, fit_sessions,
    reconstruct_session, run_analysis,
)


def make_session(root, name='one', shift=0, sequences=None):
    path = Path(root)/name
    path.mkdir()
    sequences = sequences or list(range(1,11))
    with (path/'nianfujiao_frames.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['sample_sequence','ready_us','Fz1_raw','Fz2_raw'])
        for seq in sequences:
            t = (seq-1)*16
            writer.writerow([seq,100000+t*1000,200+3*t+shift,50+.03*t*t+2*shift])
    with (path/'liuzhouli.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer = csv.writer(f); writer.writerow(['Fz'])
        for t in range(145):
            writer.writerow([-.002*(200+3*t+shift)-.003*(50+.03*t*t+2*shift)+.1])
    return path


class OfflineEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_frame_gaps_keep_time_slots_manual_offset_and_no_extrapolation(self):
        path = make_session(self.root,sequences=[1,2,4,11])
        session = reconstruct_session(path,ReconstructionOptions(),offset_ms=2)
        np.testing.assert_array_equal(session.foot_ms,[2,18,50,162])
        self.assertEqual(session.summary['missing_sequence_slots'],7)
        self.assertEqual(session.summary['matched_frames'],3)
        self.assertTrue(np.isnan(session.target[-1]))
        six = np.array([float(r['Fz']) for r in session.six_rows])
        self.assertEqual(session.target[0],six[2])
        session = reconstruct_session(path,offset_ms=-1)
        self.assertFalse(session.matched[0])

    def test_fit_recovers_known_coefficients_and_holds_out_whole_sessions(self):
        paths = [make_session(self.root,str(i),shift=i*20) for i in range(3)]
        sessions = [reconstruct_session(p) for p in paths]
        result = fit_sessions(sessions,ReconstructionOptions())
        np.testing.assert_allclose(result['coefficients'],[-.002,-.003,.1],atol=1e-12)
        self.assertTrue(result['validation_complete'])
        self.assertEqual(result['validation_predicted_points'],30)
        self.assertLess(result['leave_one_session_out_all']['rmse_n'],1e-11)
        self.assertEqual(len(result['folds']),3)

    def test_invalid_period_reset_conflicting_duplicates_and_rank_deficiency_rejected(self):
        for value in (0,-1,float('nan')):
            with self.assertRaises(ValueError):
                ReconstructionOptions(six_period_ms=value)
        path = make_session(self.root,sequences=[1,3,2])
        with self.assertRaisesRegex(ValueError,'倒退'):
            reconstruct_session(path)
        path = make_session(self.root,'duplicate',sequences=[1,2,2,3])
        session = reconstruct_session(path)
        self.assertEqual(session.summary['duplicate_foot_rows_removed'],1)
        session.raw[:,1] = session.raw[:,0]*2
        with self.assertRaisesRegex(ValueError,'独立'):
            fit_sessions([session],ReconstructionOptions())

    def test_export_keeps_original_files_and_apply_preserves_axes_with_rollback(self):
        path = make_session(self.root)
        before = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in path.iterdir()}
        result = run_analysis([path],self.root/'output',fit=True)
        out = Path(result['output_dir'])
        for name in ['report.html','analysis.json','paired_for_calibration.csv',
                     'joint_fit.json','calibration_candidate.json']:
            self.assertTrue((out/name).is_file())
        self.assertIsNone(result['fit']['leave_one_session_out_all'])
        with (out/'01_one'/'foot_reconstructed.csv').open(encoding='utf-8-sig') as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows),10)
        self.assertAlmostEqual(float(rows[0]['analysis_joint_reference_N']),-.45)
        self.assertAlmostEqual(float(rows[0]['analysis_joint_compression_N']),.45)
        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in path.iterdir()})
        config = self.root/'calibration.json'
        original = CALIBRATION_PATH.read_bytes(); config.write_bytes(original)
        callback = Mock(side_effect=RuntimeError('reload failed'))
        with self.assertRaisesRegex(RuntimeError,'reload failed'):
            apply_result(result,config,callback)
        self.assertEqual(config.read_bytes(),original)
        backup = apply_result(result,config)
        self.assertEqual(backup.read_bytes(),original)
        updated = Calibration(config).snapshot()
        self.assertEqual(updated['coefficients'],json.loads(original)['coefficients'])
        np.testing.assert_allclose(updated['normal_force']['coefficients'],[-.002,-.003,.1],atol=1e-12)

    def test_discovery_and_reconstruction_only_do_not_refit_or_change_live_config(self):
        path = make_session(self.root)
        self.assertEqual(discover_sessions(self.root),[path])
        original = CALIBRATION_PATH.read_bytes()
        result = run_analysis([path],self.root/'output')
        self.assertIsNone(result['fit'])
        self.assertEqual(CALIBRATION_PATH.read_bytes(),original)
        with self.assertRaisesRegex(ValueError,'原始会话'):
            run_analysis([path],path/'results')


class OfflineGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_background_job_preview_and_explicit_apply_only(self):
        from controller.visualization.offline_workbench import OfflineWorkbench
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            session = make_session(root)
            config = root/'calibration.json'; original = CALIBRATION_PATH.read_bytes()
            config.write_bytes(original)
            reload = Mock()
            dialog = OfflineWorkbench(current_session=session,config_path=config,reload_callback=reload)
            dialog.output_edit.setText(str(root/'out'))
            dialog.show()
            try:
                dialog.fit_button.click()
                # 线程已退出、结果信号尚未处理时，仍需保持窗口和主界面的忙状态。
                self.assertTrue(dialog._job.wait(15000))
                dialog.close()
                self.assertTrue(dialog.isVisible())
                deadline = time.monotonic()+15
                while dialog.busy and time.monotonic()<deadline:
                    self.app.processEvents(); time.sleep(.01)
                self.app.processEvents()
                self.assertFalse(dialog.busy)
                self.assertIsNotNone(dialog.result,dialog.status.text())
                self.assertTrue(dialog.apply_button.isEnabled())
                self.assertTrue(dialog.folder_button.isEnabled())
                self.assertFalse(dialog._image.isNull())
                reload.assert_not_called()
                self.assertEqual(config.read_bytes(),original)
                dialog.apply_button.click()
                reload.assert_called_once()
                self.assertIn('已应用',dialog.status.text())
                self.assertAlmostEqual(Calibration(config).snapshot()['normal_force']['coefficients'][0],-.002)
                dialog.foot_period.setValue(15)
                self.assertIsNone(dialog.result)
                self.assertFalse(dialog.apply_button.isEnabled())
                self.assertFalse(dialog.folder_button.isEnabled())
                self.assertEqual(dialog.preview_combo.count(),0)
                self.assertIsNone(dialog._image)
                self.assertEqual(dialog.summary.toPlainText(),'')
            finally:
                if dialog.busy:
                    dialog._job.wait(15000); self.app.processEvents()
                dialog.close(); dialog.deleteLater(); self.app.processEvents()

    def test_main_entry_reuses_window_and_only_opening_does_not_change_control(self):
        from gui.main_window import MainWindow
        from unittest.mock import patch
        window = MainWindow(str(ROOT/'config'/'default.yaml'))
        try:
            manager = window.sensor_manager
            with patch.object(manager.controller,'start') as start, \
                    patch.object(manager.controller,'stop') as stop:
                window.recording_panel.offline_button.click()
                dialog = window._offline_workbench
                self.assertIsNotNone(dialog)
                dialog.close()
                window.recording_panel.offline_button.click()
                self.assertIs(window._offline_workbench,dialog)
                start.assert_not_called(); stop.assert_not_called()
                window.data_recorder.is_recording = True
                dialog._start(True)
                self.assertIsNone(dialog._job)
                self.assertIn('停止数据采集',dialog.status.text())
                window.data_recorder.is_recording = False
        finally:
            if window._offline_workbench:
                window._offline_workbench.close()
            manager.controller.stop(); manager.display_timer.stop()
            window.log_widget.stop_capture(); window.deleteLater(); self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
