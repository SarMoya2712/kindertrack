"""Observable frame diagnostics, advisory only; thresholds are not confidence scores."""
import cv2
import numpy as np


def evaluate(image, faces):
    height, width = image.shape[:2]
    boxes = [] if faces is None else [[float(v) for v in face[:4]] for face in faces]
    result = {'width':width, 'height':height, 'boxes':boxes, 'state':'camera_ready'}
    if not boxes:
        return result
    if len(boxes) > 1:
        result['state'] = 'multiple_faces'
        return result
    x, y, w, h = boxes[0]
    roi = image[max(0,int(y)):min(height,int(y+h)), max(0,int(x)):min(width,int(x+w))]
    if not roi.size:
        result['state'] = 'quality_position'
        return result
    gray = cv2.cvtColor(cv2.resize(roi, (128,128)), cv2.COLOR_BGR2GRAY)
    brightness = float(np.mean(gray))
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    ratio = min(w/width, h/height)
    # Five landmarks permit an asymmetry hint, not a calibrated yaw angle.
    landmarks = faces[0][4:14].reshape(5,2)
    eye_span = float(np.linalg.norm(landmarks[1]-landmarks[0]))
    asymmetry = abs(float(landmarks[2][0]-(landmarks[0][0]+landmarks[1][0])/2))/max(eye_span,1)
    state = ('quality_far' if ratio < .10 else 'closer' if ratio < .18 else
             'quality_position' if x<0 or y<0 or x+w>width or y+h>height else
             'quality_dark' if brightness<45 else 'quality_blur' if sharpness<35 else
             'quality_pose' if asymmetry>.8 else 'face_detected')
    result.update(state=state, brightness=round(brightness,1), sharpness=round(sharpness,1),
                  face_ratio=round(ratio,3), pose_hint='extreme' if asymmetry>.8 else 'normal')
    return result
