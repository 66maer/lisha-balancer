from pathlib import Path
import cv2,numpy as np
from lisha_overlay.death_word import DeathWordDetector


def test_literal_word_matches_on_both_green_and_red_health_bars():
    path=Path(__file__).resolve().parents[1]/'src/lisha_overlay/assets/learned/death_word_mask.png'
    ink=cv2.imdecode(np.fromfile(path,dtype=np.uint8),cv2.IMREAD_GRAYSCALE)>0
    detector=DeathWordDetector()
    for colour in ((20,150,30),(20,20,160),(15,15,15)):
        image=np.full((42,85,3),colour,np.uint8);h,w=ink.shape;image[10:10+h,32:32+w][ink]=(130,120,220)
        assert detector.match(image,64)[0]


def test_red_damage_numbers_and_empty_bars_are_not_literal_death_text():
    detector=DeathWordDetector();image=np.zeros((42,85,3),np.uint8)
    cv2.putText(image,'123',(15,27),cv2.FONT_HERSHEY_SIMPLEX,.7,(130,120,220),2)
    assert not detector.match(image,64)[0]
    assert not detector.match(np.zeros_like(image),64)[0]


def test_dim_resampled_death_word_keeps_its_shape_on_a_dark_cell():
    path = Path(__file__).resolve().parents[1] / 'src/lisha_overlay/assets/learned/death_word_video_mask.png'
    mask = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    alpha = cv2.resize(mask, (28, 13), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    image = np.full((34, 74, 3), 12, dtype=np.uint8)
    image[9:22, 23:51] = 12 + alpha[:, :, None] * np.array([57, 62, 108])
    assert DeathWordDetector().match(image, 53)[0]
