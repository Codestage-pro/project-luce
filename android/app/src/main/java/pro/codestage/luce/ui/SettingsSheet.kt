package pro.codestage.luce.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.RadioButton
import androidx.compose.material3.RadioButtonDefaults
import androidx.compose.material3.SheetState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import pro.codestage.luce.LuceApi
import pro.codestage.luce.LuceConfig
import pro.codestage.luce.LuceLog
import pro.codestage.luce.UserSettings
import java.util.UUID

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsSheet(
    sheetState: SheetState,
    initialSettings: UserSettings,
    onSave: (UserSettings) -> Unit,
    onThemeChange: ((String) -> Unit)? = null,
    onDismiss: () -> Unit
) {
    var serverUrl by remember { mutableStateOf(initialSettings.serverUrl) }
    var password by remember { mutableStateOf(initialSettings.authPassword) }
    var showPassword by remember { mutableStateOf(false) }
    var selectedRes by remember { mutableStateOf(Pair(initialSettings.resolutionWidth, initialSettings.resolutionHeight)) }
    var selectedFps by remember { mutableIntStateOf(initialSettings.fps) }
    var selectedBitrate by remember { mutableIntStateOf(initialSettings.bitrateKbps) }
    var selectedAudioBitrate by remember { mutableIntStateOf(initialSettings.audioBitrateKbps) }
    var selectedAudio by remember { mutableStateOf(initialSettings.audioSource) }
    var selectedTheme by remember { mutableStateOf(initialSettings.theme) }
    var discordToken by remember { mutableStateOf(initialSettings.discordToken) }
    var discordUser by remember { mutableStateOf(initialSettings.discordUser) }
    var discordAvatar by remember { mutableStateOf(initialSettings.discordAvatar) }
    var currentUserName by remember { mutableStateOf(initialSettings.userName) }
    var isDiscordPolling by remember { mutableStateOf(false) }
    var pollTarget by remember { mutableStateOf<Pair<String, String>?>(null) } // (url do servidor, state) do login em andamento
    val pollMutex = remember { Mutex() }
    var discordStatusMsg by remember { mutableStateOf("") }
    var showLogs by remember { mutableStateOf(false) }

    val context = LocalContext.current
    val coroutineScope = rememberCoroutineScope()

    // Uma consulta ao /auth/discord/poll. O servidor entrega o resultado uma única vez, então o Mutex impede
    // que o loop de 2s e o ON_RESUME disputem a resposta. Retorna true quando o login terminou (ok ou negado).
    suspend fun pollDiscordOnce(): Boolean = pollMutex.withLock {
        val (cleanUrl, state) = pollTarget ?: return@withLock true
        val res = withContext(Dispatchers.IO) { LuceApi.pollDiscordAuth(cleanUrl, state) }
        when (res.status) {
            "ok" -> {
                pollTarget = null
                discordToken = res.token
                discordUser = res.discordUser
                discordAvatar = res.avatar
                if (res.user.isNotBlank()) currentUserName = LuceConfig.cleanName(res.user)
                isDiscordPolling = false
                discordStatusMsg = "✓ Conectado como @${res.discordUser}"
                LuceConfig.saveDiscordAuth(context, res.token, res.discordUser, res.user, res.avatar)
                // a MainActivity já fica autenticada, mesmo que o sheet seja fechado sem "Salvar"
                onSave(initialSettings.copy(
                    userName = currentUserName,
                    discordToken = discordToken,
                    discordUser = discordUser,
                    discordAvatar = discordAvatar
                ))
                true
            }
            "denied" -> {
                pollTarget = null
                isDiscordPolling = false
                discordStatusMsg = "Acesso negado: conta não pertence ao BDK."
                Toast.makeText(context, "Acesso negado: conta não pertence ao BDK.", Toast.LENGTH_LONG).show()
                true
            }
            else -> false
        }
    }

    // Voltando do navegador: consulta na hora, sem esperar o próximo ciclo de 2s
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME && isDiscordPolling) {
                coroutineScope.launch { pollDiscordOnce() }
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = LuceCard,
        contentColor = LuceText
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 24.dp)
                .verticalScroll(rememberScrollState())
                .padding(bottom = 32.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = "Configurações",
                    fontSize = 20.sp,
                    fontWeight = FontWeight.Bold,
                    color = LuceCyan
                )
                IconButton(onClick = onDismiss) {
                    Icon(Icons.Default.Close, contentDescription = "Fechar", tint = LuceMuted)
                }
            }

            Spacer(modifier = Modifier.height(16.dp))

            // Senha do Grupo
            OutlinedTextField(
                value = password,
                onValueChange = { password = it },
                label = { Text("Senha do Grupo (BDK)") },
                placeholder = { Text("senha para transmitir/assistir") },
                singleLine = true,
                visualTransformation = if (showPassword) VisualTransformation.None else PasswordVisualTransformation(),
                trailingIcon = {
                    IconButton(onClick = { showPassword = !showPassword }) {
                        Icon(
                            if (showPassword) Icons.Default.VisibilityOff else Icons.Default.Visibility,
                            contentDescription = if (showPassword) "Ocultar" else "Mostrar",
                            tint = LuceMuted
                        )
                    }
                },
                modifier = Modifier.fillMaxWidth(),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = LuceCyan,
                    unfocusedBorderColor = LuceBorder,
                    focusedLabelColor = LuceCyan,
                    unfocusedLabelColor = LuceMuted,
                    focusedTextColor = LuceText,
                    unfocusedTextColor = LuceText
                )
            )

            Spacer(modifier = Modifier.height(16.dp))

            // Autenticação Discord (Membros BDK)
            Text("Acesso Discord (Membros BDK)", color = LuceCyan, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            Spacer(modifier = Modifier.height(8.dp))

            if (discordToken.isNotBlank()) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(8.dp))
                        .background(LuceCardElevated)
                        .border(1.dp, LuceSuccess.copy(alpha = 0.5f), RoundedCornerShape(8.dp))
                        .padding(12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            text = "✓ Conectado como @${discordUser.ifBlank { "membro" }}",
                            color = LuceSuccess,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Bold
                        )
                        Text(
                            text = "Token assinado ativo",
                            color = LuceMuted,
                            fontSize = 11.sp
                        )
                    }
                    Button(
                        onClick = {
                            LuceConfig.clearDiscordAuth(context)
                            discordToken = ""
                            discordUser = ""
                            discordAvatar = ""
                            discordStatusMsg = ""
                            Toast.makeText(context, "Desconectado do Discord", Toast.LENGTH_SHORT).show()
                        },
                        colors = ButtonDefaults.buttonColors(
                            containerColor = LuceError.copy(alpha = 0.2f),
                            contentColor = LuceError
                        ),
                        shape = RoundedCornerShape(6.dp)
                    ) {
                        Text("Sair", fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                    }
                }
            } else {
                Button(
                    onClick = {
                        val state = UUID.randomUUID().toString().replace("-", "")
                        val cleanUrl = serverUrl.trim().trimEnd('/')
                        val loginUrl = "$cleanUrl/auth/discord/login?state=$state"
                        isDiscordPolling = true
                        discordStatusMsg = "Aguardando autorização no navegador..."
                        try {
                            val intent = Intent(Intent.ACTION_VIEW, Uri.parse(loginUrl))
                            context.startActivity(intent)
                        } catch (e: Exception) {
                            isDiscordPolling = false
                            discordStatusMsg = "Erro ao abrir navegador: ${e.message}"
                            return@Button
                        }

                        pollTarget = cleanUrl to state
                        coroutineScope.launch {
                            var elapsed = 0
                            while (elapsed < 120 && pollTarget != null) {
                                delay(2000)
                                elapsed += 2
                                if (pollDiscordOnce()) return@launch
                            }
                            if (pollTarget != null) {
                                pollTarget = null
                                isDiscordPolling = false
                                discordStatusMsg = "Tempo limite de login esgotado."
                            }
                        }
                    },
                    enabled = !isDiscordPolling,
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(44.dp),
                    shape = RoundedCornerShape(8.dp),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = LucePurple,
                        contentColor = Color.White
                    )
                ) {
                    if (isDiscordPolling) {
                        CircularProgressIndicator(modifier = Modifier.size(18.dp), color = Color.White, strokeWidth = 2.dp)
                        Spacer(modifier = Modifier.width(8.dp))
                        Text("Aguardando Discord...", fontSize = 13.sp)
                    } else {
                        Text("Entrar com Discord (BDK)", fontSize = 13.sp, fontWeight = FontWeight.Bold)
                    }
                }
                if (discordStatusMsg.isNotBlank()) {
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(text = discordStatusMsg, color = LuceMuted, fontSize = 11.sp)
                }
            }

            Spacer(modifier = Modifier.height(16.dp))

            // Tema Visual (Homenagem BDK)
            Text("Tema Visual (Homenagem BDK)", color = LuceCyan, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            Spacer(modifier = Modifier.height(8.dp))
            val themeOptions = listOf(
                Triple(UserSettings.THEME_LUCE, "Padrão LUCE", Color(0xFF00E5FF)),
                Triple(UserSettings.THEME_MURILO, "MuriloX06", Color(0xFFA855F7)),
                Triple(UserSettings.THEME_TIRIGAM, "Tirigam", Color(0xFFFF1744)),
                Triple(UserSettings.THEME_NINJA_SOM, "Ninja / SOM", Color(0xFF3B82F6)),
                Triple(UserSettings.THEME_DARK, "Dark OLED", Color(0xFFE4E4E7))
            )
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                themeOptions.take(3).forEach { (key, label, accentColor) ->
                    ThemeChoiceButton(
                        label = label,
                        accentColor = accentColor,
                        isSelected = selectedTheme == key,
                        modifier = Modifier.weight(1f)
                    ) {
                        selectedTheme = key
                        onThemeChange?.invoke(key)
                    }
                }
            }
            Spacer(modifier = Modifier.height(6.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                themeOptions.drop(3).forEach { (key, label, accentColor) ->
                    ThemeChoiceButton(
                        label = label,
                        accentColor = accentColor,
                        isSelected = selectedTheme == key,
                        modifier = Modifier.weight(1f)
                    ) {
                        selectedTheme = key
                        onThemeChange?.invoke(key)
                    }
                }
            }

            Spacer(modifier = Modifier.height(16.dp))

            // Resolução
            Text("Resolução", color = LuceCyan, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            Spacer(modifier = Modifier.height(8.dp))
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(
                    Pair(1280, 720) to "720p",
                    Pair(1920, 1080) to "1080p",
                    Pair(2560, 1440) to "1440p"
                ).forEach { (res, label) ->
                    ChoiceButton(
                        label = label,
                        isSelected = selectedRes == res,
                        modifier = Modifier.weight(1f)
                    ) { selectedRes = res }
                }
            }

            Spacer(modifier = Modifier.height(16.dp))

            // Taxa de Quadros (FPS)
            Text("Taxa de Quadros (FPS)", color = LuceCyan, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            Spacer(modifier = Modifier.height(8.dp))
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(30 to "30 FPS", 60 to "60 FPS").forEach { (fps, label) ->
                    ChoiceButton(
                        label = label,
                        isSelected = selectedFps == fps,
                        modifier = Modifier.weight(1f)
                    ) { selectedFps = fps }
                }
            }

            Spacer(modifier = Modifier.height(16.dp))

            // Bitrate
            Text("Taxa de Bits (Qualidade)", color = LuceCyan, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            Spacer(modifier = Modifier.height(8.dp))
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(2500 to "2.5M", 4000 to "4M", 8000 to "8M", 16000 to "16M").forEach { (bitrate, label) ->
                    ChoiceButton(
                        label = label,
                        isSelected = selectedBitrate == bitrate,
                        modifier = Modifier.weight(1f)
                    ) { selectedBitrate = bitrate }
                }
            }

            Spacer(modifier = Modifier.height(16.dp))

            // Fonte de Áudio
            Text("Fonte de Áudio na Transmissão", color = LuceCyan, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            Spacer(modifier = Modifier.height(8.dp))
            Column(modifier = Modifier.fillMaxWidth()) {
                AudioOptionRow(
                    label = "Áudio interno do sistema (jogos e apps)",
                    selected = selectedAudio == UserSettings.AUDIO_SOURCE_SYSTEM,
                    onClick = { selectedAudio = UserSettings.AUDIO_SOURCE_SYSTEM }
                )
                AudioOptionRow(
                    label = "Mudo (sem áudio)",
                    selected = selectedAudio == UserSettings.AUDIO_SOURCE_MUTED,
                    onClick = { selectedAudio = UserSettings.AUDIO_SOURCE_MUTED }
                )
            }

            Spacer(modifier = Modifier.height(16.dp))

            // Qualidade de Áudio
            Text("Qualidade de Áudio", color = LuceCyan, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            Spacer(modifier = Modifier.height(8.dp))
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(
                    64 to "64k",
                    128 to "128k",
                    256 to "256k",
                    320 to "320k"
                ).forEach { (ab, label) ->
                    ChoiceButton(
                        label = label,
                        isSelected = selectedAudioBitrate == ab,
                        modifier = Modifier.weight(1f)
                    ) { selectedAudioBitrate = ab }
                }
            }

            Spacer(modifier = Modifier.height(16.dp))

            // Diagnóstico
            Text("Diagnóstico", color = LuceCyan, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            Spacer(modifier = Modifier.height(8.dp))
            Button(
                onClick = { showLogs = true },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(44.dp)
                    .border(1.dp, LuceBorder, RoundedCornerShape(8.dp)),
                shape = RoundedCornerShape(8.dp),
                colors = ButtonDefaults.buttonColors(containerColor = LuceCard, contentColor = LuceText)
            ) {
                Text("Abrir / Copiar Logs do App", fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
            }

            Spacer(modifier = Modifier.height(24.dp))

            // Salvar
            Button(
                onClick = {
                    onSave(
                        initialSettings.copy(
                            serverUrl = serverUrl.trim(),
                            authPassword = password.trim(),
                            resolutionWidth = selectedRes.first,
                            resolutionHeight = selectedRes.second,
                            fps = selectedFps,
                            bitrateKbps = selectedBitrate,
                            audioBitrateKbps = selectedAudioBitrate,
                            audioSource = selectedAudio,
                            theme = selectedTheme,
                            userName = currentUserName,
                            discordToken = discordToken,
                            discordUser = discordUser,
                            discordAvatar = discordAvatar
                        )
                    )
                    onDismiss()
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp),
                shape = RoundedCornerShape(8.dp),
                colors = ButtonDefaults.buttonColors(containerColor = LuceCyan, contentColor = LuceNight)
            ) {
                Text("Salvar Configurações", fontWeight = FontWeight.Bold, fontSize = 15.sp)
            }
        }
        if (showLogs) LogViewerDialog(onDismiss = { showLogs = false })
    }
}

@Composable
private fun LogViewerDialog(onDismiss: () -> Unit) {
    val context = LocalContext.current
    var logText by remember { mutableStateOf(LuceLog.readLogs()) }
    val scroll = rememberScrollState()

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false)
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(12.dp)
                .clip(RoundedCornerShape(12.dp))
                .background(LuceCard)
                .border(1.dp, LuceBorder, RoundedCornerShape(12.dp))
                .padding(12.dp)
        ) {
            Text("Logs do App", color = LuceCyan, fontSize = 16.sp, fontWeight = FontWeight.Bold)
            Spacer(modifier = Modifier.height(8.dp))
            Box(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(8.dp))
                    .background(LuceNight)
                    .border(1.dp, LuceBorder, RoundedCornerShape(8.dp))
                    .verticalScroll(scroll)
                    .padding(8.dp)
            ) {
                Text(logText, color = LuceText, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
            }
            Spacer(modifier = Modifier.height(8.dp))
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                val buttonColors = ButtonDefaults.buttonColors(containerColor = LuceCardElevated, contentColor = LuceText)
                val pad = androidx.compose.foundation.layout.PaddingValues(horizontal = 4.dp)
                Button(
                    onClick = { logText = LuceLog.readLogs() },
                    modifier = Modifier.weight(1f),
                    contentPadding = pad,
                    shape = RoundedCornerShape(8.dp),
                    colors = buttonColors
                ) { Text("Atualizar", fontSize = 12.sp, maxLines = 1) }
                Button(
                    onClick = {
                        val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                        cm.setPrimaryClip(ClipData.newPlainText("Logs do LUCE", logText))
                        Toast.makeText(context, "Logs copiados para a área de transferência!", Toast.LENGTH_SHORT).show()
                    },
                    modifier = Modifier.weight(1f),
                    contentPadding = pad,
                    shape = RoundedCornerShape(8.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = LuceCyan, contentColor = LuceNight)
                ) { Text("Copiar Logs", fontSize = 12.sp, maxLines = 1) }
                Button(
                    onClick = {
                        LuceLog.clearLogs()
                        logText = LuceLog.readLogs()
                    },
                    modifier = Modifier.weight(1f),
                    contentPadding = pad,
                    shape = RoundedCornerShape(8.dp),
                    colors = buttonColors
                ) { Text("Limpar", fontSize = 12.sp, maxLines = 1) }
                Button(
                    onClick = onDismiss,
                    modifier = Modifier.weight(1f),
                    contentPadding = pad,
                    shape = RoundedCornerShape(8.dp),
                    colors = buttonColors
                ) { Text("Fechar", fontSize = 12.sp, maxLines = 1) }
            }
        }
    }
}

