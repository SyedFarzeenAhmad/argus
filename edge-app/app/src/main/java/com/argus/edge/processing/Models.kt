package com.argus.edge.processing

/**
 * What ARGUS can see in the MVP, and which model sees it. Every model here is a PROTOTYPE:
 * public weights trained with Ultralytics (AGPL-3.0, docs/03 "Model licensing"), none trained on
 * Bengaluru roads, accuracy here unmeasured. CV-Perception replaces them file by file.
 */
enum class Kind(val label: String, val category: String) {
    POTHOLE("pothole", "pothole"),
    LONGITUDINAL_CRACK("longitudinal crack", "damaged_road"),
    TRANSVERSE_CRACK("transverse crack", "damaged_road"),
    ALLIGATOR_CRACK("alligator crack", "damaged_road"),
    CAR("car", "traffic_counting"),
    TWO_WHEELER("two-wheeler", "traffic_counting"),
    BUS("bus", "traffic_counting"),
    TRUCK("truck", "traffic_counting"),
    BICYCLE("bicycle", "traffic_counting"),
    PEDESTRIAN("pedestrian", "traffic_counting");

    val isRoadDefect get() = category == "pothole" || category == "damaged_road"
}

object Models {
    data class Spec(
        val asset: String,
        val name: String,
        val version: String,
        val numClasses: Int,
        /** model class index → what we report. Classes not listed are ignored. */
        val keep: Map<Int, Kind>,
        val defaultConfidence: Float,
    )

    /** tahaUgan/pothole-yolo11n — 0 Pothole, 1 Sewage-Manhole (a cover, not an OPEN manhole: ignored). */
    val POTHOLE = Spec("pothole.onnx", "pothole-yolo11n", "tahaUgan-2v", 2, mapOf(0 to Kind.POTHOLE), 0.35f)

    /**
     * dronefreak/rdd2022-yolov8n — RDD2022 (includes India): published test mAP@50 58.8%.
     * Its class 3 (pothole) is ignored: the dedicated pothole model owns that class.
     */
    val ROAD_DAMAGE = Spec(
        "road_damage.onnx", "rdd2022-yolov8n", "dronefreak-1", 4,
        mapOf(0 to Kind.LONGITUDINAL_CRACK, 1 to Kind.TRANSVERSE_CRACK, 2 to Kind.ALLIGATOR_CRACK),
        0.30f,
    )

    /**
     * Ultralytics YOLO11n, COCO-pretrained. COCO has no auto-rickshaw class: autos come out as
     * car, truck or nothing — the known gap docs/02 names, fixed only by training on IDD.
     */
    val TRAFFIC = Spec(
        "traffic.onnx", "yolo11n-coco", "ultralytics-8.3", 80,
        mapOf(0 to Kind.PEDESTRIAN, 1 to Kind.BICYCLE, 2 to Kind.CAR, 3 to Kind.TWO_WHEELER, 5 to Kind.BUS, 7 to Kind.TRUCK),
        0.35f,
    )

    /** Dataset pre-label class ids (YOLO txt), written to classes.txt in each dataset folder. */
    val DATASET_CLASSES = listOf(Kind.POTHOLE, Kind.LONGITUDINAL_CRACK, Kind.TRANSVERSE_CRACK, Kind.ALLIGATOR_CRACK)
}

/** A detection with what it is. */
data class Labeled(val det: Detection, val kind: Kind)
