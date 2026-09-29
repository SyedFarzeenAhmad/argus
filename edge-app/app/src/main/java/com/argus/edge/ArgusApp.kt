package com.argus.edge

import android.app.Application
import com.argus.edge.core.Prefs
import com.argus.edge.processing.EdgeEngine

class ArgusApp : Application() {
    lateinit var prefs: Prefs
        private set

    /** Processing-role engine. Application-scoped so rotation or a recomposition never drops a camera link. */
    val engine: EdgeEngine by lazy { EdgeEngine(this, prefs) }

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
    }
}
