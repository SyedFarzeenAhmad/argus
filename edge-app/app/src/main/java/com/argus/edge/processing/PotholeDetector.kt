package com.argus.edge.processing

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import java.io.Closeable
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer

/**
 * The MVP's one model: potholes. Loads assets/models/pothole.onnx (YOLO11n detect, classes
 * 0 = Pothole, 1 = Sewage-Manhole; only class 0 is reported). CPU execution provider — the
 * NPU path is added once an INT8 export exists (docs/03).
 *
 * Not thread-safe by design: the engine runs inference on one dedicated thread.
 */
class PotholeDetector(context: Context) : Closeable {
    val modelName = "pothole-yolo11n"
    val modelVersion = "tahaUgan-2v"
    val runtime = "onnxrt-cpu"
    val inputSize = 640

    private val env = OrtEnvironment.getEnvironment()
    private val session: OrtSession
    private val inputName: String
    private val numClasses: Int

    private val canvasBitmap = Bitmap.createBitmap(inputSize, inputSize, Bitmap.Config.ARGB_8888)
    private val canvas = Canvas(canvasBitmap)
    private val paint = Paint(Paint.FILTER_BITMAP_FLAG)
    private val pixels = IntArray(inputSize * inputSize)
    private val input: FloatBuffer =
        ByteBuffer.allocateDirect(4 * 3 * inputSize * inputSize).order(ByteOrder.nativeOrder()).asFloatBuffer()

    init {
        val bytes = context.assets.open("models/pothole.onnx").use { it.readBytes() }
        val opts = OrtSession.SessionOptions().apply {
            setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT)
            setIntraOpNumThreads(Runtime.getRuntime().availableProcessors().coerceIn(2, 4))
        }
        session = env.createSession(bytes, opts)
        inputName = session.inputNames.first()
        val outShape = (session.outputInfo.values.first().info as ai.onnxruntime.TensorInfo).shape
        numClasses = (outShape[1] - 4).toInt()
    }

    fun detect(frame: Bitmap, confThreshold: Float): List<Detection> {
        val lb = Letterbox.of(frame.width, frame.height, inputSize)
        preprocess(frame, lb)
        OnnxTensor.createTensor(env, input, longArrayOf(1, 3, inputSize.toLong(), inputSize.toLong())).use { t ->
            session.run(mapOf(inputName to t)).use { result ->
                @Suppress("UNCHECKED_CAST")
                val out = (result[0].value as Array<Array<FloatArray>>)[0]
                return YoloDecoder.decode(out, numClasses, setOf(POTHOLE), confThreshold, lb, frame.width, frame.height)
            }
        }
    }

    private fun preprocess(frame: Bitmap, lb: Letterbox) {
        canvas.drawColor(Color.rgb(114, 114, 114))
        canvas.drawBitmap(frame, null, RectF(lb.padX, lb.padY, lb.padX + lb.newW, lb.padY + lb.newH), paint)
        canvasBitmap.getPixels(pixels, 0, inputSize, 0, 0, inputSize, inputSize)
        val plane = inputSize * inputSize
        input.rewind()
        for (i in 0 until plane) {
            val p = pixels[i]
            input.put(i, ((p shr 16) and 0xFF) / 255f)
            input.put(plane + i, ((p shr 8) and 0xFF) / 255f)
            input.put(2 * plane + i, (p and 0xFF) / 255f)
        }
        input.rewind()
    }

    override fun close() {
        session.close()
        canvasBitmap.recycle()
    }

    companion object { const val POTHOLE = 0 }
}
