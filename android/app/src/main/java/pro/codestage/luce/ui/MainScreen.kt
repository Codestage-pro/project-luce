package pro.codestage.luce.ui

import android.app.Activity
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.Image
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
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.SystemUpdate
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import pro.codestage.luce.R
import pro.codestage.luce.RosterItem
import pro.codestage.luce.UpdateInfo
import pro.codestage.luce.UserSettings
import pro.codestage.luce.WatchSession

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(
    settings: UserSettings,
    userName: String,
    onUserNameChange: (String) -> Unit,
    isStreaming: Boolean,
    streamStatusText: String,
    streamStatsText: String,
    onStartStreamClick: () -> Unit,
    onStopStreamClick: () -> Unit,
    rosterList: List<RosterItem>,
    onlineCount: Int = 0,
    onlineUsers: List<String> = emptyList(),
    isRosterLoading: Boolean,
    onRefreshRoster: () -> Unit,
    watchedSessions: List<WatchSession>,
    fullscreenStream: String?,
    onToggleFullscreen: (String?) -> Unit,
    onToggleWatchStream: (String) -> Unit,
    onCloseStream: (WatchSession) -> Unit,
    updateInfo: UpdateInfo?,
    isDownloadingUpdate: Boolean = false,
    updateDownloadProgress: Float = -1f,
    onUpdateClick: (UpdateInfo) -> Unit,
    onThemeChange: ((String) -> Unit)? = null,
    onSaveSettings: (UserSettings) -> Unit,
    openSettingsTrigger: Boolean = false,
    onSettingsOpened: (() -> Unit)? = null
) {
    var showSettingsSheet by remember { mutableStateOf(false) }
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    LaunchedEffect(openSettingsTrigger) {
        if (openSettingsTrigger) {
            showSettingsSheet = true
            onSettingsOpened?.invoke()
        }
    }

    if (updateInfo != null) {
        val context = LocalContext.current
        val activity = context as? Activity
        MandatoryUpdateScreen(
            updateInfo = updateInfo,
            isDownloading = isDownloadingUpdate,
            progress = updateDownloadProgress,
            onUpdateClick = { onUpdateClick(updateInfo) },
            onQuitClick = { activity?.finishAffinity() }
        )
        return
    }

    if (fullscreenStream != null) {
        WatchGrid(
            sessions = watchedSessions,
            fullscreenStreamName = fullscreenStream,
            onToggleFullscreen = onToggleFullscreen,
            onCloseStream = onCloseStream,
            modifier = Modifier.fillMaxSize()
        )
        return
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Image(
                            painter = painterResource(id = R.drawable.luce_logo),
                            contentDescription = "Project LUCE",
                            modifier = Modifier.size(34.dp)
                        )
                        Spacer(modifier = Modifier.width(10.dp))
                        Text(
                            text = "Project LUCE",
                            fontWeight = FontWeight.Bold,
                            fontSize = 18.sp,
                            color = LuceText
                        )
                    }
                },
                actions = {
                    Box(
                        modifier = Modifier
                            .background(
                                color = androidx.compose.ui.graphics.Color(0xFF22C55E).copy(alpha = 0.12f),
                                shape = RoundedCornerShape(12.dp)
                            )
                            .border(
                                width = 1.dp,
                                color = androidx.compose.ui.graphics.Color(0xFF22C55E).copy(alpha = 0.4f),
                                shape = RoundedCornerShape(12.dp)
                            )
                            .padding(horizontal = 8.dp, vertical = 4.dp),
                        contentAlignment = Alignment.Center
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Box(
                                modifier = Modifier
                                    .size(6.dp)
                                    .background(androidx.compose.ui.graphics.Color(0xFF22C55E), shape = CircleShape)
                            )
                            Spacer(modifier = Modifier.width(5.dp))
                            Text(
                                text = "$onlineCount online",
                                fontSize = 11.sp,
                                fontWeight = FontWeight.Bold,
                                color = androidx.compose.ui.graphics.Color(0xFF22C55E)
                            )
                        }
                    }
                    IconButton(onClick = { showSettingsSheet = true }) {
                        Icon(
                            Icons.Default.Settings,
                            contentDescription = "Configurações",
                            tint = LuceMuted
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = LuceNight,
                    titleContentColor = LuceText
                )
            )
        },
        containerColor = LuceNight
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 16.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            // Áreas de streams que estamos assistindo
            if (watchedSessions.isNotEmpty()) {
                Text(
                    text = "Assistindo",
                    color = LuceCyan,
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Bold
                )
                WatchGrid(
                    sessions = watchedSessions,
                    fullscreenStreamName = fullscreenStream,
                    onToggleFullscreen = onToggleFullscreen,
                    onCloseStream = onCloseStream,
                    modifier = Modifier.fillMaxWidth()
                )
            }

            // Card de Transmissão
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .border(1.dp, LuceBorder, RoundedCornerShape(12.dp)),
                shape = RoundedCornerShape(12.dp),
                colors = CardDefaults.cardColors(containerColor = LuceCard)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            text = "Transmitir Tela",
                            color = LuceCyan,
                            fontSize = 16.sp,
                            fontWeight = FontWeight.Bold
                        )
                        if (settings.isFullyAuthenticated) {
                            val user = settings.discordUser.ifBlank { "membro" }
                            Box(
                                modifier = Modifier
                                    .clip(RoundedCornerShape(6.dp))
                                    .background(LuceCyan.copy(alpha = 0.15f))
                                    .border(1.dp, LuceCyan.copy(alpha = 0.5f), RoundedCornerShape(6.dp))
                                    .clickable { showSettingsSheet = true }
                                    .padding(horizontal = 8.dp, vertical = 3.dp)
                            ) {
                                Text(
                                    text = "@$user (BDK) · Senha OK",
                                    color = LuceCyan,
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.Bold
                                )
                            }
                        } else {
                            Box(
                                modifier = Modifier
                                    .clip(RoundedCornerShape(6.dp))
                                    .background(LuceCardElevated)
                                    .border(1.dp, LuceMagenta.copy(alpha = 0.6f), RoundedCornerShape(6.dp))
                                    .clickable { showSettingsSheet = true }
                                    .padding(horizontal = 8.dp, vertical = 3.dp)
                            ) {
                                Text(
                                    text = "⚠ Autenticação necessária",
                                    color = LuceMagenta,
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.SemiBold
                                )
                            }
                        }
                    }

                    Spacer(modifier = Modifier.height(12.dp))

                    OutlinedTextField(
                        value = userName,
                        onValueChange = onUserNameChange,
                        label = { Text("Seu Nome") },
                        placeholder = { Text("digite seu nome") },
                        singleLine = true,
                        enabled = !isStreaming,
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

                    Spacer(modifier = Modifier.height(12.dp))

                    // Linha de Status
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(8.dp))
                            .background(LuceCardElevated)
                            .padding(horizontal = 12.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        val isOk = settings.isFullyAuthenticated && !isStreaming
                        val dotColor = if (isStreaming) LuceMagenta else if (isOk) LuceSuccess else LuceMuted
                        Box(
                            modifier = Modifier
                                .size(8.dp)
                                .clip(CircleShape)
                                .background(dotColor)
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        val effectiveStatus = if (!settings.isFullyAuthenticated && !isStreaming) {
                            "faça login no Discord (BDK) e digite a senha em Configurações"
                        } else {
                            streamStatusText
                        }
                        Text(
                            text = effectiveStatus,
                            color = LuceText,
                            fontSize = 13.sp,
                            modifier = Modifier.weight(1f)
                        )
                    }

                    if (isStreaming && streamStatsText.isNotBlank()) {
                        Spacer(modifier = Modifier.height(6.dp))
                        Text(
                            text = streamStatsText,
                            color = LuceMuted,
                            fontSize = 11.sp,
                            fontFamily = FontFamily.Monospace
                        )
                    }

                    Spacer(modifier = Modifier.height(16.dp))

                    Button(
                        onClick = {
                            if (isStreaming) onStopStreamClick() else onStartStreamClick()
                        },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(48.dp),
                        shape = RoundedCornerShape(8.dp),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = if (isStreaming) LuceMagenta else LuceCyan,
                            contentColor = if (isStreaming) Color.White else LuceNight
                        )
                    ) {
                        Icon(
                            if (isStreaming) Icons.Default.Stop else Icons.Default.PlayArrow,
                            contentDescription = null,
                            modifier = Modifier.size(20.dp)
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            text = if (isStreaming) "Parar Transmissão" else "Transmitir Tela",
                            fontWeight = FontWeight.Bold,
                            fontSize = 15.sp
                        )
                    }
                }
            }

            // Seção de Transmissões ao Vivo
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .border(1.dp, LuceBorder, RoundedCornerShape(12.dp)),
                shape = RoundedCornerShape(12.dp),
                colors = CardDefaults.cardColors(containerColor = LuceCard)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            text = "Transmissões ao Vivo",
                            color = LuceCyan,
                            fontSize = 16.sp,
                            fontWeight = FontWeight.Bold
                        )
                        IconButton(onClick = onRefreshRoster) {
                            if (isRosterLoading) {
                                CircularProgressIndicator(modifier = Modifier.size(16.dp), color = LuceCyan, strokeWidth = 2.dp)
                            } else {
                                Icon(Icons.Default.Refresh, contentDescription = "Atualizar", tint = LuceMuted)
                            }
                        }
                    }

                    Spacer(modifier = Modifier.height(8.dp))

                    if (rosterList.isEmpty()) {
                        Text(
                            text = "Ninguém transmitindo no momento.",
                            color = LuceMuted,
                            fontSize = 13.sp,
                            modifier = Modifier.padding(vertical = 12.dp)
                        )
                    } else {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            rosterList.forEach { item ->
                                val isWatchingThis = watchedSessions.any { it.streamName == item.name }
                                RosterItemCard(
                                    item = item,
                                    isWatching = isWatchingThis,
                                    onToggleWatch = { onToggleWatchStream(item.name) }
                                )
                            }
                        }
                    }
                }
            }

            Spacer(modifier = Modifier.height(24.dp))
        }
    }

    if (showSettingsSheet) {
        SettingsSheet(
            sheetState = sheetState,
            initialSettings = settings,
            onSave = onSaveSettings,
            onThemeChange = onThemeChange,
            onDismiss = { showSettingsSheet = false }
        )
    }
}

