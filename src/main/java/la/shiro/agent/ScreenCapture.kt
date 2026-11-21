package la.shiro.agent

import android.annotation.SuppressLint
import android.annotation.TargetApi
import android.app.UiAutomation
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.graphics.Point
import android.graphics.Rect
import android.hardware.display.DisplayManager
import android.hardware.display.DisplayManagerGlobal
import android.hardware.display.VirtualDisplay
import android.media.Image
import android.media.ImageReader
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.view.Surface
import java.io.ByteArrayOutputStream
import java.lang.reflect.InvocationTargetException
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

data class CropRect(
    val x: Int,
    val y: Int,
    val width: Int,
    val height: Int
) {
    fun clampTo(imageWidth: Int, imageHeight: Int): CropRect {
        if (x >= imageWidth || y >= imageHeight) {
            throw BadRequestException("crop origin outside image bounds")
        }
        val outW = minOf(width, imageWidth - x)
        val outH = minOf(height, imageHeight - y)
        return CropRect(x, y, outW, outH)
    }

    companion object {
        fun parse(raw: String): CropRect {
            val parts = raw.split(',')
            if (parts.size != 4) {
                throw BadRequestException("invalid crop format, expected x,y,w,h: $raw")
            }
            val x = parts[0].trim().toIntOrNull()
                ?: throw BadRequestException("invalid crop x: $raw")
            val y = parts[1].trim().toIntOrNull()
                ?: throw BadRequestException("invalid crop y: $raw")
            val w = parts[2].trim().toIntOrNull()
                ?: throw BadRequestException("invalid crop width: $raw")
            val h = parts[3].trim().toIntOrNull()
                ?: throw BadRequestException("invalid crop height: $raw")
            if (x < 0 || y < 0 || w <= 0 || h <= 0) {
                throw BadRequestException("invalid crop bounds (require x>=0, y>=0, w>0, h>0): $raw")
            }
            return CropRect(x, y, w, h)
        }
    }
}

