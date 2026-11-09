from pathlib import Path
import cv2,numpy as np
from lisha_overlay.scene_words import SceneWordDetector


def test_menu_word_over_buff_data_pauses_but_the_same_word_in_a_name_does_not():
    path=Path(__file__).resolve().parents[1]/'src/lisha_overlay/assets/learned/scene_need_word.png'
    word=cv2.imdecode(np.fromfile(path,dtype=np.uint8),cv2.IMREAD_GRAYSCALE)
    detector=SceneWordDetector();h,w=word.shape
    image=np.zeros((120,220,3),np.uint8);image[48:48+h,80:80+w]=word[:,:,None]
    bands=[[30,40,180,80]]
    assert detector.find(image,bands,64)
    name_only=np.zeros_like(image);name_only[8:8+h,80:80+w]=word[:,:,None]
    assert not detector.find(name_only,bands,64)


def test_white_health_numbers_do_not_become_a_game_menu():
    image=np.zeros((90,180,3),np.uint8)
    cv2.putText(image,'11.8w',(40,58),cv2.FONT_HERSHEY_SIMPLEX,.55,(240,240,240),1)
    assert not SceneWordDetector().find(image,[[0,30,180,80]],64)
