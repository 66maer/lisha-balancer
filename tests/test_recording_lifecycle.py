"""Owned offscreen widgets only; no screen capture or native input."""
import pytest
from PySide6.QtCore import QEvent,QPoint,QPointF,QRect,Qt
from PySide6.QtGui import QMouseEvent
from lisha_overlay import app as app_module,overlay,platform_win
from lisha_overlay.analysis import PlayerState,evaluate_current


@pytest.fixture
def controller(qt_app,monkeypatch,tmp_path,request):
    monkeypatch.setattr(app_module.sys, 'argv', ['main.py', *getattr(request, 'param', [])])
    monkeypatch.setattr(app_module,'load_settings',lambda:{'grid_ready':True,'remote_compatible':False,'panel_passthrough':True})
    monkeypatch.setattr(app_module,'save_settings',lambda settings:None)
    monkeypatch.setattr(app_module,'settings_path',lambda:tmp_path/'settings.json')
    monkeypatch.setattr(app_module,'register_hotkey',lambda *args:False)
    monkeypatch.setattr(app_module,'unregister_hotkey',lambda *args:True)
    for module in (app_module,overlay,platform_win):
        monkeypatch.setattr(module,'exclude_from_capture',lambda *args:True)
        monkeypatch.setattr(module,'set_click_through',lambda *args:True)
    monkeypatch.setattr(app_module,'set_topmost',lambda *args:True)
    monkeypatch.setattr(platform_win,'set_topmost',lambda *args:True)
    def region(scan,inset):
        rect=scan.geometry().adjusted(inset,inset,-inset,-inset)
        return dict(left=rect.x(),top=rect.y(),width=rect.width(),height=rect.height())
    monkeypatch.setattr(app_module,'native_client_region',region)
    c=app_module.OverlayController(qt_app)
    c._update_region()
    yield c
    c.shutdown();c.deleteLater()
    qt_app.processEvents()


@pytest.mark.parametrize("controller,expected", [
    ([], False),
    (["--debug-captures"], True),
    (["--no-debug-captures"], False),
    (["--debug-captures", "--no-debug-captures"], False),
], indirect=["controller"])
def test_capture_diagnostics_flags(controller, expected):
    assert controller.debug_captures is expected


def mouse(widget,kind,point,global_point):
    return QMouseEvent(kind,QPointF(point),QPointF(global_point),Qt.MouseButton.LeftButton,
                       Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)


def drag_frame(c,*,corner=False):
    point=QPoint(c.scan.width()-2,c.scan.height()-2) if corner else QPoint(35,2)
    start=c.scan.mapToGlobal(point)
    c.scan.mousePressEvent(mouse(c.scan,QEvent.Type.MouseButtonPress,point,start))
    c.scan.mouseMoveEvent(mouse(c.scan,QEvent.Type.MouseMove,point+QPoint(8,8),start+QPoint(8,8)))
    c.scan.mouseReleaseEvent(mouse(c.scan,QEvent.Type.MouseButtonRelease,point+QPoint(8,8),start+QPoint(8,8)))


def test_move_resize_calibrate_start_lock_stop_unlock(controller,qt_app):
    c=controller
    # A stored calibration cannot silently enable the first start this session.
    assert not c.grid_ready and not c.panel.start_button.isEnabled()
    assert not c.scan.locked
    c.scan.setGeometry(QRect(60,60,320,360))
    before=c.scan.geometry();drag_frame(c)
    assert c.scan.pos()!=before.topLeft()
    before=c.scan.size();drag_frame(c,corner=True)
    assert c.scan.size()!=before
    c._update_region()
    c.grid_ready=True;c._calibrated_geometry=QRect(c.scan.geometry());c.panel.set_grid_ready(True)
    # Mark running without starting the capture worker; exercise controller/UI only.
    c.running=True
    c.analyze_mode('all')
    assert c._continuous_analysis and c.scan.locked and c.panel._analysis_active
    assert c.panel.start_button.isEnabled() and not c.panel.grid_button.isEnabled()
    assert not c.panel.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    before=c.scan.geometry();drag_frame(c);drag_frame(c,corner=True)
    assert c.scan.geometry()==before
    # Recovery shortcuts cannot unlock an actively recording frame.
    c.set_locked(False)
    assert c.scan.locked
    c.stop_analysis()
    assert not c._continuous_analysis and not c.scan.locked and not c.panel._analysis_active
    before=c.scan.geometry();drag_frame(c)
    assert c.scan.geometry()!=before
    assert not c.grid_ready and not c.panel.start_button.isEnabled()


def test_instruction_is_bold_and_impossible_message_is_exact(controller,qt_app):
    players=[PlayerState(f'g{i//5+1}p{i%5+1}','black',2) for i in range(25)]
    result=evaluate_current(players)
    assert result.status=='no_solution' and result.instruction=='无解，等死吧'
    controller.panel.set_result(result);qt_app.processEvents()
    assert controller.panel._instruction_label.text()=='无解，等死吧'
    assert controller.panel._instruction_label.font().bold()
