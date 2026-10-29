# Project LUCE v1.0.33 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Windows WGC capture fixes (borderless & dynamic resize without clipping), 320 kbps audio quality, live Discord BDK membership revocation, extinguish android-test prerelease, stylized online users counter, native log viewer for X11, run security audit (Cloudflare + Strix), and publish v1.0.33 across all 3 platforms.

**Architecture:** Native C++/WinRT enhancement for Windows WGC capture; multi-platform GStreamer & WebRTC audio bitrate bump to 320 kbps; HMAC-SHA256 authenticated cipher for tokenized Discord state with 15s-cached live API guild verification on SFU proxy; real-time active users aggregation on `/v3/paths/list`; GTK4 native log viewer window fallback on Linux; streamlined Android CI publishing directly to `android-latest`.

**Tech Stack:** Python 3, GTK4/GStreamer, C++ Direct3D11 / WinRT WGC, Android Jetpack Compose / WebRTC, GitHub Actions CI.

## Global Constraints
- Write COMPLETE code for all modified files without `TODO` or placeholders.
- Secrets (`auth_password`, `discord_client_secret`) must never be leaked, logged, or exposed.
- All git commits must conclude with `Co-Authored-By: Claude Code <noreply@anthropic.com>`.
- Always run `graphify update .` after changes.

---

### Task 1: Windows Window Capture (Zero Yellow Border & Zero Fullscreen Cropping)
**Files:**
- Modify: `luce/windows/wgc_capture.cpp`
- Modify: `luce/windows/winsys.py`
- Modify: `luce/capture_pipeline.py`

- [ ] **Step 1:** In `luce/windows/wgc_capture.cpp`:
  - Define `IGraphicsCaptureAccessStatics` interface and call `RequestAccessAsync(GraphicsCaptureAccessKind_Borderless)`.
  - Check `put_IsBorderRequired(FALSE)` and set `borderless_supported = 1`. Export `luce_wgc_is_borderless`.
  - In `luce_wgc_grab_frame`, read `frame->get_ContentSize` and `GetClientRect` each frame; recreate frame pool and staging texture if window resized. Copy only `min(desc.Width, target_w)` x `min(desc.Height, target_h)`.
- [ ] **Step 2:** In `luce/windows/winsys.py`:
  - Update `WgcGrabber` with `is_borderless` method.
  - In `WindowGrabber.__init__`, test `_GdiWindowGrabber` first with `PW_RENDERFULLCONTENT`. If valid non-black frame, use it (zero yellow border). If black (DirectX game), use `WgcGrabber`. If WGC is not borderless, prefer GDI.
- [ ] **Step 3:** In `luce/capture_pipeline.py`:
  - Set `_try_set(src, "show-border", False)` on `d3d11screencapturesrc`.
  - For windows with "Fonte" (resolution 0, 0), set encoder output resolution to the monitor's dimensions where the window resides instead of the initial window rect.
  - In `conv` / `scale`, set `add-borders=True` and update `appsrc` caps whenever `grabber.grab()` returns new dimensions.
- [ ] **Step 4:** Verify python syntax and types with `py_compile`.

---

### Task 2: 320 kbps Audio Quality (Desktop & Android)
**Files:**
- Modify: `luce/gui.py`
- Modify: `luce/capture_pipeline.py`
- Modify: `android/app/src/main/java/pro/codestage/luce/LuceConfig.kt`
- Modify: `android/app/src/main/java/pro/codestage/luce/ui/SettingsSheet.kt`
- Modify: `android/app/src/main/java/pro/codestage/luce/WebRtcEngine.kt`
- Modify: `android/app/src/main/java/pro/codestage/luce/LuceApi.kt`

- [ ] **Step 1:** In `luce/gui.py`, add 320 to `AUDIO_BITRATES_KBPS` (`[64, 128, 256, 320]`).
- [ ] **Step 2:** In `luce/capture_pipeline.py`, ensure opusenc bitrate supports up to 320000 bps.
- [ ] **Step 3:** In Android `LuceConfig.kt`, add `audioBitrateKbps: Int = 128` to `UserSettings` with persistent load/save.
- [ ] **Step 4:** In `SettingsSheet.kt`, add choice selector for 64, 128, 256, 320 kbps.
- [ ] **Step 5:** In `WebRtcEngine.kt` (`PublishSession.start`), set `aSender.parameters.encodings[0].maxBitrateBps = settings.audioBitrateKbps * 1000`.
- [ ] **Step 6:** In `LuceApi.kt` (`sanitizeSdpOffer`), append/update `maxaveragebitrate=${audioBitrateKbps * 1000};stereo=1;sprop-stereo=1` on the Opus `a=fmtp:` line.

---

### Task 3: Live Revocation on BDK Discord Guild Exit
**Files:**
- Modify: `server/sfu_proxy.py`
- Modify: `server/test_sfu_proxy.py`
- Modify: `luce/gui.py`
- Modify: `luce/capture_pipeline.py`
- Modify: `android/app/src/main/java/pro/codestage/luce/MainActivity.kt`
- Modify: `android/app/src/main/java/pro/codestage/luce/WebRtcEngine.kt`