data class CaptureRequest(
    val format: ImageFormat = ImageFormat.PNG,
    val quality: Int = format.defaultQuality,
    val scale: Float = 1.0f,
    val fast: Boolean = false,
    val crop: CropRect? = null
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

            val crop = params["crop"]?.let { raw ->
                CropRect.parse(raw)
            }

            return CaptureRequest(format, quality, scale, fast, crop)
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
    val cacheHit: Boolean = false,
    private val onClose: () -> Unit
) : AutoCloseable {

    private val closed = AtomicBoolean(false)

    fun toHeaders(): Map<String, String> = mapOf(
        "X-Width" to width.toString(),
        "X-Height" to height.toString(),
        "X-Format" to format.wireName,
        "X-Capture-Source" to source.wireName,
        "X-Cache-Hit" to cacheHit.toString()
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

internal interface MirrorDisplay : AutoCloseable

// Keep API 34+ static DisplayManager.createVirtualDisplay isolated so R8 does not inline it into ScreenCapture on API 29-33.
internal object VirtualDisplayFactory {
    fun create(
        name: String,
        displayWidth: Int,
        displayHeight: Int,
        targetWidth: Int,
        targetHeight: Int,
        displayIdToMirror: Int,
        layerStack: Int,
        surface: Surface
    ): MirrorDisplay? {
        if (Build.VERSION.SDK_INT >= 34) {
            try {
                val vd = createApi34(name, targetWidth, targetHeight, displayIdToMirror, surface)
                    ?: return null
                val released = AtomicBoolean(false)
                return object : MirrorDisplay {
                    override fun close() {
                        if (released.compareAndSet(false, true)) {
                            vd.release()
                        }
                    }
                }
            } catch (_: NoSuchMethodError) {
                // Fall back to SurfaceControl reflection when this build lacks the API 34+ static overload.
            }
        }
        return createViaSurfaceControl(
            name = name,
            displayWidth = displayWidth,
            displayHeight = displayHeight,
            targetWidth = targetWidth,
            targetHeight = targetHeight,
            layerStack = layerStack,
            surface = surface
        )
    }

    @TargetApi(34)
    private fun createApi34(
        name: String,
        width: Int,
        height: Int,
        displayIdToMirror: Int,
        surface: Surface
    ): VirtualDisplay? {
        return DisplayManager.createVirtualDisplay(name, width, height, displayIdToMirror, surface)
    }

    @SuppressLint("PrivateApi", "DiscouragedPrivateApi")
    private fun createViaSurfaceControl(
        name: String,
        displayWidth: Int,
        displayHeight: Int,
        targetWidth: Int,
        targetHeight: Int,
        layerStack: Int,
        surface: Surface
    ): MirrorDisplay? {
        val sc = Class.forName("android.view.SurfaceControl")
        val createDisplay = sc.getMethod("createDisplay", String::class.java, Boolean::class.javaPrimitiveType)
        val openTransaction = sc.getMethod("openTransaction")
        val closeTransaction = sc.getMethod("closeTransaction")
        val setDisplaySurface = sc.getMethod("setDisplaySurface", IBinder::class.java, Surface::class.java)
        val setDisplayProjection = sc.getMethod(
            "setDisplayProjection",
            IBinder::class.java,
            Int::class.javaPrimitiveType,
            Rect::class.java,
            Rect::class.java
        )
        val setDisplayLayerStack = sc.getMethod("setDisplayLayerStack", IBinder::class.java, Int::class.javaPrimitiveType)
        val destroyDisplay = sc.getMethod("destroyDisplay", IBinder::class.java)

        // API 29-30 allow shell uid to create secure mirror displays; API 31-33 reject secure=true without READ_FRAME_BUFFER.
        val trySecure = Build.VERSION.SDK_INT < 31
        val token: IBinder = try {
            createDisplay.invoke(null, name, trySecure) as? IBinder
        } catch (e: InvocationTargetException) {
            if (trySecure && e.cause is SecurityException) {
                try {
                    createDisplay.invoke(null, name, false) as? IBinder
                } catch (retryError: InvocationTargetException) {
                    throw retryError.cause ?: retryError
                }
            } else {
                throw e.cause ?: e
            }
        } ?: return null

        try {
            openTransaction.invoke(null)
            try {
                setDisplaySurface.invoke(null, token, surface)
                setDisplayProjection.invoke(
                    null,
                    token,
                    Surface.ROTATION_0,
                    Rect(0, 0, displayWidth, displayHeight),
                    Rect(0, 0, targetWidth, targetHeight)
                )
                setDisplayLayerStack.invoke(null, token, layerStack)
            } finally {
                closeTransaction.invoke(null)
            }
        } catch (t: Throwable) {
            try {
                destroyDisplay.invoke(null, token)
            } catch (_: Throwable) {
            }
            throw (if (t is InvocationTargetException) t.cause ?: t else t)
        }

        val released = AtomicBoolean(false)
        return object : MirrorDisplay {
            override fun close() {
                if (released.compareAndSet(false, true)) {
                    try {
                        destroyDisplay.invoke(null, token)
                    } catch (_: Throwable) {
                    }
                }
            }
        }
    }
}

class ScreenCapture(private val uiAutomation: UiAutomation) : AutoCloseable {

    private data class SurfaceConfig(
        val displayWidth: Int,
        val displayHeight: Int,
        val rotation: Int,
        val layerStack: Int,
        val targetWidth: Int,
        val targetHeight: Int
    )

    private class EncodedCache(
        val frameVersion: Long,
        val format: ImageFormat,
        val quality: Int,
        val crop: CropRect?,
        val width: Int,
        val height: Int,
        val data: ByteArray
    )

    private sealed class VirtualDisplayState {
        object Uninitialized : VirtualDisplayState()
        class Active(
            val config: SurfaceConfig,
            val mirrorDisplay: MirrorDisplay,
            val imageReader: ImageReader,
            val frameLock: ReentrantLock,
            val frameReady: Condition,
            var latestRgba: ByteArray,
            var backRgba: ByteArray,
            var hasFrame: Boolean,
            var firstFrameTimedOut: Boolean = false,
            var frameVersion: Long = 0L,
            var encodeBitmap: Bitmap?,
            var cropBitmap: Bitmap? = null,
            var encodedCache: EncodedCache? = null,
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
                val targetW = active.config.targetWidth
                val targetH = active.config.targetHeight
                val clampedCrop = request.crop?.clampTo(targetW, targetH)
                val effectiveCrop = if (
                    clampedCrop != null &&
                    (clampedCrop.x != 0 || clampedCrop.y != 0 || clampedCrop.width != targetW || clampedCrop.height != targetH)
                ) {
                    clampedCrop
                } else {
                    null
                }
                val outW = effectiveCrop?.width ?: targetW
                val outH = effectiveCrop?.height ?: targetH
                val outByteCount = outW * outH * 4

                val currentVersion = active.frameLock.withLock {
                    if (active.disposed || !active.hasFrame) -1L else active.frameVersion
                }
                if (currentVersion < 0L) return null

                if (request.format != ImageFormat.RGBA) {
                    val cached = active.encodedCache
                    if (
                        cached != null &&
                        cached.frameVersion == currentVersion &&
                        cached.format == request.format &&
                        cached.quality == request.quality &&
                        cached.crop == effectiveCrop
                    ) {
                        val dst = stream.prepareExact(cached.data.size)
                        System.arraycopy(cached.data, 0, dst, 0, cached.data.size)
                        handedOff = true
                        return CaptureResult(
                            buffer = stream.rawBuffer,
                            length = cached.data.size,
                            width = cached.width,
                            height = cached.height,
                            format = request.format,
                            source = CaptureSource.VIRTUAL_DISPLAY,
                            cacheHit = true,
                            onClose = { bufferPool.release(stream) }
                        )
                    }
                }

                when (request.format) {
                    ImageFormat.RGBA -> {
                        val dst = stream.prepareExact(outByteCount)
                        val copied = active.frameLock.withLock {
                            if (active.disposed || !active.hasFrame) {
                                false
                            } else {
                                copyRgbaRegion(active.latestRgba, targetW, effectiveCrop, dst, outByteCount)
                                true
                            }
                        }
                        if (!copied) return null
                    }
                    ImageFormat.PNG -> {
                        val rgba = ByteArray(outByteCount)
                        val encodedVersion = active.frameLock.withLock {
                            if (active.disposed || !active.hasFrame) {
                                -1L
                            } else {
                                copyRgbaRegion(active.latestRgba, targetW, effectiveCrop, rgba, outByteCount)
                                active.frameVersion
                            }
                        }
                        if (encodedVersion < 0L) return null
                        FastPng.encode(rgba, outW, outH, stream)
                        if (stream.length <= MAX_ENCODED_CACHE_BYTES) {
                            active.encodedCache = EncodedCache(
                                frameVersion = encodedVersion,
                                format = request.format,
                                quality = request.quality,
                                crop = effectiveCrop,
                                width = outW,
                                height = outH,
                                data = stream.rawBuffer.copyOf(stream.length)
                            )
                        }
                    }
                    ImageFormat.JPEG -> {
                        val bitmap: Bitmap
                        val encodedVersion: Long
                        if (effectiveCrop == null) {
                            bitmap = active.encodeBitmap
                                ?: Bitmap.createBitmap(targetW, targetH, Bitmap.Config.ARGB_8888).also {
                                    active.encodeBitmap = it
                                }
                            encodedVersion = active.frameLock.withLock {
                                if (active.disposed || !active.hasFrame) {
                                    -1L
                                } else {
                                    bitmap.copyPixelsFromBuffer(ByteBuffer.wrap(active.latestRgba, 0, outByteCount))
                                    active.frameVersion
                                }
                            }
                            if (encodedVersion < 0L) return null
                        } else {
                            val existingCrop = active.cropBitmap
                            bitmap = if (existingCrop != null && existingCrop.width == outW && existingCrop.height == outH) {
                                existingCrop
                            } else {
                                try { existingCrop?.recycle() } catch (_: Throwable) {}
                                Bitmap.createBitmap(outW, outH, Bitmap.Config.ARGB_8888).also {
                                    active.cropBitmap = it
                                }
                            }
                            val scratch = stream.prepareExact(outByteCount)
                            encodedVersion = active.frameLock.withLock {
                                if (active.disposed || !active.hasFrame) {
                                    -1L
                                } else {
                                    copyRgbaRegion(active.latestRgba, targetW, effectiveCrop, scratch, outByteCount)
                                    active.frameVersion
                                }
                            }
                            if (encodedVersion < 0L) return null
                            bitmap.copyPixelsFromBuffer(ByteBuffer.wrap(scratch, 0, outByteCount))
                            stream.reset()
                        }

                        bitmap.compress(Bitmap.CompressFormat.JPEG, request.quality, stream)
                        if (stream.length <= MAX_ENCODED_CACHE_BYTES) {
                            active.encodedCache = EncodedCache(
                                frameVersion = encodedVersion,
                                format = request.format,
                                quality = request.quality,
                                crop = effectiveCrop,
                                width = outW,
                                height = outH,
                                data = stream.rawBuffer.copyOf(stream.length)
                            )
                        }
                    }
                }

                handedOff = true
                return CaptureResult(
                    buffer = stream.rawBuffer,
                    length = stream.length,
                    width = outW,
                    height = outH,
                    format = request.format,
                    source = CaptureSource.VIRTUAL_DISPLAY,
                    cacheHit = false,
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
        val mirrorDisplay = try {
            VirtualDisplayFactory.create(
                MIRROR_DISPLAY_NAME,
                desired.displayWidth,
                desired.displayHeight,
                desired.targetWidth,
                desired.targetHeight,
                DEFAULT_DISPLAY_ID,
                desired.layerStack,
                reader.surface
            )
        } catch (t: Throwable) {
            try { reader.close() } catch (_: Throwable) {}
            val reason = "${t.javaClass.simpleName}: ${t.message}"
            state = VirtualDisplayState.Unavailable(reason)
            System.err.println("[Agent] VirtualDisplay unavailable, falling back to UiAutomation: $reason")
            return null
        }

        if (mirrorDisplay == null) {
            try { reader.close() } catch (_: Throwable) {}
            state = VirtualDisplayState.Unavailable("createVirtualDisplay returned null")
            return null
        }

        val frameLock = ReentrantLock()
        val active = VirtualDisplayState.Active(
            config = desired,
            mirrorDisplay = mirrorDisplay,
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
                active.firstFrameTimedOut = false
                active.frameVersion++
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
            if (active.hasFrame && !active.disposed) return@withLock true
            if (active.firstFrameTimedOut || active.disposed) return@withLock false
            while (!active.hasFrame && !active.disposed && remainingNs > 0L) {
                try {
                    remainingNs = active.frameReady.awaitNanos(remainingNs)
                } catch (_: InterruptedException) {
                    Thread.currentThread().interrupt()
                    return@withLock false
                }
            }
            if (!active.hasFrame && !active.disposed) {
                active.firstFrameTimedOut = true
            }
            active.hasFrame && !active.disposed
        }
    }

    private fun copyRgbaRegion(
        src: ByteArray,
        srcWidth: Int,
        crop: CropRect?,
        dst: ByteArray,
        outByteCount: Int
    ) {
        if (crop == null) {
            System.arraycopy(src, 0, dst, 0, outByteCount)
            return
        }
        val srcStride = srcWidth * 4
        val dstStride = crop.width * 4
        if (crop.x == 0 && crop.width == srcWidth) {
            System.arraycopy(src, crop.y * srcStride, dst, 0, outByteCount)
            return
        }
        val srcXOffset = crop.x * 4
        for (row in 0 until crop.height) {
            val srcOffset = (crop.y + row) * srcStride + srcXOffset
            System.arraycopy(src, srcOffset, dst, row * dstStride, dstStride)
        }
    }

    private fun captureViaUiAutomation(request: CaptureRequest): CaptureResult? {
        val rawBitmap: Bitmap = uiAutomation.takeScreenshot() ?: return null
        var scaledBitmap: Bitmap? = null
        var croppedBitmap: Bitmap? = null
        var rgbaBitmap: Bitmap? = null
        val stream = bufferPool.acquire()
        var handedOff = false
        try {
            val scaled: Bitmap = if (request.scale < 1.0f) {
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

            val working: Bitmap = if (request.crop != null) {
                val clamped = request.crop.clampTo(scaled.width, scaled.height)
                if (clamped.x != 0 || clamped.y != 0 || clamped.width != scaled.width || clamped.height != scaled.height) {
                    Bitmap.createBitmap(scaled, clamped.x, clamped.y, clamped.width, clamped.height).also {
                        croppedBitmap = it
                    }
                } else {
                    scaled
                }
            } else {
                scaled
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
                    val argb: Bitmap = if (working.config == Bitmap.Config.ARGB_8888) {
                        working
                    } else {
                        working.copy(Bitmap.Config.ARGB_8888, false).also { rgbaBitmap = it }
                    }
                    val rgba = ByteArray(argb.width * argb.height * 4)
                    argb.copyPixelsToBuffer(ByteBuffer.wrap(rgba))
                    FastPng.encode(rgba, argb.width, argb.height, stream)
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
                cacheHit = false,
                onClose = { bufferPool.release(stream) }
            )
        } finally {
            if (!handedOff) {
                bufferPool.release(stream)
            }
            if (rgbaBitmap != null && rgbaBitmap !== croppedBitmap && rgbaBitmap !== scaledBitmap && rgbaBitmap !== rawBitmap) {
                rgbaBitmap?.recycle()
            }
            if (croppedBitmap != null && croppedBitmap !== scaledBitmap && croppedBitmap !== rawBitmap) {
                croppedBitmap?.recycle()
            }
            if (scaledBitmap != null && scaledBitmap !== rawBitmap) {
                scaledBitmap?.recycle()
            }
            rawBitmap.recycle()
        }
    }

    private fun readDesiredSurfaceConfig(scale: Float): SurfaceConfig? {
        val dmGlobal = DisplayManagerGlobal.getInstance()
        val display = try {
            dmGlobal.getRealDisplay(DEFAULT_DISPLAY_ID)
        } catch (_: Throwable) {
            null
        } ?: return null
        val size = Point()
        display.getRealSize(size)
        if (size.x <= 0 || size.y <= 0) return null
        val layerStack = try {
            dmGlobal.getDisplayInfo(DEFAULT_DISPLAY_ID)?.layerStack ?: 0
        } catch (_: Throwable) {
            0
        }
        val targetW = if (scale == 1.0f) size.x else (size.x * scale).roundToInt().coerceAtLeast(1)
        val targetH = if (scale == 1.0f) size.y else (size.y * scale).roundToInt().coerceAtLeast(1)
        return SurfaceConfig(size.x, size.y, display.rotation, layerStack, targetW, targetH)
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
        try { active.mirrorDisplay.close() } catch (_: Throwable) {}
        try { active.imageReader.close() } catch (_: Throwable) {}
        try { active.encodeBitmap?.recycle() } catch (_: Throwable) {}
        active.encodeBitmap = null
        try { active.cropBitmap?.recycle() } catch (_: Throwable) {}
        active.cropBitmap = null
        active.encodedCache = null
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
        private const val MAX_ENCODED_CACHE_BYTES: Int = 4 * 1024 * 1024
    }
}
