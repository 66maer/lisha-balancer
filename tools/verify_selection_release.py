"""Exercise the frozen calibration flow using owned windows and IPC, no input.

Separate profile/data directories keep the three test instances isolated from
the user's application. Only processes created here can be stopped here.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
import numpy as np
from PySide6.QtCore import QPoint,Qt
from PySide6.QtGui import QImage,QPainter
from PySide6.QtWidgets import QApplication,QWidget
from lisha_overlay import __version__
from test_grid_detection import _draw_grid


def read_json(path):
    try:return json.loads(path.read_text(encoding='utf8'))
    except (OSError,ValueError):return None


class Canvas(QWidget):
    def __init__(self,image,x,y):
        super().__init__(None,Qt.WindowType.FramelessWindowHint|Qt.WindowType.Tool|
                         Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.WindowDoesNotAcceptFocus)
        self.image=image
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(x,y,image.width(),image.height())
    def paintEvent(self,event):
        painter=QPainter(self);painter.drawImage(QPoint(0,0),self.image)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--verify-analysis',action='store_true',help='Also check immediate reference publication and subsequent confirmation.')
    parser.add_argument('--reference',type=Path,help='Owned 760x620 fixture containing a real calibrated panel.')
    parser.add_argument('--expect-command',action='store_true',help='Require the grouped command strategy in the analysis check.')
    args=parser.parse_args();args.output.mkdir(exist_ok=False,parents=True)
    exe=args.executable.resolve();app=QApplication([]);app.setQuitOnLastWindowClosed(False)
    screen=next(s for s in app.screens() if s.devicePixelRatio()==1 and s.geometry().width()>=1500 and s.geometry().height()>=900)
    origin=screen.availableGeometry().topLeft()
    pixels=np.full((620,760,3),(28,34,31),np.uint8);_draw_grid(pixels,(3,2))
    picture=QImage(pixels.data,760,620,pixels.strides[0],QImage.Format.Format_BGR888).copy()
    if args.reference:
        picture=QImage(str(args.reference.resolve()))
        if picture.isNull() or picture.width()!=760 or picture.height()!=620:raise ValueError('Expected a 760x620 verification fixture')
    blank=QImage(360,420,QImage.Format.Format_RGB32);blank.fill(0xff1c221f)
    fixture=Canvas(picture,origin.x()+40,origin.y()+90)
    empty=Canvas(blank,origin.x()+900,origin.y()+100)
    fixture.show();empty.show();app.processEvents()
    records=[];owned=[];active=None;failure=None

    def wait(predicate,seconds=20,allow_exit=False):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            app.processEvents()
            if predicate():return
            if not allow_exit and active is not None and active.poll() is not None:raise RuntimeError('Owned test application exited')
            time.sleep(.03)
        raise TimeoutError('Owned calibration verification timed out')

    def spawn(arguments,env):
        p=subprocess.Popen([str(exe),*arguments],cwd=exe.parent,env=env,
            creationflags=subprocess.CREATE_NO_WINDOW)
        owned.append(p);return p

    try:
        for name,local,expected in [('empty',(900,100,360,420),'not_found'),
                                    ('partial',(40,90,210,620),'not_found'),
                                    ('complete',(40,90,760,620),'located')]:
            stage=args.output/name;data=stage/'data'/'LishaBalancer';data.mkdir(parents=True)
            profile=stage/'profile';profile.mkdir()
            env=os.environ.copy();env['USERPROFILE']=str(profile.resolve());env['LOCALAPPDATA']=str((stage/'data').resolve())
            for key in ('QT_QPA_PLATFORM','PYTHONHOME','PYTHONPATH'):env.pop(key,None)
            x,y,w,h=local;x+=origin.x();y+=origin.y()
            geometry=[x-4,y-4,w+8,h+8];region=dict(left=x,top=y,width=w,height=h)
            settings={'scan_geometry':geometry,'panel_geometry':[origin.x()+1300,origin.y()+100,232,108],
                      'attached':False,'pinned':True,'remote_compatible':True,'panel_passthrough':False}
            (data/'settings.json').write_text(json.dumps(settings),encoding='utf8')
            active=spawn([],env)
            wait(lambda:(read_json(data/'latest-grid.json') or {}).get('status')=='needs_calibration')
            command=spawn(['--locate'],env)
            wait(lambda:command.poll()==0 and (read_json(data/'latest-grid.json') or {}).get('status') in ('located','not_found')
                 and (read_json(data/'settings.json') or {}).get('grid_ready')==(expected=='located'))
            result=read_json(data/'latest-grid.json');saved=read_json(data/'settings.json')
            assert result['version']==__version__ and result['process_id']==active.pid
            assert result['status']==expected,(name,result)
            assert result['source_region']==region and result['search_scope']=='selection'
            assert saved['scan_geometry']==geometry,(name,saved['scan_geometry'],geometry)
            assert saved['grid_ready']==(expected=='located')
            records.append({'case':name,'expected':expected,'passed':True,'geometry_unchanged':True,
                            'requested_region':region,'diagnosis':result})
            if expected=='located' and args.verify_analysis:
                command=spawn(['--analyze','all'],env)
                wait(lambda:command.poll()==0 and (read_json(data/'latest-analysis.json') or {}).get('published'))
                first=read_json(data/'latest-analysis.json')
                assert first['version']==__version__ and first['published_kind']=='reference',first
                assert first['reference'] and first['confirmation_status']=='confirming'
                assert first['model_output_policy']=='best_current_prediction'
                wait(lambda:(read_json(data/'latest-analysis.json') or {}).get('confirmation_status')=='confirmed')
                confirmed=read_json(data/'latest-analysis.json')
                assert confirmed['published']
                if not args.reference:
                    assert confirmed['published_kind']=='confirmed' and not confirmed['reference']
                if args.expect_command:
                    assert confirmed['strategy_policy']=='buff_groups_then_refinement',confirmed
                    assert not any(a['kind']=='circle' and a['order']==2 and a['role']!='center' for a in confirmed['annotations'])
                    assert len(confirmed['guide_keys'])==3
                assert first['players']==confirmed['players']
                records[-1]['analysis']={'immediate_reference':first,'subsequent_confirmation':confirmed}
            quit_command=spawn(['--quit'],env)
            wait(lambda:quit_command.poll()==0 and active.poll()==0,seconds=10,allow_exit=True)
            active=None
    except Exception as error:
        failure=f'{type(error).__name__}: {error}'
    finally:
        fixture.hide();empty.hide()
        for p in owned:
            if p.poll() is None:p.terminate()
        report={'passed':failure is None,'version':__version__,'keyboard_mouse_calls':0,
                'error':failure,'records':records}
        (args.output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'passed':failure is None,'records':len(records),'error':failure},ensure_ascii=True),flush=True)
    return 0 if failure is None else 1


if __name__=='__main__':raise SystemExit(main())
