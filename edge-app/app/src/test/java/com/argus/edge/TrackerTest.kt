package com.argus.edge

import com.argus.edge.processing.Detection
import com.argus.edge.processing.IouTracker
import com.argus.edge.processing.Kind
import com.argus.edge.processing.Labeled
import com.argus.edge.processing.TrafficWindow
import org.junit.Assert.assertEquals
import org.junit.Test

class TrackerTest {
    private fun car(x: Float) = Labeled(Detection(x, 100f, x + 80f, 160f, 0.9f, 2), Kind.CAR)
    private fun person(x: Float) = Labeled(Detection(x, 50f, x + 20f, 120f, 0.9f, 0), Kind.PEDESTRIAN)

    @Test fun oneCarSeenForManyFramesCountsOnce() {
        val t = IouTracker(); val w = TrafficWindow("front", 0)
        for (i in 0 until 20) w.add(t.update(listOf(car(100f + i * 4))), t.visible())
        assertEquals(1, w.unique[Kind.CAR])
        assertEquals(20, w.frames)
    }

    @Test fun twoFramesIsNotEnoughToCount() {
        val t = IouTracker(); val w = TrafficWindow("front", 0)
        repeat(2) { w.add(t.update(listOf(car(100f))), t.visible()) }
        assertEquals(null, w.unique[Kind.CAR])
    }

    @Test fun carAndPedestriansAreCountedSeparatelyAndMaxCrowdTracked() {
        val t = IouTracker(); val w = TrafficWindow("front", 0)
        repeat(5) { w.add(t.update(listOf(car(300f), person(10f), person(60f), person(110f))), t.visible()) }
        assertEquals(1, w.unique[Kind.CAR])
        assertEquals(3, w.unique[Kind.PEDESTRIAN])
        assertEquals(3, w.maxPedestriansInFrame)
    }

    @Test fun classFlickerCarToTruckIsStillOneVehicle() {
        val t = IouTracker(); val w = TrafficWindow("front", 0)
        repeat(6) { i ->
            val k = if (i % 2 == 0) Kind.CAR else Kind.TRUCK
            w.add(t.update(listOf(Labeled(Detection(100f, 100f, 180f, 160f, 0.9f, 2), k))), t.visible())
        }
        assertEquals(1, w.unique.values.sum())
    }

    @Test fun aVehicleThatLeavesAndAnotherThatArrivesAreTwo() {
        val t = IouTracker(); val w = TrafficWindow("front", 0)
        repeat(4) { w.add(t.update(listOf(car(100f))), t.visible()) }
        repeat(5) { w.add(t.update(emptyList()), t.visible()) }   // gone longer than maxMisses
        repeat(4) { w.add(t.update(listOf(car(100f))), t.visible()) }
        assertEquals(2, w.unique[Kind.CAR])
    }
}
