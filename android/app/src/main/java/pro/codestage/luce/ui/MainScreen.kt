package pro.codestage.luce.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
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
    onSaveSettings: (UserSettings) -> Unit
) {
    var showSettingsSheet by remember { mutableStateOf(false) }
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

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
                    if (updateInfo != null) {
                        IconButton(onClick = { onUpdateClick(updateInfo) }) {
                            Icon(
                                Icons.Default.SystemUpdate,
                                contentDescription = "Atualização disponível",
                                tint = LuceCyan
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
            // Banner de Atualização Disponível
            if (updateInfo != null) {
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .border(1.dp, LuceMagenta.copy(alpha = 0.6f), RoundedCornerShape(12.dp)),
                    shape = RoundedCornerShape(12.dp),
                    colors = CardDefaults.cardColors(containerColor = LuceCardElevated)
                ) {
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(16.dp)
                    ) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.SpaceBetween
                        ) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                modifier = Modifier.weight(1f)
                            ) {
                                Box(
                                    modifier = Modifier
                                        .size(36.dp)
                                        .background(LuceMagenta.copy(alpha = 0.2f), CircleShape),
                                    contentAlignment = Alignment.Center
                                ) {
                                    Icon(
                                        Icons.Default.SystemUpdate,
                                        contentDescription = null,
                                        tint = LuceMagenta,
                                        modifier = Modifier.size(20.dp)
                                    )
                                }
                                Spacer(modifier = Modifier.width(12.dp))
                                Column {
                                    Text(
                                        text = "Nova versão disponível",
                                        color = LuceText,
                                        fontSize = 15.sp,
                                        fontWeight = FontWeight.Bold
                                    )
                                    Text(
                                        text = "v${updateInfo.tagName}",
                                        color = LuceMagenta,
                                        fontSize = 13.sp,
                                        fontWeight = FontWeight.SemiBold
                                    )
                                }
                            }

                            if (!isDownloadingUpdate) {
                                Button(
                                    onClick = { onUpdateClick(updateInfo) },
                                    colors = ButtonDefaults.buttonColors(containerColor = LuceMagenta),
                                    shape = RoundedCornerShape(8.dp)
                                ) {
                                    Text("Atualizar", fontWeight = FontWeight.Bold, color = Color.White)
                                }
                            }
                        }

                        if (isDownloadingUpdate) {
                            Spacer(modifier = Modifier.height(12.dp))
                            val pctText = if (updateDownloadProgress >= 0f) {
                                "${(updateDownloadProgress * 100).toInt()}%"
                            } else {
                                "baixando…"
                            }
                            Text(
                                text = "Baixando atualização: $pctText",
                                color = LuceText,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.Medium
                            )
                            Spacer(modifier = Modifier.height(6.dp))
                            if (updateDownloadProgress >= 0f) {
                                LinearProgressIndicator(
                                    progress = { updateDownloadProgress },
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .height(6.dp)
                                        .clip(RoundedCornerShape(3.dp)),
                                    color = LuceMagenta,
                                    trackColor = LuceBorder
                                )
                            } else {
                                LinearProgressIndicator(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .height(6.dp)
                                        .clip(RoundedCornerShape(3.dp)),
                                    color = LuceMagenta,
                                    trackColor = LuceBorder
                                )
                            }
                        }
                    }
                }
            }

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
                    Text(
                        text = "Transmitir Tela",
                        color = LuceCyan,
                        fontSize = 16.sp,
                        fontWeight = FontWeight.Bold
                    )

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
                        Box(
                            modifier = Modifier
                                .size(8.dp)
                                .clip(CircleShape)
                                .background(if (isStreaming) LuceMagenta else LuceSuccess)
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            text = streamStatusText,
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
