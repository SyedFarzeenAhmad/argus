package com.argus.edge.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.SwapHoriz
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.argus.edge.BuildConfig
import com.argus.edge.core.Prefs
import com.argus.edge.core.Role
import com.argus.edge.ui.theme.Argus
import com.argus.edge.ui.theme.Mono

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsSheet(prefs: Prefs, role: Role, onDismiss: () -> Unit, onChangeRole: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var deviceId by remember { mutableStateOf(prefs.deviceId) }
    var bus by remember { mutableStateOf(prefs.busId) }
    var route by remember { mutableStateOf(prefs.routeId) }
    var conf by remember { mutableFloatStateOf(prefs.confidenceThreshold) }

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet, containerColor = Argus.Surface) {
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).navigationBarsPadding()
                .padding(horizontal = 22.dp).padding(bottom = 18.dp),
        ) {
            Text("Settings", style = MaterialTheme.typography.headlineSmall, color = Argus.Text)
            Text(
                if (role == Role.CAMERA) "This phone is a camera." else "This phone is the processing client.",
                style = MaterialTheme.typography.bodySmall, color = Argus.TextDim,
            )
            Spacer(Modifier.height(18.dp))

            if (role == Role.PROCESSOR) {
                Field("Device ID", deviceId, "Stable id of this edge unit, e.g. BLR-BUS-4417-EDGE") { deviceId = it.uppercase() }
                Field("Bus", bus, "Registration, e.g. KA01FA4417") { bus = it }
                Field("Route", route, "e.g. 500D") { route = it }
                Spacer(Modifier.height(8.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Pothole confidence threshold", style = MaterialTheme.typography.titleSmall, color = Argus.Text, modifier = Modifier.weight(1f))
                    Text("%.2f".format(conf), style = MaterialTheme.typography.titleSmall.copy(fontFamily = Mono), color = Argus.Accent)
                }
                Slider(
                    value = conf, onValueChange = { conf = it }, valueRange = 0.1f..0.9f,
                    colors = SliderDefaults.colors(thumbColor = Argus.Accent, activeTrackColor = Argus.Accent, inactiveTrackColor = Argus.SurfaceTop),
                )
                Text("Lower finds more, with more false alarms. The backend's multi-pass fusion filters the rest.",
                    style = MaterialTheme.typography.bodySmall, color = Argus.TextFaint)
                Spacer(Modifier.height(16.dp))
                Button(
                    onClick = {
                        prefs.setDeviceId(deviceId); prefs.busId = bus; prefs.routeId = route; prefs.confidenceThreshold = conf
                        onDismiss()
                    },
                    modifier = Modifier.fillMaxWidth().height(50.dp),
                    shape = RoundedCornerShape(14.dp),
                ) { Text("Save") }
                Spacer(Modifier.height(10.dp))
            }

            OutlinedButton(
                onClick = onChangeRole,
                modifier = Modifier.fillMaxWidth().height(50.dp),
                shape = RoundedCornerShape(14.dp),
                colors = ButtonDefaults.outlinedButtonColors(contentColor = Argus.Warn),
                border = BorderStroke(1.dp, Argus.Warn.copy(alpha = 0.5f)),
            ) {
                Icon(Icons.Rounded.SwapHoriz, null)
                Spacer(Modifier.width(8.dp))
                Text("Change what this phone does")
            }
            Spacer(Modifier.height(14.dp))
            Text("ARGUS Edge v${BuildConfig.VERSION_NAME} · model pothole-yolo11n (prototype)",
                style = MaterialTheme.typography.bodySmall.copy(fontFamily = Mono), color = Argus.TextFaint)
        }
    }
}

@Composable
private fun Field(label: String, value: String, hint: String, onChange: (String) -> Unit) {
    OutlinedTextField(
        value = value, onValueChange = onChange, label = { Text(label) }, supportingText = { Text(hint) },
        singleLine = true, modifier = Modifier.fillMaxWidth().padding(bottom = 6.dp),
        shape = RoundedCornerShape(14.dp),
        textStyle = MaterialTheme.typography.bodyLarge.copy(fontFamily = Mono),
        colors = OutlinedTextFieldDefaults.colors(unfocusedBorderColor = Argus.Outline, focusedBorderColor = Argus.Accent),
    )
}
