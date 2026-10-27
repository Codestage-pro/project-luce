package pro.codestage.luce.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import pro.codestage.luce.UserSettings

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsSheet(
    sheetState: SheetState,
    initialSettings: UserSettings,
    onSave: (UserSettings) -> Unit,
    onDismiss: () -> Unit
) {
    var serverUrl by remember { mutableStateOf(initialSettings.serverUrl) }
    var password by remember { mutableStateOf(initialSettings.authPassword) }
    var showPassword by remember { mutableStateOf(false) }
    var selectedRes by remember { mutableStateOf(Pair(initialSettings.resolutionWidth, initialSettings.resolutionHeight)) }
    var selectedFps by remember { mutableIntStateOf(initialSettings.fps) }
    var selectedBitrate by remember { mutableIntStateOf(initialSettings.bitrateKbps) }
    var selectedAudio by remember { mutableStateOf(initialSettings.audioSource) }

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

            // Servidor
            OutlinedTextField(
                value = serverUrl,
                onValueChange = { serverUrl = it },
                label = { Text("Servidor") },
                placeholder = { Text("https://luce.codestage.pro") },
                singleLine = true,
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

            // Senha do Grupo
            OutlinedTextField(
                value = password,
                onValueChange = { password = it },
                label = { Text("Senha do Grupo") },
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

            Spacer(modifier = Modifier.height(20.dp))

            // Resolução
            Text("Resolução de Transmissão", color = LuceCyan, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            Spacer(modifier = Modifier.height(8.dp))
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(Pair(1280, 720) to "720p", Pair(1920, 1080) to "1080p").forEach { (res, label) ->
                    ChoiceButton(
                        label = label,
                        isSelected = selectedRes == res,
                        modifier = Modifier.weight(1f)
                    ) { selectedRes = res }
                }
            }

            Spacer(modifier = Modifier.height(16.dp))

            // Taxa de Quadros (FPS)
            Text("Taxa de Quadros", color = LuceCyan, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
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

            // Bitrate de Vídeo
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
                    label = "Áudio do sistema (jogos/apps)",
                    selected = selectedAudio == UserSettings.AUDIO_SOURCE_SYSTEM,
                    onClick = { selectedAudio = UserSettings.AUDIO_SOURCE_SYSTEM }
                )
                AudioOptionRow(
                    label = "Microfone",
                    selected = selectedAudio == UserSettings.AUDIO_SOURCE_MIC,
                    onClick = { selectedAudio = UserSettings.AUDIO_SOURCE_MIC }
                )
                AudioOptionRow(
                    label = "Mudo (sem áudio)",
                    selected = selectedAudio == UserSettings.AUDIO_SOURCE_MUTED,
                    onClick = { selectedAudio = UserSettings.AUDIO_SOURCE_MUTED }
                )
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
                            audioSource = selectedAudio
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
            .padding(vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        RadioButton(
            selected = selected,
            onClick = onClick,
            colors = RadioButtonDefaults.colors(
                selectedColor = LuceCyan,
                unselectedColor = LuceMuted
            )
        )
        Spacer(modifier = Modifier.width(8.dp))
        Text(text = label, color = LuceText, fontSize = 14.sp)
    }
}
