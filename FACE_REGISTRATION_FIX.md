# Child photo registration detection fix

## Findings and root cause

The original registration branch in `FaceService.analyze()` raised `one_face`
both when YuNet returned zero detections and when it returned multiple detections.
Consequently, “В кадре должно быть ровно одно лицо” did not identify which
condition occurred.

The reported child's image is not present among the project image fixtures.
Its exact rejection reason, original detection count, expression sensitivity,
and possible background false positives therefore cannot be established.
The message alone cannot establish any of these facts.

A separate, reproducible detector-scale defect was confirmed with the bundled
`lena.jpg`: resize the portrait to 1600×1600 and run the original detector with
its unchanged 0.85 threshold. The original detected face count is **0**.
At 640×640 the same portrait produces **1** detection. The old upload pipeline
could feed a 1600-pixel image directly to YuNet with no smaller-scale pass.

OpenCV's [official YuNet documentation](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)
describes its approximate training-related face-size range as 10–300 pixels.
The reproduced behavior is consistent with this scale limitation. It does not
prove that scale caused the reported child's rejection.

EXIF correction, aspect-ratio-preserving thumbnailing, RGB encoding, and OpenCV
BGR decoding were already correct. No unintended crop or extra tensor
normalization was found. No mouth-state or neutral-expression rule existed.
There is no evidence justifying a confidence reduction.

## Changes

- Registration detects the entire stored image at native size and at long-edge
  limits of 640 and 320 pixels, without upscaling. Native size remains included
  to preserve evidence of small/background faces.
- Boxes and five alignment landmarks are mapped back to stored-image coordinates.
  Only duplicate detections from different passes are merged (IoU > 0.5).
  Detections from the same pass are never merged by this extra step.
- Nonfinite detections, invalid sizes, largely out-of-frame boxes, invalid
  landmark coordinates, or degenerate eye separation trigger a safe retry.
  Questionable extra candidates are not silently discarded to accept an image.
- Multiple distinct candidates are rejected, including small background faces.
  A single usable face retains the existing minimum size of 60×60 pixels.
- The existing frame-quality sharpness measure and blur cutoff of 35 are reused
  for enrollment. Blurry but detectable faces require a retry. This cutoff is
  a practical quality heuristic, not a calibrated guarantee of recognition.
- New registration-only translations give the exact requested Russian zero-face
  and multiple-face errors; Uzbek translations are also included.
- Admin sees instructions to select another clear photo and retry. Every retry
  repeats detection and embedding checks. No bypass or manual override was added.
- Diagnostic INFO events record original/oriented/stored dimensions, EXIF
  orientation, preprocessing, input sizes, detector/OpenCV versions, confidence
  threshold, detection counts, confidence scores, stored-coordinate boxes,
  merged count, ambiguity, and sharpness. No image bytes, filenames, arbitrary
  EXIF metadata, landmarks, or embedding vectors are logged.
- SFace aligns against the exact normalized image that is stored. Finite
  128-dimensional embeddings and unit normalization remain required.
- Recognition identity thresholds, scanner detection/liveness, attendance,
  tariffs, and groups were not changed.

The newer `2026may` YuNet export described upstream changes dynamic-shape
compatibility for OpenCV 5's ONNX Runtime engine, rather than supplying newly
trained detection weights. This project requires OpenCV <5; no model replacement
was justified for this fix.

## Validation

Local OpenCV: **4.14.0**. Models match the checksums enforced by Railway's
`scripts/download_models.py`:

- YuNet 2023mar:
  `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`
- SFace 2021dec:
  `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`

Tests use `FaceService.reference()` and the production photo-preparation code,
plus HTTP registration against the isolated local PostgreSQL test database.
Railway's deployed OpenCV version and active model files were not inspected.

```sh
.venv/bin/pytest tests/test_registration_detection.py \
  tests/test_face_registration.py tests/test_real_recognition.py \
  tests/test_photo_quality.py tests/test_scanner.py \
  tests/test_live_scanner.py tests/test_children.py -q --tb=short
```

**61 passed in 15.16 seconds.** `git diff --check` also passed.

| Case | Result |
| --- | --- |
| Existing single-person adult portrait fixtures | Valid embeddings |
| 1600×1600 portrait missed by original detector | Original count 0; new pipeline embeds; HTTP registration succeeds |
| ±20° in-plane head rotation | Valid embeddings |
| EXIF-rotated portrait | Corrected, embeds, metadata stripped |
| Two copies of a portrait | Rejected as multiple faces |
| Two distinct people composed from existing fixtures | Rejected; second portrait alone embeds |
| Blank image | Exact zero-face error |
| Strongly blurred image | Rejected with no face |
| Low-detail image with a stubbed single detection | Retry; no embedding |
| Too-small face | Existing `closer` rejection |
| Cross-scale duplicate boxes (stubbed detector) | Merged into one |
| Small extra background face (stubbed detector) | Multiple-face rejection |
| Overlapping same-pass people (stubbed detector) | Not merged |
| Malformed/out-of-frame candidates (stubbed detector) | Safe retry |
| Existing enrollment, replacement, backfill, stored-vector matching | Pass |
| Scanner and live scanner regressions | Pass |

## Files changed

- `services/face_recognition.py`
- `services/photo_storage.py`
- `templates/child_form.html`
- `translations/ru.json`
- `translations/uz_lat.json`
- `translations/uz_cyr.json`
- `tests/test_registration_detection.py` (new)
- `tests/test_real_recognition.py`
- `README.md`
- `FACE_REGISTRATION_FIX.md` (new)

## Remaining limitations

The actual child photo and a representative child-expression dataset were not
available. Crying, smiling, open-mouth child faces, and controlled out-of-plane
side-angle cases remain unverified; ±20° tests demonstrate roll tolerance only.
There is no new expression restriction, but general support cannot be guaranteed
without these tests.

Valid-looking background false positives can still cause a conservative
rejection; geometry and multiscale agreement cannot prove a candidate is human.
The retry workflow is implemented; an interactive crop/selection editor is not.
Detection now costs up to three passes per registration photo. Railway deployment
was not performed, and this change does not claim production validation.
