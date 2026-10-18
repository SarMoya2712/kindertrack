# KinderTrack registration and LIVE scanner update

Implemented in the existing Flask/PostgreSQL application. No schema, attendance,
tariff, dashboard calculation, or statistics business-rule changes.

## Registration

- 1–10 references per child; JPEG, PNG, WebP. Limits use binary megabytes:
  15 MiB/file, 100 MiB/submitted batch, 50 million source pixels.
- Batch size checks precede decoding/inference. Pillow verifies the actual format,
  dimensions and complete decoding; corrupt, unsupported, animated and
  decompression-bomb images fail. Original filenames are never used for storage.
- EXIF orientation is applied, metadata removed, aspect ratio preserved, maximum
  edge reduced to 1600 px with Lanczos, output encoded as JPEG quality 90.
- Photos append by default. Preview and remove new selections individually.
  Existing-photo checkboxes remove only selected photos; select one and upload a
  replacement to replace that individual reference. The final count remains 1–10.
- Each new valid reference generates one normalized 128-dimensional SFace vector
  associated with its child in PostgreSQL. Existing references and vectors remain
  intact during additions and unrelated edits. Existing unenrolled-only backfill
  behavior remains intact. Matching reads stored vectors, not reference photos.
- Changes are transactional and child edits are locked; failed writes roll back and
  remove newly created files. Replaced files are removed only after commit.

## Protected persistent storage

Private photo routes retain authentication and `no-store, private`; photo directories
use 0700 and files 0600. DATA_DIR/MODEL_DIR are configurable. On Railway they default
to the attached volume and its models subdirectory. Startup refuses paths outside
the declared volume. `scripts/start_railway.sh` verifies/downloads models into that
volume at runtime, runs existing migrations, then starts Gunicorn.

