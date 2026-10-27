package pro.codestage.luce

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioManager
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import pro.codestage.luce.ui.LuceTheme
import pro.codestage.luce.ui.MainScreen

class MainActivity : ComponentActivity() {

    private lateinit var mediaProjectionManager: MediaProjectionManager
    private lateinit var audioManager: AudioManager

    private var settings by mutableStateOf(UserSettings())
    private var userName by mutableStateOf("")
    private var isStreaming by mutableStateOf(false)
    private var streamStatusText by mutableStateOf("pronto para transmitir")
    private var streamStatsText by mutableStateOf("")

    private val rosterList = mutableStateListOf<RosterItem>()
    private var isRosterLoading by mutableStateOf(false)

    private val watchedSessions = mutableStateListOf<WatchSession>()
    private var fullscreenStream by mutableStateOf<String?>(null)

    private var updateInfo by mutableStateOf<UpdateInfo?>(null)
    private var isDownloadingUpdate by mutableStateOf(false)
    private var updateDownloadProgress by mutableStateOf(-1f)
    private var pendingStartStreamAfterPermission = false

    private val screenCaptureLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK && result.data != null) {
            startScreenCaptureService(result.data!!)
        } else {
            Toast.makeText(this, "Permissão de captura de tela recusada", Toast.LENGTH_SHORT).show()
        }
    }

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { permissions ->
        if (pendingStartStreamAfterPermission) {
            pendingStartStreamAfterPermission = false
            if (permissions[Manifest.permission.RECORD_AUDIO] == true ||
                ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
            ) {
                val captureIntent = mediaProjectionManager.createScreenCaptureIntent()
                screenCaptureLauncher.launch(captureIntent)
            } else {
                Toast.makeText(this, "Permissão de áudio necessária para transmitir", Toast.LENGTH_LONG).show()
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        mediaProjectionManager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        audioManager = getSystemService(Context.AUDIO_SERVICE) as AudioManager

        settings = LuceConfig.load(this)
        userName = settings.userName.ifBlank { LuceConfig.getOrGenerateUserName(this) }

        requestInitialPermissions()

        setContent {
            LuceTheme {
                MainScreen(
                    settings = settings,
                    userName = userName,
                    onUserNameChange = { newName ->
                        userName = newName
                        LuceConfig.saveUserName(this, newName)
                    },
                    isStreaming = isStreaming,
                    streamStatusText = streamStatusText,
                    streamStatsText = streamStatsText,
                    onStartStreamClick = { handleStartStream() },
                    onStopStreamClick = { handleStopStream() },
                    rosterList = rosterList,
                    isRosterLoading = isRosterLoading,
                    onRefreshRoster = { fetchRoster() },
                    watchedSessions = watchedSessions,
                    fullscreenStream = fullscreenStream,
                    onToggleFullscreen = { streamName -> fullscreenStream = streamName },
                    onToggleWatchStream = { streamName -> handleToggleWatch(streamName) },
                    onCloseStream = { session -> handleCloseWatch(session) },
                    updateInfo = updateInfo,
                    isDownloadingUpdate = isDownloadingUpdate,
                    updateDownloadProgress = updateDownloadProgress,
                    onUpdateClick = { info -> handleStartUpdate(info) },
                    onSaveSettings = { newSettings ->
                        settings = newSettings
                        LuceConfig.save(this, newSettings)
                        Toast.makeText(this, "Configurações salvas!", Toast.LENGTH_SHORT).show()
                        fetchRoster()
                    }
                )
            }
        }

        startRosterPolling()
        startStreamStatusWatcher()
        checkForUpdates()
    }

    private fun requestInitialPermissions() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                permissionLauncher.launch(arrayOf(Manifest.permission.POST_NOTIFICATIONS))
            }
        }
    }

    private fun handleStartStream() {
        val clean = LuceConfig.cleanName(userName)
        if (clean.isBlank()) {
            Toast.makeText(this, "Digite um nome válido antes de transmitir", Toast.LENGTH_SHORT).show()
            return
        }
        LuceConfig.saveUserName(this, clean)
        userName = clean

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            pendingStartStreamAfterPermission = true
            permissionLauncher.launch(arrayOf(Manifest.permission.RECORD_AUDIO))
            return
        }

        val captureIntent = mediaProjectionManager.createScreenCaptureIntent()
        screenCaptureLauncher.launch(captureIntent)
    }

    private fun startScreenCaptureService(resultData: Intent) {
        val serviceIntent = Intent(this, ScreenCaptureService::class.java).apply {
            action = ScreenCaptureService.ACTION_START
            putExtra(ScreenCaptureService.EXTRA_RESULT_DATA, resultData)
        }
        ContextCompat.startForegroundService(this, serviceIntent)
    }

    private fun handleStopStream() {
        ScreenCaptureService.stop(this)
        isStreaming = false
        streamStatusText = "transmissão encerrada"
        streamStatsText = ""
    }

    private fun handleToggleWatch(streamName: String) {
        val existing = watchedSessions.find { it.streamName == streamName }
        if (existing != null) {
            handleCloseWatch(existing)
            return
        }

        // Normaliza e salva nome de viewer
        val myName = LuceConfig.getOrGenerateUserName(this)
        userName = myName
        LuceConfig.saveUserName(this, myName)

        val session = WatchSession(
            context = this,
            streamName = streamName,
            baseUrl = settings.serverUrl,
            myViewerName = myName,
            authToken = settings.authToken
        )
        watchedSessions.add(session)
        session.start()

        updateAudioRouting()
    }

    private fun handleCloseWatch(session: WatchSession) {
        session.stop()
        watchedSessions.remove(session)
        if (fullscreenStream == session.streamName) {
            fullscreenStream = null
        }
        updateAudioRouting()
    }

    private fun updateAudioRouting() {
        audioManager.mode = AudioManager.MODE_NORMAL
    }

    private fun startStreamStatusWatcher() {
        lifecycleScope.launch {
            while (isActive) {
                delay(1000)
                val session = ScreenCaptureService.currentSession
                if (session != null) {
                    isStreaming = session.isLive.value
                    streamStatusText = session.statusText.value
                    streamStatsText = session.statsText.value
                } else if (isStreaming) {
                    isStreaming = false
                    streamStatusText = "pronto para transmitir"
                    streamStatsText = ""
                }
            }
        }
    }

    private fun startRosterPolling() {
        lifecycleScope.launch {
            while (isActive) {
                fetchRoster()
                delay(3000)
            }
        }
    }

    private fun fetchRoster() {
        lifecycleScope.launch(Dispatchers.IO) {
            isRosterLoading = true
            try {
                val items = LuceApi.listPaths(settings.serverUrl, settings.authToken)
                withContext(Dispatchers.Main) {
                    rosterList.clear()
                    rosterList.addAll(items.filter { it.ready })
                    isRosterLoading = false
                }
            } catch (_: Exception) {
                withContext(Dispatchers.Main) {
                    isRosterLoading = false
                }
            }
        }
    }

    private fun checkForUpdates() {
        lifecycleScope.launch {
            try {
                val info = Updater.checkUpdate(BuildConfig.VERSION_NAME)
                if (info != null) {
                    updateInfo = info
                }
            } catch (_: Exception) {}
        }
    }

    private fun handleStartUpdate(info: UpdateInfo) {
        if (isDownloadingUpdate) return
        isDownloadingUpdate = true
        updateDownloadProgress = 0f
        lifecycleScope.launch {
            Updater.downloadAndInstall(
                context = this@MainActivity,
                downloadUrl = info.downloadUrl,
                onProgress = { progress ->
                    updateDownloadProgress = progress
                },
                onError = { err ->
                    isDownloadingUpdate = false
                    updateDownloadProgress = -1f
                    Toast.makeText(this@MainActivity, err, Toast.LENGTH_LONG).show()
                }
            )
            isDownloadingUpdate = false
        }
    }

    override fun onDestroy() {
        watchedSessions.forEach { it.stop() }
        watchedSessions.clear()
        updateAudioRouting()
        super.onDestroy()
    }
}