@Composable
fun ThemeChoiceButton(
    label: String,
    accentColor: Color,
    isSelected: Boolean,
    modifier: Modifier = Modifier,
    onClick: () -> Unit
) {
    Box(
        modifier = modifier
            .clip(RoundedCornerShape(8.dp))
            .background(if (isSelected) LuceCardElevated else LuceNight)
            .border(
                width = if (isSelected) 2.dp else 1.dp,
                color = if (isSelected) accentColor else LuceBorder,
                shape = RoundedCornerShape(8.dp)
            )
            .clickable(onClick = onClick)
            .padding(vertical = 10.dp, horizontal = 4.dp),
        contentAlignment = Alignment.Center
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.Center
        ) {
            Box(
                modifier = Modifier
                    .size(8.dp)
                    .background(accentColor, CircleShape)
            )
            Spacer(modifier = Modifier.width(6.dp))
            Text(
                text = label,
                color = if (isSelected) Color.White else LuceMuted,
                fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Normal,
                fontSize = 11.sp,
                maxLines = 1
            )
        }
    }
}

@Composable
fun ChoiceButton(
    label: String,
    isSelected: Boolean,
    modifier: Modifier = Modifier,
    onClick: () -> Unit
) {
    Box(
        modifier = modifier
            .clip(RoundedCornerShape(8.dp))
            .background(if (isSelected) LucePurple else LuceCardElevated)
            .clickable(onClick = onClick)
            .padding(vertical = 10.dp),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text = label,
            color = if (isSelected) Color.White else LuceMuted,
            fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Normal,
            fontSize = 13.sp
        )
    }
}

@Composable
fun AudioOptionRow(
    label: String,
    selected: Boolean,
    onClick: () -> Unit
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        RadioButton(
            selected = selected,
            onClick = onClick,
            colors = RadioButtonDefaults.colors(selectedColor = LuceCyan, unselectedColor = LuceBorder)
        )
        Spacer(modifier = Modifier.width(8.dp))
        Text(text = label, color = LuceText, fontSize = 14.sp)
    }
}
