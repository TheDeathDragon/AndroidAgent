package la.shiro.agent

import android.annotation.SuppressLint
import android.app.UiAutomation
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.graphics.Point
import android.hardware.display.DisplayManager
import android.hardware.display.DisplayManagerGlobal
import android.hardware.display.VirtualDisplay
import android.media.Image
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.view.Surface
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.util.ArrayDeque
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.locks.Condition
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock
import kotlin.math.roundToInt

enum class ImageFormat(
    val wireName: String,
    val mimeType: String,
    val defaultQuality: Int
) {
    PNG("png", "image/png", 100),
    JPEG("jpeg", "image/jpeg", 80),
    RGBA("rgba", "application/octet-stream", 100);

    companion object {
        fun parse(raw: String?): ImageFormat {
            if (raw.isNullOrBlank()) return PNG
            return when (raw.trim().lowercase(Locale.US)) {
                "png" -> PNG
                "jpeg", "jpg" -> JPEG
                "rgba" -> RGBA
                else -> throw BadRequestException("unsupported format: $raw")
            }
        }
    }
}

enum class CaptureSource(val wireName: String) {
    VIRTUAL_DISPLAY("virtual-display"),
    UI_AUTOMATION("uiautomation")
}

data class CaptureRequest(
    val format: ImageFormat = ImageFormat.PNG,
    val quality: Int = format.defaultQuality,
    val scale: Float = 1.0f,
    val fast: Boolean = false
) {
    companion object {
        fun parse(params: Map<String, String>): CaptureRequest {
            val format = ImageFormat.parse(params["format"])
            val quality = params["quality"]?.let { raw ->
                val parsed = raw.toIntOrNull()
                    ?: throw BadRequestException("invalid quality: $raw")
                if (parsed !in 1..100) {
                    throw BadRequestException("quality out of range 1..100: $parsed")
                }
                parsed
            } ?: format.defaultQuality

            val scale = params["scale"]?.let { raw ->
                val parsed = raw.toFloatOrNull()
                    ?: throw BadRequestException("invalid scale: $raw")
                if (!parsed.isFinite() || parsed < 0.1f || parsed > 1.0f) {
                    throw BadRequestException("scale out of range 0.1..1.0: $raw")
                }
                parsed
            } ?: 1.0f

            val fast = params["fast"]?.let { raw ->
                raw.trim().lowercase(Locale.US).toBooleanStrictOrNull()
                    ?: throw BadRequestException("invalid fast flag: $raw")
            } ?: false

            return CaptureRequest(format, quality, scale, fast)
        }
    }
}

class CaptureResult(
    val buffer: ByteArray,
    val length: Int,
    val width: Int,
    val height: Int,
    val format: ImageFormat,
    val source: CaptureSource,
    private val onClose: () -> Unit
) : AutoCloseable {

    private val closed = AtomicBoolean(false)

    fun toHeaders(): Map<String, String> = mapOf(
        "X-Width" to width.toString(),
        "X-Height" to height.toString(),
        "X-Format" to format.wireName,
        "X-Capture-Source" to source.wireName
    )

    override fun close() {
        if (closed.compareAndSet(false, true)) {
            onClose()
        }
    }
}

internal class ExposedByteArrayOutputStream(
    initialCapacity: Int = 256 * 1024
) : ByteArrayOutputStream(initialCapacity) {

    val rawBuffer: ByteArray get() = buf
    val length: Int get() = count

    fun prepareExact(byteCount: Int): ByteArray {
        if (buf.size < byteCount) {
            buf = ByteArray(byteCount)
        }
        count = byteCount
        return buf
    }
}

// Isolated helper so R8 does not inline the Android 14+ static 5-arg hidden API into ScreenCapture.
internal object VirtualDisplayFactory {
    fun create(
        name: String,
        width: Int,
        height: Int,
        displayIdToMirror: Int,
        surface: Surface
    ): VirtualDisplay? {
        return DisplayManager.createVirtualDisplay(name, width, height, displayIdToMirror, surface)
    }
}

class ScreenCapture(private val uiAutomation: UiAutomation) : AutoCloseable {

    private data class SurfaceConfig(
        val displayWidth: Int,
        val displayHeight: Int,
        val rotation: Int,
        val targetWidth: Int,
        val targetHeight: Int
    )

