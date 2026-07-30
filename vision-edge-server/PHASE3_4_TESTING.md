# Phase 3 and Phase 4 validation

This guide validates the complete Phase 1-4 path on the RTX laptop:

```text
4K camera
→ person detection
→ anonymous tracking
→ track-linked face detection and quality
→ consent-based local face identity
```

Phase 3 and Phase 4 do not change the deferred Phase 2 two-person crossing and Primary
hysteresis acceptance. Those scenarios can be repeated later with multiple participants.

## 1. Pull the existing branch

```powershell
cd D:\smart-office-agent
git switch Vision-Edge-Server
git pull --ff-only origin Vision-Edge-Server
git rev-parse --short HEAD
```

## 2. Use the verified environment

```powershell
conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server
python -m pip check
python -c "import cv2; print(cv2.__version__); print('FaceDetectorYN', hasattr(cv2, 'FaceDetectorYN')); print('FaceRecognizerSF', hasattr(cv2, 'FaceRecognizerSF'))"
```

Expected:

```text
OpenCV 4.9.0
FaceDetectorYN True
FaceRecognizerSF True
```

## 3. Download the two OpenCV face models

```powershell
.\scripts\download_face_models.ps1
```

Verify:

```powershell
Get-Item .\models\face_detection_yunet_2023mar.onnx
Get-Item .\models\face_recognition_sface_2021dec.onnx
```

Do not commit these binaries. `models/*.onnx` remains ignored by Git.

## 4. Run hardware-independent tests

```powershell
python -m pytest -q
```

The Phase 3-4 tests cover:

- face-quality scoring on a synthetic frontal face observation;
- SQLite identity CRUD;
- bounded embedding samples and normalized identity prototypes;
- repeated-observation confirmation;
- best-versus-second-best identity margin.

## 5. Start the complete service

Close Windows Camera, Teams, browsers, and any other camera user first.

```powershell
.\scripts\start_vision_server.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

The status endpoint must report:

```text
version = 0.5.0
phase = phase4_face_identity
vision.status = ready
face.ready = true
identity.ready = true
```

Status URL:

```text
http://127.0.0.1:8015/api/v1/status
```

## 6. Automated Phase 3-4 smoke test

Open a second PowerShell terminal:

```powershell
conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server
.\scripts\run_phase34_smoke_test.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

This validates model loading, APIs, identity database access, and debug image generation. It
does not require a visible face.

Then stand in front of the camera and run the stricter form:

```powershell
.\scripts\run_phase34_smoke_test.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe" `
  -RequireFace
```

Look at the camera, avoid strong backlight, and remain still until a face has passed the
quality gate for three face-analysis cycles.

Expected:

```text
PASS: Phase 3 face analysis and Phase 4 local identity runtime are ready.
```

## 7. Compact Phase 1-4 evaluation window

```powershell
.\scripts\run_phase1234_live_view.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe" `
  -WindowWidth 960 `
  -WindowHeight 540
```

The window displays:

- raw person detection when enabled in configuration;
- track ID, track state, trail, Primary and engagement state;
- face box and five YuNet landmarks;
- face quality, sharpness and frontal score;
- recognized local display name and SFace similarity;
- face, track and identity counts.

Controls:

```text
Q / Esc   close
P         pause or resume
Space     save annotated screenshot
R         restart the server-side vision pipeline
L         print enrolled identities in the terminal
E         enroll the current Primary, only when an enrollment name was supplied
```

Observations and screenshots are saved under:

```text
logs/phase1234_live_view/
```

## 8. Phase 3 face-quality tests

Use the compact window and test these cases individually.

### Normal frontal face

Stand at a normal conversation distance and look at the camera.

Expected:

```text
face box follows the matching track_id
five landmarks remain on the eyes, nose and mouth corners
quality.ready becomes true after stable observations
```

### Turn away

Turn the head significantly left or right.

Expected:

```text
frontal_score decreases
quality.ready becomes false before identity enrollment is permitted
```

### Motion blur

Move the head rapidly.

Expected:

```text
sharpness decreases
blurred observations are not accepted for enrollment
```

### Lighting

Test a dark face and a strongly backlit face.

Expected:

```text
brightness gate rejects clearly unsuitable observations
```

### Track linkage

Temporarily cover the face while keeping the body visible.

Expected:

```text
person track remains stable
face can disappear without deleting the person track
face is linked back to the same track when visible again
```

## 9. Consent-based enrollment

Enrollment is never automatic. Ask the participant for explicit permission before executing
one of the following operations.

### Enrollment from the live window

Launch with a name:

```powershell
.\scripts\run_phase1234_live_view.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe" `
  -EnrollName "Rico"
```

Wait until:

```text
the intended person is the Primary track
face quality is ready
the participant has explicitly consented
```

Then press `E` once.

### Enrollment from PowerShell

Find the current Primary ID:

```powershell
(Invoke-RestMethod http://127.0.0.1:8015/api/v1/tracks).primary_track_id
```

Then enroll, replacing the track ID:

```powershell
.\scripts\manage_identity.ps1 `
  -Action enroll `
  -TrackId 1 `
  -DisplayName "Rico" `
  -Consent `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

The service stores normalized face embeddings and consent metadata. It does not save face
images.

List identities:

```powershell
.\scripts\manage_identity.ps1 `
  -Action list `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

Delete an identity by its returned `identity_id`:

```powershell
.\scripts\manage_identity.ps1 `
  -Action delete `
  -IdentityId "person_replace_with_actual_id" `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

## 10. Recognition test

After enrollment:

1. leave the camera view for longer than the Phase 2 track timeout;
2. return so a new anonymous `track_id` is created;
3. face the camera under similar lighting;
4. wait for three accepted identity observations.

Expected:

```text
new anonymous track_id
same enrolled identity_id and display_name
visitor_identified event emitted once for that track
```

Recognition also requires the best identity score to exceed the configured cosine threshold
and to beat the second-best candidate by the configured minimum margin.

## 11. Identity endpoints

```text
GET    /api/v1/faces
GET    /api/v1/identities
POST   /api/v1/identities/enroll
DELETE /api/v1/identities/{identity_id}
```

Identity data file:

```text
data/identity/visitor_identities.sqlite3
```

This path is ignored by Git. Back it up or delete it according to the exhibition operator's
consent and retention procedure.

## 12. Acceptance boundary

Phase 3 is accepted after:

- YuNet model loads;
- a visible face is associated with the correct person track;
- face quality reacts correctly to blur, lighting and head pose;
- short face disappearance does not corrupt the person track;
- `run_phase34_smoke_test.ps1 -RequireFace` passes.

Phase 4 is accepted after:

- explicit-consent enrollment succeeds;
- no face image is written to the identity database;
- the participant leaves long enough to obtain a new track ID;
- the returning participant is assigned the enrolled local identity;
- deletion removes the identity and its samples;
- an unregistered participant remains unknown.

Two-person crossing, similar-looking visitors, and Primary hysteresis remain deferred until
multiple participants are available. Those tests should be repeated with Phase 3-4 enabled
because identity evidence can make ID-switch errors easier to diagnose.