- [ ] **Step 1:** In `server/sfu_proxy.py`:
  - Implement XOR stream cipher with SHA-256/HMAC for embedding encrypted `access_token` in Discord token: `discord:<user_id>:<username>:<exp>:<enc_access_token>:<sig>`.
  - Add thread-safe `discord_sessions` in memory.
  - Implement `check_bdk_membership_live(user_id, access_token)` with 15-second cache, Discord API `/users/@me/guilds` call, guild presence verification, unpublishing active streams if removed, and graceful handling of 429/network errors.
  - Call `check_bdk_membership_live` inside `_verify_discord_part`.
- [ ] **Step 2:** Add unit tests in `server/test_sfu_proxy.py` testing live membership check, token encryption, and 401 handling.
- [ ] **Step 3:** In `luce/gui.py` and `luce/capture_pipeline.py`, handle HTTP 401: stop sharing, clear saved Discord token, notify user, and open settings.
- [ ] **Step 4:** In Android `MainActivity.kt` and `WebRtcEngine.kt`, handle HTTP 401: stop streaming, clear Discord auth, show Toast, and trigger settings sheet.

---

### Task 4: Extinguish `android-test` Prerelease Permanently
**Files:**
- Modify: `.github/workflows/android-build.yml`
- Modify: `android/app/src/main/java/pro/codestage/luce/Updater.kt`

- [ ] **Step 1:** In `.github/workflows/android-build.yml`:
  - Remove `Move a tag android-test` and `Atualiza a Prerelease android-test` steps.
  - Ensure untagged branch pushes do not use `-probe.` and only upload artifacts.
  - Only official tags (`v*` and `android-v*`) update `android-latest`.
- [ ] **Step 2:** In `android/app/src/main/java/pro/codestage/luce/Updater.kt`:
  - Remove `ANDROID_TEST_URL`, `FALLBACK_ANDROID_TEST_URL` and all `android-test` checking.
  - In `isNewerVersion`: return `false` if version contains "probe" or "test". Compare strictly numeric segments; if equal, return `false`.
  - In `downloadAndInstall`: delete any existing `Luce-Android.apk` in Downloads before enqueueing.
- [ ] **Step 3:** Delete remote GitHub release and tag `android-test`.

---

### Task 5: Stylized Online Users Counter
**Files:**
- Modify: `server/sfu_proxy.py`
- Modify: `luce/roster.py`
- Modify: `luce/theme.py`
- Modify: `luce/gui.py`
- Modify: `android/app/src/main/java/pro/codestage/luce/LuceApi.kt`
- Modify: `android/app/src/main/java/pro/codestage/luce/MainActivity.kt`
- Modify: `android/app/src/main/java/pro/codestage/luce/ui/MainScreen.kt`

- [ ] **Step 1:** In `server/sfu_proxy.py`:
  - Add `online_users: dict[str, float]` with `ONLINE_TTL = 15`.
  - Track username from `Authorization` or `X-Luce-Viewer` header.
  - Return `online_count` and `online_users` in `GET /v3/paths/list`.
- [ ] **Step 2:** In `luce/roster.py`:
  - Add `fetch_state(base_url, token, viewer_name, timeout)` returning `(paths_dict, online_count, online_users)`.
  - Maintain `list_paths` compatibility.
- [ ] **Step 3:** In `luce/theme.py`:
  - Add `.online-pill` and `.online-dot` CSS rules.
- [ ] **Step 4:** In `luce/gui.py`:
  - Display online pill in header with green dot, "X online", and tooltip listing users.
- [ ] **Step 5:** In Android `LuceApi.kt`, `MainActivity.kt`, and `MainScreen.kt`:
  - Update API with `RosterResponse`.
  - Display styled online badge in `TopAppBar` with neon green dot and toast on tap.

---

### Task 6: Arch Linux X11 Logs Viewer & Folder Opener
**Files:**
- Modify: `luce/gui.py`

- [ ] **Step 1:** Implement `_open_logs_folder(parent_win: Gtk.Window)` in `luce/gui.py`.
- [ ] **Step 2:** Search for installed GUI file managers via `shutil.which` (`thunar`, `dolphin`, `nemo`, `nautilus`, `pcmanfm`, etc.).
- [ ] **Step 3:** Implement native `LogViewerWindow(Gtk.Window)` displaying full log directory path, "Copiar caminho", live tabs/views for `luce.log` and `gst.log` in monospace `Gtk.TextView`, "Copiar logs", and "Atualizar".
- [ ] **Step 4:** Wire "Abrir pasta de logs" button in `SettingsWindow` to `_open_logs_folder`.

---

### Task 7: Security Audit (Cloudflare + Strix), Version Bump & Release v1.0.33
**Files:**
- Modify: `luce/updater.py`
- Modify: `packaging/pro.codestage.Luce.metainfo.xml`
- Modify: `PKGBUILD`
- Modify: `packaging/windows/luce.iss`
- Modify: `android/app/build.gradle.kts`
- Modify: `.github/workflows/windows-build.yml`

- [ ] **Step 1:** Run Cloudflare security audit and Strix pentest modules against `server/sfu_proxy.py`, `luce/`, and `android/`. Fix any findings.
- [ ] **Step 2:** Bump version to `1.0.33` across all metadata files.
- [ ] **Step 3:** Run all unit tests (`test_sfu_proxy.py`), compile checks (`py_compile`), Android release build (`assembleRelease`), and `graphify update .`.
- [ ] **Step 4:** Commit to `main`, create tags `v1.0.33` and `android-v1.0.33`, and push to remote.