@Composable
fun RosterItemCard(
    item: RosterItem,
    isWatching: Boolean,
    onToggleWatch: () -> Unit
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .background(LuceCardElevated)
            .padding(12.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(4.dp))
                        .background(LuceMagenta)
                        .padding(horizontal = 6.dp, vertical = 2.dp)
                ) {
                    Text(
                        text = "AO VIVO",
                        color = Color.White,
                        fontSize = 10.sp,
                        fontWeight = FontWeight.Bold
                    )
                }
                Spacer(modifier = Modifier.width(8.dp))
                Text(
                    text = item.name,
                    color = LuceText,
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold
                )
            }

            if (item.viewers.isNotEmpty()) {
                Spacer(modifier = Modifier.height(4.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        Icons.Default.Visibility,
                        contentDescription = "Espectadores",
                        tint = LuceMuted,
                        modifier = Modifier.size(14.dp)
                    )
                    Spacer(modifier = Modifier.width(4.dp))
                    Text(
                        text = "${item.viewers.size}: ${item.viewers.joinToString(", ")}",
                        color = LuceMuted,
                        fontSize = 11.sp
                    )
                }
            }
        }

        Spacer(modifier = Modifier.width(12.dp))

        Button(
            onClick = onToggleWatch,
            shape = RoundedCornerShape(6.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = if (isWatching) LucePurple else LuceCyan,
                contentColor = if (isWatching) Color.White else LuceNight
            ),
            modifier = Modifier.height(36.dp)
        ) {
            Text(
                text = if (isWatching) "Assistindo" else "Assistir",
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold
            )
        }
    }
}

