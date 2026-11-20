from dataclasses import replace
import cv2
import numpy as np
from PySide6.QtCore import QRectF
from lisha_overlay.team_layout import TeamLayout
from lisha_overlay.learned_geometry import calibrated_bands, fit_buff_band


def fixture():
    image=np.full((360,750,3),20,np.uint8)
    layout=TeamLayout.uniform((5,),left=.02,right=.02,top=.02,bottom=.02,column_gap=0,row_gap=0,footer=0)
    cells=layout.cells(QRectF(0,0,750,360))
    for c in cells:
        x=round(c.rect.left()+6);y=round(c.rect.top()+25)
        cv2.rectangle(image,(x,y),(x+31,y+31),(240,240,240),2)
        cv2.rectangle(image,(x+3,y+3),(x+28,y+28),(60,30,65),-1)
    return image,cells


def test_artwork_scale_comes_from_rims_instead_of_other_skill_edges():
    image,cells=fixture();bands,side,support=calibrated_bands(image,cells)
    assert support>=20 and 31<=side<=35
    for cell,(_,bounds) in zip(cells,bands):
        expected=round(cell.rect.top()+25)
        assert abs(bounds[1]-expected)<=2
        assert bounds[3]>=expected+32


def test_small_grid_translation_keeps_physical_baselines_and_picture_scale():
    image,cells=fixture();bands,side,_=calibrated_bands(image,cells)
    shifted=[replace(c,rect=c.rect.translated(2,-2)) for c in cells]
    moved,new_side,_=calibrated_bands(image,shifted)
    assert side==new_side
    for (_,before),(_,after) in zip(bands,moved):
        assert before[0]==after[0] and before[1]==after[1]


def test_fit_keeps_a_larger_icon_and_padding_within_its_player_tile():
    layout = TeamLayout.uniform((5,), left=0, right=0, top=0, bottom=0,
                                column_gap=0, row_gap=0, footer=0)
    cell = layout.cells(QRectF(0, 0, 600, 250))[0]

    bounds, anchor = fit_buff_band([10, 22, 120, 45], cell, [(5, 16, 31)], 22, 250)

    assert bounds == [3, 14, 120, 49]
    assert anchor == (1, 1, 31)


def test_conflicting_first_slot_rims_leave_the_team_geometry_in_place():
    layout = TeamLayout.uniform((5,), left=0, right=0, top=0, bottom=0,
                                column_gap=0, row_gap=0, footer=0)
    cell = layout.cells(QRectF(0, 0, 600, 250))[0]
    bounds = [8, 23, 120, 50]

    assert fit_buff_band(bounds, cell, [(7, 22, 22), (7, 25, 22)], 22, 250) == (bounds, None)
