package com.argus.edge.processing

/**
 * Minimal IoU tracker for counting (docs/02: counts are UNIQUE TRACKS, never per-frame sums,
 * which would scale with bus speed). Greedy highest-IoU matching within a class group, tracks
 * confirmed after [minHits] and dropped after [maxMisses]. Deliberately simple — no motion
 * model, so fast oncoming traffic at low frame rates can fragment into two tracks and be
 * over-counted. ByteTrack (docs/03) replaces it.
 */
class IouTracker(
    private val iouThreshold: Float = 0.3f,
    private val minHits: Int = 3,
    private val maxMisses: Int = 3,
) {
    class Track(val id: Long, var box: Detection, val kind: Kind) {
        var hits = 1
        var misses = 0
        var counted = false
    }

    private val tracks = ArrayList<Track>()
    private var nextId = 1L

    /** Feeds one frame. Returns tracks that became confirmed on this frame (count each once). */
    fun update(dets: List<Labeled>): List<Track> {
        val unmatched = dets.toMutableList()
        val newlyConfirmed = ArrayList<Track>()
        // Greedy: best IoU pairs first.
        val pairs = ArrayList<Triple<Track, Labeled, Float>>()
        for (t in tracks) for (d in dets) {
            if (group(t.kind) != group(d.kind)) continue
            val iou = YoloDecoder.iou(t.box, d.det)
            if (iou >= iouThreshold) pairs += Triple(t, d, iou)
        }
        pairs.sortByDescending { it.third }
        val usedTracks = HashSet<Track>()
        for ((t, d, _) in pairs) {
            if (t in usedTracks || d !in unmatched) continue
            usedTracks += t; unmatched -= d
            t.box = d.det; t.hits++; t.misses = 0
            if (!t.counted && t.hits >= minHits) { t.counted = true; newlyConfirmed += t }
        }
        for (t in tracks) if (t !in usedTracks) t.misses++
        tracks.removeAll { it.misses > maxMisses }
        for (d in unmatched) tracks += Track(nextId++, d.det, d.kind)
        return newlyConfirmed
    }

    /** Confirmed tracks visible right now, by kind. */
    fun visible(): Map<Kind, Int> = tracks.filter { it.misses == 0 && it.hits >= minHits }.groupingBy { it.kind }.eachCount()

    // A car detected as a truck on one frame is the same vehicle; people and bicycles stay apart.
    private fun group(k: Kind) = when (k) {
        Kind.CAR, Kind.TRUCK, Kind.BUS -> 0
        Kind.TWO_WHEELER, Kind.BICYCLE -> 1
        Kind.PEDESTRIAN -> 2
        else -> 3
    }
}

/** Counts for one camera over one window (default 30 s). Becomes SegmentPass once map matching exists. */
class TrafficWindow(val cameraId: String, val startMs: Long) {
    val unique = HashMap<Kind, Int>()
    var frames = 0
    var vehiclesInFrameSum = 0
    var maxPedestriansInFrame = 0

    fun add(newlyConfirmed: List<IouTracker.Track>, visible: Map<Kind, Int>) {
        frames++
        for (t in newlyConfirmed) unique[t.kind] = (unique[t.kind] ?: 0) + 1
        vehiclesInFrameSum += visible.filterKeys { it != Kind.PEDESTRIAN }.values.sum()
        maxPedestriansInFrame = maxOf(maxPedestriansInFrame, visible[Kind.PEDESTRIAN] ?: 0)
    }

    val meanVehiclesInFrame: Float get() = if (frames == 0) 0f else vehiclesInFrameSum.toFloat() / frames
}
