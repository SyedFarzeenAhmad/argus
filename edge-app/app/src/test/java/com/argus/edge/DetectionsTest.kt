package com.argus.edge

import com.argus.edge.processing.Detection
import com.argus.edge.processing.Letterbox
import com.argus.edge.processing.YoloDecoder
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DetectionsTest {
    @Test fun letterboxLandscapePadsVertically() {
        val lb = Letterbox.of(1280, 720, 640)
        assertEquals(0.5f, lb.scale, 1e-6f)
        assertEquals(0f, lb.padX, 1e-6f)
        assertEquals(140f, lb.padY, 1e-6f)
        val (x, y) = lb.toOriginal(320f, 320f)
        assertEquals(640f, x, 1e-3f)
        assertEquals(360f, y, 1e-3f)
    }

    @Test fun decodeMapsBackToOriginalPixelsAndFiltersClass() {
        val lb = Letterbox.of(1280, 720, 640)
        // 3 anchors: a pothole, a manhole (class 1, must be dropped), a weak pothole (below threshold)
        val out = arrayOf(
            floatArrayOf(320f, 100f, 500f),  // cx
            floatArrayOf(320f, 300f, 300f),  // cy
            floatArrayOf(100f, 50f, 50f),    // w
            floatArrayOf(50f, 50f, 50f),     // h
            floatArrayOf(0.9f, 0.1f, 0.2f),  // class 0 pothole
            floatArrayOf(0.05f, 0.95f, 0.1f),// class 1 manhole
        )
        val d = YoloDecoder.decode(out, 2, setOf(0), 0.35f, lb, 1280, 720)
        assertEquals(1, d.size)
        assertEquals(540f, d[0].x1, 1e-3f)   // (270-0)/0.5
        assertEquals(310f, d[0].y1, 1e-3f)   // (295-140)/0.5
        assertEquals(740f, d[0].x2, 1e-3f)
        assertEquals(410f, d[0].y2, 1e-3f)
    }

    @Test fun nmsKeepsBestOfOverlapsButNotDisjoint() {
        val a = Detection(0f, 0f, 100f, 100f, 0.9f, 0)
        val b = Detection(5f, 5f, 105f, 105f, 0.8f, 0)
        val c = Detection(300f, 300f, 400f, 400f, 0.7f, 0)
        val kept = YoloDecoder.nms(listOf(b, c, a), 0.45f)
        assertEquals(listOf(a, c), kept)
    }

    @Test fun iouOfIdenticalIsOne() {
        val a = Detection(0f, 0f, 10f, 10f, 1f, 0)
        assertEquals(1f, YoloDecoder.iou(a, a), 1e-6f)
        assertTrue(YoloDecoder.iou(a, Detection(20f, 20f, 30f, 30f, 1f, 0)) == 0f)
    }
}