    private sealed class VirtualDisplayState {
        object Uninitialized : VirtualDisplayState()
        class Active(
            val config: SurfaceConfig,
            val virtualDisplay: VirtualDisplay,
            val imageReader: ImageReader,
            val frameLock: ReentrantLock,
            val frameReady: Condition,
            var latestRgba: ByteArray,
            var backRgba: ByteArray,
            var hasFrame: Boolean,
            var encodeBitmap: Bitmap?,
            var disposed: Boolean = false
        ) : VirtualDisplayState()
        class Unavailable(val reason: String) : VirtualDisplayState()
        object Closed : VirtualDisplayState()
    }

    private class BufferPool(
        private val maxPooled: Int = 2,
        private val maxRetainedBytes: Int = 12 * 1024 * 1024
    ) {
        private val lock = Any()
        private val pool = ArrayDeque<ExposedByteArrayOutputStream>(maxPooled)
        private var closed = false

        fun acquire(): ExposedByteArrayOutputStream {
            synchronized(lock) {
                val reused = if (pool.isEmpty()) null else pool.removeFirst()
                if (reused != null) {
                    reused.reset()
                    return reused
                }
            }
            return ExposedByteArrayOutputStream()
        }

        fun release(stream: ExposedByteArrayOutputStream) {
            stream.reset()
            synchronized(lock) {
                if (!closed && pool.size < maxPooled && stream.rawBuffer.size <= maxRetainedBytes) {
                    pool.addFirst(stream)
                }
            }
        }

        fun close() {
            synchronized(lock) {
                closed = true
                pool.clear()
            }
        }
    }

    private val captureLock = Any()
    private val bufferPool = BufferPool()
    private var state: VirtualDisplayState = VirtualDisplayState.Uninitialized
    private var mirrorThread: HandlerThread? = null
    private var mirrorHandler: Handler? = null

    fun capture(request: CaptureRequest = CaptureRequest()): CaptureResult? {
        if (request.fast) {
            val fastResult = captureFast(request)
            if (fastResult != null) return fastResult
        }
        return captureViaUiAutomation(request)
    }

