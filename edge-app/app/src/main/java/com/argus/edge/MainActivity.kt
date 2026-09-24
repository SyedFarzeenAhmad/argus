package com.argus.edge

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import com.argus.edge.core.Role
import com.argus.edge.ui.CameraScreen
import com.argus.edge.ui.ProcessingScreen
import com.argus.edge.ui.RoleScreen
import com.argus.edge.ui.SettingsSheet
import com.argus.edge.ui.theme.ArgusTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
        )
        val app = application as ArgusApp

        setContent {
            ArgusTheme {
                var role by remember { mutableStateOf(app.prefs.role) }
                var settings by remember { mutableStateOf(false) }

                AnimatedContent(
                    targetState = role,
                    transitionSpec = { fadeIn(tween(350)) togetherWith fadeOut(tween(250)) },
                    label = "role",
                ) { r ->
                    when (r) {
                        null -> RoleScreen(app.prefs.deviceId) { chosen -> app.prefs.role = chosen; role = chosen }
                        Role.CAMERA -> CameraScreen(onSettings = { settings = true })
                        Role.PROCESSOR -> ProcessingScreen(app.engine, app.prefs, onSettings = { settings = true })
                    }
                }

                val current = role
                if (settings && current != null) {
                    SettingsSheet(
                        prefs = app.prefs,
                        role = current,
                        onDismiss = { settings = false },
                        onChangeRole = {
                            settings = false
                            if (current == Role.PROCESSOR) app.engine.deactivate()
                            app.prefs.role = null
                            role = null
                        },
                    )
                }
            }
        }
    }
}