@Composable
fun MandatoryUpdateScreen(
    updateInfo: UpdateInfo,
    isDownloading: Boolean,
    progress: Float,
    onUpdateClick: () -> Unit,
    onQuitClick: () -> Unit
) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(LuceNight)
            .padding(24.dp),
        contentAlignment = Alignment.Center
    ) {
        Card(
            modifier = Modifier
                .fillMaxWidth()
                .border(1.dp, LuceCyan.copy(alpha = 0.5f), RoundedCornerShape(16.dp)),
            shape = RoundedCornerShape(16.dp),
            colors = CardDefaults.cardColors(containerColor = LuceCard)
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(16.dp)
            ) {
                Box(
                    modifier = Modifier
                        .clip(CircleShape)
                        .background(LuceMagenta.copy(alpha = 0.15f))
                        .border(1.dp, LuceMagenta, CircleShape)
                        .padding(horizontal = 12.dp, vertical = 4.dp)
                ) {
                    Text(
                        text = "ATUALIZAÇÃO OBRIGATÓRIA",
                        color = LuceMagenta,
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Bold
                    )
                }

                Text(
                    text = "Project LUCE",
                    color = LuceText,
                    fontSize = 20.sp,
                    fontWeight = FontWeight.Bold
                )

                Text(
                    text = "Versão ${updateInfo.version}",
                    color = LuceCyan,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.SemiBold
                )

                Text(
                    text = "Uma nova versão é necessária para continuar usando o Project LUCE.",
                    color = LuceMuted,
                    fontSize = 13.sp,
                    textAlign = TextAlign.Center,
                    lineHeight = 18.sp
                )

                if (isDownloading) {
                    Column(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        val pctText = if (progress >= 0f) "${(progress * 100).toInt()}%" else "baixando…"
                        Text(
                            text = "Baixando atualização: $pctText",
                            color = LuceText,
                            fontSize = 12.sp,
                            fontWeight = FontWeight.Medium
                        )
                        if (progress >= 0f) {
                            LinearProgressIndicator(
                                progress = { progress },
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(8.dp)
                                    .clip(RoundedCornerShape(4.dp)),
                                color = LuceMagenta,
                                trackColor = LuceBorder
                            )
                        } else {
                            LinearProgressIndicator(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(8.dp)
                                    .clip(RoundedCornerShape(4.dp)),
                                color = LuceMagenta,
                                trackColor = LuceBorder
                            )
                        }
                    }
                }

                Spacer(modifier = Modifier.height(4.dp))

                Button(
                    onClick = onUpdateClick,
                    enabled = !isDownloading,
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(48.dp),
                    shape = RoundedCornerShape(10.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = LuceCyan)
                ) {
                    Text(
                        text = if (isDownloading) "Baixando…" else "Atualizar Agora",
                        color = LuceNight,
                        fontWeight = FontWeight.Bold,
                        fontSize = 15.sp
                    )
                }

                Button(
                    onClick = onQuitClick,
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(44.dp),
                    shape = RoundedCornerShape(10.dp),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = LuceCardElevated,
                        contentColor = LuceMuted
                    )
                ) {
                    Text(
                        text = "Sair",
                        fontWeight = FontWeight.Medium,
                        fontSize = 14.sp
                    )
                }
            }
        }
    }
}
