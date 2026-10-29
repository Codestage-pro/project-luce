# Project LUCE v1.0.34 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Discord avatars on live roster, log viewer close button, MinGW WGC linker fix, bump to v1.0.34, audit against SSRF, and release on Linux, Windows, and Android.

**Architecture:** 
- MinGW C++: Define `IID_IGraphicsCaptureAccessStatics` explicitly as GUID constant and call `RoGetActivationFactory` directly.
- Server (`sfu_proxy.py`): Extract Discord avatars from OAuth/API, track `user_avatars`, validate `X-Luce-Avatar` strictly to `cdn.discordapp.com`, and serve via `/v3/paths/list`.
- Desktop (`luce/`): Add close button + Escape handler in `LogViewerWindow`. Cache Discord avatar textures asynchronously in GTK4.
- Android (`android/`): Add `discordAvatar` persistence, fetch avatar bitmaps into `LruCache`, and display in `RosterItemCard`.

**Tech Stack:** C++ (Direct3D11 / WinRT WGC), Python 3 (GTK4 / GStreamer / HTTP), Kotlin / Jetpack Compose / WebRTC, GitHub Actions CI.

## Global Constraints
- Python syntax must compile across all files (`python3 -m py_compile`).
- Server unit tests must pass (`python3 -m unittest server/test_sfu_proxy.py`).
- Android release APK must build cleanly (`./gradlew assembleRelease`).
- Strictly enforce `https://cdn.discordapp.com/` for avatars to prevent SSRF vulnerabilities.
- Complete code without placeholders or `TODO`s.

---

### Task 1: MinGW WGC Linker Fix in `luce/windows/wgc_capture.cpp`
- [ ] Define `IID_IGraphicsCaptureAccessStatics` as a constant GUID.
- [ ] Replace `GetActivationFactory` call for `IGraphicsCaptureAccessStatics` with direct `RoGetActivationFactory`.

### Task 2: Log Viewer Close Button & Escape Key in `luce/gui.py`
- [ ] Add explicit "Fechar" button (`css_classes=["stop"]`) connected to `self.close()`.
- [ ] Add `Gtk.EventControllerKey` listening for Escape key to close the window.

### Task 3: Discord Profile Avatars on Server (`server/sfu_proxy.py`)
- [ ] Define `user_avatars: dict[str, str]` and `_build_discord_avatar_url`.
- [ ] Add SSRF validator `_is_valid_discord_avatar_url`.
- [ ] Populate `user_avatars` during Discord OAuth callback, membership check, and online user recording.
- [ ] Return `"avatar"` in each item and `"avatars"` in root JSON of `/v3/paths/list`.
- [ ] Add tests in `server/test_sfu_proxy.py` verifying avatar building, SSRF rejection, and roster response.

### Task 4: Discord Profile Avatars on Desktop (`luce/`)
- [ ] Add `"discord_avatar": ""` to `luce/config.py`.
- [ ] In `luce/roster.py`: send `X-Luce-Avatar`, update `LAST_AVATARS`, export `get_avatar_url`.
- [ ] In `luce/gui.py`: persist `discord_avatar` on login, implement async avatar texture downloader with strict SSRF check and GTK4 picture integration.

### Task 5: Discord Profile Avatars on Android (`android/`)
- [ ] In `LuceConfig.kt`: add `discordAvatar` to `UserSettings` with SharedPreferences persistence.
- [ ] In `LuceApi.kt`: parse `avatarUrl` in `RosterItem` and `DiscordPollResult`, send `X-Luce-Avatar` header.
- [ ] In `MainScreen.kt`: implement `DiscordAvatar` composable with LRU cache and fallback initial circle, integrate into `RosterItemCard`.

### Task 6: Version Bump to 1.0.34, Full Validation & Release
- [ ] Bump versions in `luce/updater.py`, `PKGBUILD`, `packaging/pro.codestage.Luce.metainfo.xml`, `packaging/windows/luce.iss`, `android/app/build.gradle.kts`, and CI workflows.
- [ ] Run python compilation and server unit tests.
- [ ] Compile Android release APK locally.
- [ ] Run `graphify update .`.
- [ ] Commit, tag `v1.0.34` & `android-v1.0.34`, and push.