    private fun captureFast(request: CaptureRequest): CaptureResult? {
        val desiredConfig = readDesiredSurfaceConfig(request.scale) ?: return null
        val stream = bufferPool.acquire()
        var handedOff = false
        try {
            synchronized(captureLock) {
                val active = ensureActiveLocked(desiredConfig) ?: return null
                if (!awaitFirstFrame(active)) {
                    return null
                }
                val width = active.config.targetWidth
                val height = active.config.targetHeight
                val byteCount = width * height * 4

                when (request.format) {
                    ImageFormat.RGBA -> {
                        val dst = stream.prepareExact(byteCount)
                        val copied = active.frameLock.withLock {
                            if (active.disposed || !active.hasFrame) {
                                false
                            } else {
                                System.arraycopy(active.latestRgba, 0, dst, 0, byteCount)
                                true
                            }
                        }
                        if (!copied) return null
                    }
                    ImageFormat.PNG, ImageFormat.JPEG -> {
                        val bitmap = active.encodeBitmap
                            ?: Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888).also {
                                active.encodeBitmap = it
                            }
                        val copied = active.frameLock.withLock {
                            if (active.disposed || !active.hasFrame) {
                                false
                            } else {
                                bitmap.copyPixelsFromBuffer(ByteBuffer.wrap(active.latestRgba, 0, byteCount))
                                true
                            }
                        }
                        if (!copied) return null
                        val compressFormat = if (request.format == ImageFormat.PNG) {
                            Bitmap.CompressFormat.PNG
                        } else {
                            Bitmap.CompressFormat.JPEG
                        }
                        bitmap.compress(compressFormat, request.quality, stream)
                    }
                }

                handedOff = true
                return CaptureResult(
                    buffer = stream.rawBuffer,
                    length = stream.length,
                    width = width,
                    height = height,
                    format = request.format,
                    source = CaptureSource.VIRTUAL_DISPLAY,
                    onClose = { bufferPool.release(stream) }
                )
            }
        } finally {
            if (!handedOff) {
                bufferPool.release(stream)
            }
        }
    }

    private fun ensureActiveLocked(desired: SurfaceConfig): VirtualDisplayState.Active? {
        when (val current = state) {
            is VirtualDisplayState.Closed -> return null
            is VirtualDisplayState.Unavailable -> return null
            is VirtualDisplayState.Active -> {
                if (current.config == desired) return current
                disposeActiveLocked(current)
                state = VirtualDisplayState.Uninitialized
            }
            is VirtualDisplayState.Uninitialized -> Unit
        }

        val handler = ensureMirrorHandlerLocked()
        val byteCount = desired.targetWidth * desired.targetHeight * 4
        // Always use RGBA_8888; RGB_565 produces corrupted buffers on Qualcomm Android 16 devices.
        val reader = ImageReader.newInstance(
            desired.targetWidth,
            desired.targetHeight,
            PixelFormat.RGBA_8888,
            IMAGE_READER_MAX_IMAGES
        )
        val virtualDisplay = try {
            VirtualDisplayFactory.create(
                MIRROR_DISPLAY_NAME,
                desired.targetWidth,
                desired.targetHeight,
                DEFAULT_DISPLAY_ID,
                reader.surface
            )
        } catch (t: Throwable) {
            try { reader.close() } catch (_: Throwable) {}
            val reason = "${t.javaClass.simpleName}: ${t.message}"
            state = VirtualDisplayState.Unavailable(reason)
            System.err.println("[Agent] VirtualDisplay unavailable, falling back to UiAutomation: $reason")
            return null
        }

        if (virtualDisplay == null) {
            try { reader.close() } catch (_: Throwable) {}
            state = VirtualDisplayState.Unavailable("createVirtualDisplay returned null")
            return null
        }

        val frameLock = ReentrantLock()
        val active = VirtualDisplayState.Active(
            config = desired,
            virtualDisplay = virtualDisplay,
            imageReader = reader,
            frameLock = frameLock,
            frameReady = frameLock.newCondition(),
            latestRgba = ByteArray(byteCount),
            backRgba = ByteArray(byteCount),
            hasFrame = false,
            encodeBitmap = null
        )

        reader.setOnImageAvailableListener({ imageReader ->
            onMirrorImageAvailable(active, imageReader)
        }, handler)
        handler.post { onMirrorImageAvailable(active, reader) }

        state = active
        return active
    }

    private fun onMirrorImageAvailable(active: VirtualDisplayState.Active, imageReader: ImageReader) {
        val image: Image = try {
            imageReader.acquireLatestImage() ?: return
        } catch (_: Throwable) {
            return
        }
        try {
            if (active.disposed) return
            val plane = image.planes[0]
            val buffer = plane.buffer
            val pixelStride = plane.pixelStride
            val rowStride = plane.rowStride
            val width = minOf(active.config.targetWidth, image.width)
            val height = minOf(active.config.targetHeight, image.height)
            val rowBytes = active.config.targetWidth * 4
            val copyRowBytes = width * 4
            val dst = active.backRgba

            if (pixelStride == 4 && rowStride == rowBytes && width == active.config.targetWidth) {
                buffer.position(0)
                buffer.get(dst, 0, rowBytes * height)
            } else if (pixelStride == 4) {
                for (y in 0 until height) {
                    buffer.position(y * rowStride)
                    buffer.get(dst, y * rowBytes, copyRowBytes)
                }
            } else {
                for (y in 0 until height) {
                    val rowBase = y * rowStride
                    val dstRowBase = y * rowBytes
                    for (x in 0 until width) {
                        val srcIdx = rowBase + x * pixelStride
                        val dstIdx = dstRowBase + x * 4
                        dst[dstIdx] = buffer.get(srcIdx)
                        dst[dstIdx + 1] = buffer.get(srcIdx + 1)
                        dst[dstIdx + 2] = buffer.get(srcIdx + 2)
                        dst[dstIdx + 3] = buffer.get(srcIdx + 3)
                    }
                }
            }

            active.frameLock.withLock {
                if (active.disposed) return
                val swapped = active.latestRgba
                active.latestRgba = active.backRgba
                active.backRgba = swapped
                active.hasFrame = true
                active.frameReady.signalAll()
            }
        } catch (_: IllegalStateException) {
        } catch (_: IndexOutOfBoundsException) {
        } catch (_: java.nio.BufferUnderflowException) {
        } finally {
            try {
                image.close()
            } catch (_: Throwable) {
            }
        }
    }

    private fun awaitFirstFrame(active: VirtualDisplayState.Active): Boolean {
        var remainingNs = FIRST_FRAME_TIMEOUT_MS * 1_000_000L
        return active.frameLock.withLock {
            while (!active.hasFrame && !active.disposed && remainingNs > 0L) {
                try {
                    remainingNs = active.frameReady.awaitNanos(remainingNs)
                } catch (_: InterruptedException) {
                    Thread.currentThread().interrupt()
                    return@withLock false
                }
            }
            active.hasFrame && !active.disposed
        }
    }

    private fun captureViaUiAutomation(request: CaptureRequest): CaptureResult? {
        val rawBitmap: Bitmap = uiAutomation.takeScreenshot() ?: return null
        var scaledBitmap: Bitmap? = null
        var rgbaBitmap: Bitmap? = null
        val stream = bufferPool.acquire()
        var handedOff = false
        try {
            val working: Bitmap = if (request.scale < 1.0f) {
                val targetW = (rawBitmap.width * request.scale).roundToInt().coerceAtLeast(1)
                val targetH = (rawBitmap.height * request.scale).roundToInt().coerceAtLeast(1)
                if (targetW != rawBitmap.width || targetH != rawBitmap.height) {
                    Bitmap.createScaledBitmap(rawBitmap, targetW, targetH, true).also {
                        scaledBitmap = it
                    }
                } else {
                    rawBitmap
                }
            } else {
                rawBitmap
            }

            when (request.format) {
                ImageFormat.RGBA -> {
                    val argb: Bitmap = if (working.config == Bitmap.Config.ARGB_8888) {
                        working
                    } else {
                        working.copy(Bitmap.Config.ARGB_8888, false).also { rgbaBitmap = it }
                    }
                    val byteCount = argb.width * argb.height * 4
                    val dst = stream.prepareExact(byteCount)
                    argb.copyPixelsToBuffer(ByteBuffer.wrap(dst, 0, byteCount))
                }
                ImageFormat.PNG -> {
                    working.compress(Bitmap.CompressFormat.PNG, request.quality, stream)
                }
                ImageFormat.JPEG -> {
                    working.compress(Bitmap.CompressFormat.JPEG, request.quality, stream)
                }
            }

            handedOff = true
            return CaptureResult(
                buffer = stream.rawBuffer,
                length = stream.length,
                width = working.width,
                height = working.height,
                format = request.format,
                source = CaptureSource.UI_AUTOMATION,
                onClose = { bufferPool.release(stream) }
            )
        } finally {
            if (!handedOff) {
                bufferPool.release(stream)
            }
            if (rgbaBitmap != null && rgbaBitmap !== scaledBitmap && rgbaBitmap !== rawBitmap) {
                rgbaBitmap?.recycle()
            }
            if (scaledBitmap != null && scaledBitmap !== rawBitmap) {
                scaledBitmap?.recycle()
            }
            rawBitmap.recycle()
        }
    }

    private fun readDesiredSurfaceConfig(scale: Float): SurfaceConfig? {
        val display = try {
            DisplayManagerGlobal.getInstance().getRealDisplay(DEFAULT_DISPLAY_ID)
        } catch (_: Throwable) {
            null
        } ?: return null
        val size = Point()
        display.getRealSize(size)
        if (size.x <= 0 || size.y <= 0) return null
        val targetW = if (scale == 1.0f) size.x else (size.x * scale).roundToInt().coerceAtLeast(1)
        val targetH = if (scale == 1.0f) size.y else (size.y * scale).roundToInt().coerceAtLeast(1)
        return SurfaceConfig(size.x, size.y, display.rotation, targetW, targetH)
    }

    private fun ensureMirrorHandlerLocked(): Handler {
        val existing = mirrorHandler
        if (existing != null) return existing
        val thread = HandlerThread("ScreenCaptureMirror").apply { start() }
        val handler = Handler(thread.looper)
        mirrorThread = thread
        mirrorHandler = handler
        return handler
    }

    private fun disposeActiveLocked(active: VirtualDisplayState.Active) {
        active.frameLock.withLock {
            active.disposed = true
            active.frameReady.signalAll()
        }
        try { active.imageReader.setOnImageAvailableListener(null, null) } catch (_: Throwable) {}
        try { active.virtualDisplay.release() } catch (_: Throwable) {}
        try { active.imageReader.close() } catch (_: Throwable) {}
        try { active.encodeBitmap?.recycle() } catch (_: Throwable) {}
        active.encodeBitmap = null
    }

    override fun close() {
        synchronized(captureLock) {
            val current = state
            if (current is VirtualDisplayState.Active) {
                disposeActiveLocked(current)
            }
            state = VirtualDisplayState.Closed
            try { mirrorThread?.quitSafely() } catch (_: Throwable) {}
            mirrorThread = null
            mirrorHandler = null
        }
        bufferPool.close()
    }

    companion object {
        private const val MIRROR_DISPLAY_NAME: String = "agent-mirror"
        private const val DEFAULT_DISPLAY_ID: Int = 0
        private const val IMAGE_READER_MAX_IMAGES: Int = 3
        private const val FIRST_FRAME_TIMEOUT_MS: Long = 300L
    }
}