**Deployment action remains:** attach the Railway volume, copy existing photos while
preserving database filenames, and configure the start command as documented in
README. A local restart persistence test passed, but no Railway account/deployment
was inspected or redeployed. Runtime volume mounting follows the
[Railway volume documentation](https://docs.railway.com/volumes).

## Mobile scanner and quality

The preview fills nearly the entire mobile width with responsive portrait/landscape
ratios, `object-fit: cover`, safe-area spacing and 48 px minimum controls. Decorative
side content is hidden on phones. Front/rear switching stops previous tracks. The
rear camera remains preferred, using ideal 1280×960 constraints with fallback.
Front mirroring affects display only. Recognition receives the uncropped, browser-
oriented source frame resized to a maximum edge of 720 px. Bounding boxes use the
cover scale, centered crop offsets and mirrored display coordinates. Old boxes
expire, resize updates their mapping, and responses with obsolete orientation are
ignored. Fullscreen uses the API with a CSS immersive fallback and exit control.

Quality is advisory and localized in Russian, Uzbek Latin and Uzbek Cyrillic:

| Observation | Logic |
| --- | --- |
| Size | Minimum of face-width/frame-width and face-height/frame-height; below .10: too far; below .18: move closer |
| Position | Face crossing source boundaries or the visible cover crop prompts repositioning |
| Brightness | Mean grayscale intensity in a 128×128 face crop; below 45: insufficient light |
| Blur | Laplacian variance on that crop; below 35: blurred |
| Multiple faces | Detector count greater than one |
| Pose | Nose offset from eye midpoint / eye span greater than .8 gives an extreme-pose hint |

These are heuristic diagnostics, not calibrated confidence percentages. Moderate
side angles are not rejected by the quality layer. The existing one-face and
minimum-60-pixel recognition checks remain. Quality and boxes arrive with sampled
recognition responses, so network/inference latency can cause visible lag during
motion. Pose is a landmark-asymmetry hint, not a measured yaw angle.

## LIVE behavior and measurements

One self-scheduling request runs at a time. Sampling is configurable from 300–5000 ms;
confirmation windows expand as needed for slow sampling. JPEG quality remains .82.
Duplicate sample fingerprints are throttled to 1.5 seconds during confirmation and
10 seconds otherwise, allowing stable confirmation without continuously submitting
a static scene. Existing attendance uniqueness and audio/unknown cooldowns remain.

Local warm-model benchmark, 30 runs, 720×720 real-image fixture:

| Measurement | Result |
| --- | --- |
| JPEG frame payload | 74,852 bytes |
| Decode + detection + quality + embedding, median | 23.94 ms |
| Same pipeline, p95 | 25.91 ms |

These timings exclude DB comparison, network, and mobile hardware. No production
throughput, mobile FPS, recognition accuracy, or battery-life claim is made. The
browser slow-request test confirms advancing video playback with only one pending
request. A synthetic horizontal motion-blur experiment on the real-image fixture
reduced Laplacian variance from 629.1 to 27.9 (31-pixel kernel), triggering the hint.

## Verification

Final full suite: **149 passed in 147.44 seconds**. Follow-up: **2 passed in 5.24
seconds**, including one newly added aggregate-limit case and one repeated geometry
check after final CSS changes. **150 distinct tests passed.** JavaScript syntax,
Python compilation, focused fatal/undefined-name lint, shell syntax and git whitespace
checks also passed. Desktop Chrome used simulated cameras; it was not Android Chrome
or mobile Safari. A mobile screenshot was inspected at
`/tmp/kindscan-scanner-review/scanner-mobile.png`.

| Requested test | Evidence / status |
| --- | --- |
| 1 photo; 10 photos | PostgreSQL integration and real-model enrollment tests passed |
| 11 photos | Rejected without child/photo records |
| Per-file and aggregate limits | Unit and multipart registration integration passed |
| Decompression bombs | Oversized PNG headers, including 50,010,000 pixels, rejected before pixel allocation |
| Stored embeddings | Real 10-reference enrollment, vector norms/model, association, preservation and stored-vector-only matching passed |
| Front, rear, switching | Simulated Chrome constraints, mirroring, and track cleanup passed; physical lenses untested |
| Portrait/landscape | Simulated viewport, cover mapping and overflow tests passed |
| Fullscreen | API and CSS fallback paths passed in desktop Chrome |
| Bounding boxes | Source-to-cover coordinate checks and mirrored positions passed |
| Close/distant faces | Diagnostic ratio cases and existing real-model minimum-size rejection passed |
| Low light / blur | Synthetic diagnostic tests and real-fixture synthetic motion blur checked |
| Multiple faces | Diagnostic and real-model multi-face rejection passed |
| Side angles | Moderate landmark-asymmetry case accepted by quality logic; representative child/profile recognition accuracy untested |
| Android Chrome / mobile Safari | Not performed; physical devices unavailable |
| Railway persistence | Local restart and fail-closed path tests passed; actual Railway redeploy not performed |
| Attendance, audio, dashboard, statistics | Existing PostgreSQL concurrency/uniqueness, simulated Web Audio, end-to-end and reports tests passed |

Initial failures were resolved: obsolete photo-limit/audit expectations, a test DB
with C locale (retested in a dedicated UTF-8 database), duplicate suppression blocking
static-frame confirmation, and photo preview wrapping on narrow screens. No unrelated
search business logic was changed. The dedicated local `kindscan_scanner_test`
database remains available; test-created schemas were cleaned up by fixtures.

## Files modified / added

- Registration/storage: `routes/children.py`, `services/photo_storage.py`,
  `templates/child_form.html`, `static/app.js`.
- Scanner/quality: `routes/scanner.py`, `services/face_recognition.py`,
  `services/live_scanner.py`, **new** `services/frame_quality.py`,
  `static/scanner.js`, `static/app.css`, `templates/scanner.html`.
- Configuration/deployment: `app.py`, `config.py`, `.env.example`,
  `scripts/download_models.py`, **new** `scripts/start_railway.sh`.
- Localization: `translations/ru.json`, `translations/uz_lat.json`,
  `translations/uz_cyr.json`.
- Tests: `tests/test_application.py`, `tests/test_audit_security.py`,
  `tests/test_camera_browser.py`, `tests/test_children.py`,
  `tests/test_face_registration.py`, **new** `tests/test_photo_quality.py`.
- Documentation: `README.md`, **new** `REGISTRATION_SCANNER_REPORT.md`.
