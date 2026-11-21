package la.shiro.agent

import android.app.UiAutomation
import android.content.Context
import android.graphics.Point
import android.hardware.display.DisplayManagerGlobal
import android.os.Trace
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketException
import java.util.Collections
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.SynchronousQueue
import java.util.concurrent.ThreadFactory
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

class SimpleHttpServer(
    private val port: Int,
    uiAutomation: UiAutomation,
    private val context: Context
) {

    private val running: AtomicBoolean = AtomicBoolean(false)
    private val stopInitiated: AtomicBoolean = AtomicBoolean(false)
    private val stoppedLatch: CountDownLatch = CountDownLatch(1)
    private val activeSockets: MutableSet<Socket> = Collections.newSetFromMap(ConcurrentHashMap())
    private val executor: ThreadPoolExecutor = ThreadPoolExecutor(
        CORE_WORKERS,
        MAX_WORKERS,
        WORKER_KEEP_ALIVE_SECONDS,
        TimeUnit.SECONDS,
        SynchronousQueue(),
        daemonThreadFactory("HttpWorker")
    )
    @Volatile
    private var serverSocket: ServerSocket? = null

    private val uiHierarchy: UiHierarchy = UiHierarchy(uiAutomation)
    private val screenCapture: ScreenCapture = ScreenCapture(uiAutomation)
    private val inputController: InputController = InputController(uiAutomation)
    private val packageService: PackageService = PackageService(context)
    private val deviceInfo: DeviceInfo = DeviceInfo(context)
    private val localeService: LocaleService = LocaleService()

    // Identity of the code this process actually loaded, hashed at startup so a
    // JAR overwritten afterwards cannot make a stale process claim the new build.
    // The version code is not usable for this: its yyMM part is the build date.
    private val jarSha256: String? = try {
        val path = System.getProperty("java.class.path")?.split(':')?.firstOrNull { it.isNotEmpty() }
        if (path == null) null else {
            val digest = java.security.MessageDigest.getInstance("SHA-256")
            java.io.File(path).inputStream().use { input ->
                val buf = ByteArray(64 * 1024)
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    digest.update(buf, 0, n)
                }
            }
            digest.digest().joinToString("") { "%02x".format(it) }
        }
    } catch (_: Throwable) {
        null
    }

    val isAlive: Boolean get() = running.get()

    fun start() {
        val socket = ServerSocket(port, 50, InetAddress.getByName("127.0.0.1"))
        serverSocket = socket
        running.set(true)
        Thread({
            try {
                while (running.get()) {
                    val client: Socket = try {
                        socket.accept()
                    } catch (e: Throwable) {
                        if (running.get()) {
                            System.err.println("[Agent] Accept error: ${e.message}")
                        }
                        continue
                    }
                    try {
                        executor.execute { handleClient(client) }
                    } catch (_: RejectedExecutionException) {
                        rejectBusyClient(client)
                    }
                }
            } finally {
                stop()
            }
        }, "HttpServerThread").start()
    }

    fun stop() {
        if (!stopInitiated.compareAndSet(false, true)) return
        running.set(false)
        try {
            try { serverSocket?.close() } catch (_: Throwable) {}
            for (socket in activeSockets.toList()) {
                try { socket.close() } catch (_: Throwable) {}
            }
            activeSockets.clear()
            try { inputController.close() } catch (_: Throwable) {}
            try { screenCapture.close() } catch (_: Throwable) {}
            executor.shutdown()
        } finally {
            stoppedLatch.countDown()
        }
    }

    fun awaitStop() {
        stoppedLatch.await()
    }

    private fun rejectBusyClient(client: Socket) {
        try {
            client.use { socket ->
                socket.soTimeout = 2_000
                socket.tcpNoDelay = true
                val out = BufferedOutputStream(socket.getOutputStream(), 1024)
                val response = HttpResponse.text(HttpStatus.SERVICE_UNAVAILABLE, "server busy")
                HttpProtocol.writeResponse(out, response, keepAlive = false)
            }
        } catch (_: Throwable) {
        }
    }

    private fun handleClient(socket: Socket) {
        activeSockets.add(socket)
        try {
            socket.use { client ->
                client.tcpNoDelay = true
                val input = BufferedInputStream(client.getInputStream())
                val output = BufferedOutputStream(client.getOutputStream(), OUTPUT_BUFFER_BYTES)
                var requestsHandled = 0

                while (running.get() && requestsHandled < MAX_REQUESTS_PER_CONN) {
                    val request: HttpRequest = try {
                        HttpProtocol.readRequest(input, client, IDLE_TIMEOUT_MS, ACTIVE_TIMEOUT_MS)
                            ?: break
                    } catch (e: PayloadTooLargeException) {
                        writeSafe(
                            output,
                            HttpResponse.text(HttpStatus.PAYLOAD_TOO_LARGE, e.message ?: "payload too large"),
                            keepAlive = false
                        )
                        break
                    } catch (e: MethodNotAllowedException) {
                        val headers = e.allow?.let { mapOf("Allow" to it) } ?: emptyMap()
                        writeSafe(
                            output,
                            HttpResponse.text(
                                HttpStatus.METHOD_NOT_ALLOWED,
                                e.message ?: "method not allowed",
                                headers = headers
                            ),
                            keepAlive = false
                        )
                        break
                    } catch (e: BadRequestException) {
                        writeSafe(
                            output,
                            HttpResponse.text(HttpStatus.BAD_REQUEST, e.message ?: "bad request"),
                            keepAlive = false
                        )
                        break
                    }

                    requestsHandled++
                    val keepAlive = request.keepAlive &&
                        running.get() &&
                        requestsHandled < MAX_REQUESTS_PER_CONN &&
                        activeSockets.size <= MAX_KEEP_ALIVE_SOCKETS

                    val shouldContinue = dispatch(request, output, keepAlive)
                    if (!shouldContinue || !keepAlive) break
                }
            }
        } catch (_: SocketException) {
        } catch (e: Throwable) {
            if (running.get()) {
                System.err.println("[Agent] Handle error: ${e.message}")
            }
        } finally {
            activeSockets.remove(socket)
        }
    }

    // One atrace section per request so a Perfetto trace (atrace_apps: "app_process")
    // shows each endpoint's on-device time next to the input and render work it causes.
    private fun dispatch(
        request: HttpRequest,
        output: BufferedOutputStream,
        keepAlive: Boolean
    ): Boolean {
        Trace.beginSection("agent ${request.path}")
        try {
            return dispatchTraced(request, output, keepAlive)
        } finally {
            Trace.endSection()
        }
    }

    private fun dispatchTraced(
        request: HttpRequest,
        output: BufferedOutputStream,
        keepAlive: Boolean
    ): Boolean {
        val headOnly = request.method == HttpMethod.HEAD
        if (request.path == "/stop") {
            val response = HttpResponse.text(HttpStatus.OK, "stopping")
            HttpProtocol.writeResponse(output, response, keepAlive = false, headOnly = headOnly)
            Thread({ stop() }, "HttpStopThread").apply {
                isDaemon = true
                start()
            }
            return false
        }
        if (request.path == "/screenshot") {
            handleScreenshot(request, output, keepAlive, headOnly)
            return true
        }

        val response: HttpResponse = try {
            route(request)
        } catch (e: BadRequestException) {
            HttpResponse.text(HttpStatus.BAD_REQUEST, e.message ?: "bad request")
        } catch (e: MethodNotAllowedException) {
            val headers = e.allow?.let { mapOf("Allow" to it) } ?: emptyMap()
            HttpResponse.text(
                HttpStatus.METHOD_NOT_ALLOWED,
                e.message ?: "method not allowed",
                headers = headers
            )
        } catch (e: Throwable) {
            System.err.println("[Agent] Route ${request.path} failed: ${e.message}")
            HttpResponse.text(HttpStatus.INTERNAL_SERVER_ERROR, "${request.path} failed: ${e.message}")
        }

        HttpProtocol.writeResponse(output, response, keepAlive = keepAlive, headOnly = headOnly)
        return true
    }

    private fun route(request: HttpRequest): HttpResponse {
        return when (request.path) {
            "/health" -> HttpResponse.text(HttpStatus.OK, "ok")
            "/hierarchy" -> handleHierarchy(request.query)
            "/packages" -> handlePackages(request.query)
            "/signature" -> handleSignature(request.query)
            "/icon" -> handleIcon(request.query)
            "/info" -> handleInfo()
            "/device-info" -> handleDeviceInfo()
            "/locales" -> handleLocales()
            "/set-locale" -> handleSetLocale(request.query)
            "/input/tap" -> handleInputTap(request)
            "/input/swipe" -> handleInputSwipe(request)
            "/input/key" -> handleInputKey(request)
            "/input/text" -> handleInputText(request)
            "/input/global" -> handleInputGlobal(request)
            "/input/batch" -> handleInputBatch(request)
            else -> HttpResponse.text(HttpStatus.NOT_FOUND, "not found: ${request.path}")
        }
    }

    private fun handleScreenshot(
        request: HttpRequest,
        output: BufferedOutputStream,
        keepAlive: Boolean,
        headOnly: Boolean
    ) {
        val captureRequest = try {
            CaptureRequest.parse(request.query)
        } catch (e: BadRequestException) {
            HttpProtocol.writeResponse(
                output,
                HttpResponse.text(HttpStatus.BAD_REQUEST, e.message ?: "bad request"),
                keepAlive = keepAlive,
                headOnly = headOnly
            )
            return
        }

        val result = try {
            screenCapture.capture(captureRequest)
        } catch (e: BadRequestException) {
            HttpProtocol.writeResponse(
                output,
                HttpResponse.text(HttpStatus.BAD_REQUEST, e.message ?: "bad request"),
                keepAlive = keepAlive,
                headOnly = headOnly
            )
            return
        } catch (e: Throwable) {
            System.err.println("[Agent] Screenshot failed: ${e.message}")
            HttpProtocol.writeResponse(
                output,
                HttpResponse.text(HttpStatus.INTERNAL_SERVER_ERROR, "screenshot failed: ${e.message}"),
                keepAlive = keepAlive,
                headOnly = headOnly
            )
            return
        }

        if (result == null) {
            HttpProtocol.writeResponse(
                output,
                HttpResponse.text(HttpStatus.INTERNAL_SERVER_ERROR, "screenshot returned null"),
                keepAlive = keepAlive,
                headOnly = headOnly
            )
            return
        }

        result.use { captured ->
            val response = HttpResponse.bytes(
                status = HttpStatus.OK,
                data = captured.buffer,
                contentType = captured.format.mimeType,
                headers = captured.toHeaders(),
                offset = 0,
                length = captured.length
            )
            HttpProtocol.writeResponse(output, response, keepAlive = keepAlive, headOnly = headOnly)
        }
    }

    private fun handleHierarchy(params: Map<String, String>): HttpResponse {
        val compressed: Boolean = params["compressed"]?.toBooleanStrictOrNull() ?: true
        val xml: String = uiHierarchy.dump(compressed)
        return HttpResponse.text(HttpStatus.OK, xml, "application/xml; charset=utf-8")
    }

    private fun handlePackages(params: Map<String, String>): HttpResponse {
        val userOnly: Boolean = params["user_only"]?.toBooleanStrictOrNull() ?: false
        val json: String = packageService.listPackages(userOnly)
        return HttpResponse.text(HttpStatus.OK, json, "application/json; charset=utf-8")
    }

    private fun handleSignature(params: Map<String, String>): HttpResponse {
        val packageName: String? = params["pkg"]
        if (packageName.isNullOrEmpty()) {
            return HttpResponse.text(HttpStatus.BAD_REQUEST, "missing pkg parameter")
        }
        // 404 rather than an empty 200: an empty body means "installed, no scheme detected",
        // and the host must not report an uninstalled app as unsigned.
        val schemes: String = packageService.getSigningSchemes(packageName)
            ?: return HttpResponse.text(HttpStatus.NOT_FOUND, "package not found: $packageName")
        return HttpResponse.text(HttpStatus.OK, schemes, "text/plain; charset=utf-8")
    }

    private fun handleLocales(): HttpResponse {
        val json: String = localeService.buildLocalesJson()
        return HttpResponse.text(HttpStatus.OK, json, "application/json; charset=utf-8")
    }

    private fun handleSetLocale(params: Map<String, String>): HttpResponse {
        val tag: String? = params["tag"]
        if (tag.isNullOrBlank()) {
            return jsonError(HttpStatus.BAD_REQUEST, "missing tag parameter")
        }
        return try {
            HttpResponse.text(HttpStatus.OK, localeService.setLocale(tag), "application/json; charset=utf-8")
        } catch (e: Throwable) {
            System.err.println("[Agent] Set locale failed for $tag: ${e.message}")
            val status = if (e is IllegalArgumentException) HttpStatus.BAD_REQUEST else HttpStatus.INTERNAL_SERVER_ERROR
            jsonError(status, e.message ?: e.javaClass.simpleName)
        }
    }

    private fun jsonError(status: HttpStatus, message: String): HttpResponse =
        HttpResponse.text(status, localeService.errorJson(message), "application/json; charset=utf-8")

    private fun handleIcon(params: Map<String, String>): HttpResponse {
        val packageName: String? = params["pkg"]
        if (packageName.isNullOrEmpty()) {
            return HttpResponse.text(HttpStatus.BAD_REQUEST, "missing pkg parameter")
        }
        val png: ByteArray? = packageService.getIcon(packageName)
        return if (png != null) {
            HttpResponse.bytes(HttpStatus.OK, png, "image/png")
        } else {
            HttpResponse.text(HttpStatus.NOT_FOUND, "icon not found")
        }
    }

    private fun handleInfo(): HttpResponse {
        val json = JSONObject().apply {
            put("brand", android.os.Build.BRAND)
            put("model", android.os.Build.MODEL)
            put("sdk", android.os.Build.VERSION.SDK_INT)
            put("release", android.os.Build.VERSION.RELEASE)
            put("agentVersion", BuildConfig.VERSION_NAME)
            put("agentVersionCode", BuildConfig.VERSION_CODE)
            put("jarSha256", jarSha256 ?: JSONObject.NULL)
            // Explicit capabilities, so hosts need not infer support from unrelated fields.
            put("features", JSONArray(FEATURES))
            putDisplay(this)
        }.toString()
        return HttpResponse.text(HttpStatus.OK, json, "application/json; charset=utf-8")
    }

    // Current-rotation size, so hosts can compute gesture coordinates without dumping the
    // whole hierarchy. /info doubles as the hosts' liveness probe, so a hidden-API failure
    // on an odd ROM only drops these fields.
    private fun putDisplay(json: JSONObject) {
        try {
            val display = DisplayManagerGlobal.getInstance().getRealDisplay(0) ?: return
            val size = Point()
            display.getRealSize(size)
            json.put("width", size.x)
            json.put("height", size.y)
            json.put("rotation", display.rotation)
        } catch (_: Throwable) {
        }
    }

    private fun handleDeviceInfo(): HttpResponse {
        val json: String = deviceInfo.buildJson()
        return HttpResponse.text(HttpStatus.OK, json, "application/json; charset=utf-8")
    }

    private fun handleInputTap(request: HttpRequest): HttpResponse {
        val command = InputController.parseTap(request)
        return inputResultResponse(inputController.execute(command))
    }

    private fun handleInputSwipe(request: HttpRequest): HttpResponse {
        val command = InputController.parseSwipe(request)
        return inputResultResponse(inputController.execute(command))
    }

    private fun handleInputKey(request: HttpRequest): HttpResponse {
        val command = InputController.parseKey(request)
        return inputResultResponse(inputController.execute(command))
    }

    private fun handleInputText(request: HttpRequest): HttpResponse {
        val command = InputController.parseText(request)
        return inputResultResponse(inputController.execute(command))
    }

    private fun handleInputGlobal(request: HttpRequest): HttpResponse {
        val command = InputController.parseGlobal(request)
        return inputResultResponse(inputController.execute(command))
    }

    private fun handleInputBatch(request: HttpRequest): HttpResponse {
        if (request.method != HttpMethod.POST) {
            throw MethodNotAllowedException("batch requires POST", "POST")
        }
        val batch = InputController.parseBatch(request)
        return inputResultResponse(inputController.executeBatch(batch))
    }

    private fun inputResultResponse(result: InputResult): HttpResponse {
        return HttpResponse.text(
            HttpStatus.OK,
            if (result.ok) """{"ok":true}""" else """{"ok":false}""",
            "application/json; charset=utf-8",
            headers = mapOf("X-Input-Source" to result.source)
        )
    }

    private fun writeSafe(output: BufferedOutputStream, response: HttpResponse, keepAlive: Boolean) {
        try {
            HttpProtocol.writeResponse(output, response, keepAlive = keepAlive)
        } catch (_: Throwable) {
        }
    }

    private fun daemonThreadFactory(prefix: String): ThreadFactory {
        val seq = AtomicInteger(1)
        return ThreadFactory { runnable ->
            Thread(runnable, "$prefix-${seq.getAndIncrement()}").apply {
                isDaemon = true
            }
        }
    }

    companion object {
        private val FEATURES: List<String> = listOf("swipe-easing")
        private const val CORE_WORKERS: Int = 4
        private const val MAX_WORKERS: Int = 32
        private const val WORKER_KEEP_ALIVE_SECONDS: Long = 30L
        private const val MAX_KEEP_ALIVE_SOCKETS: Int = 16
        private const val MAX_REQUESTS_PER_CONN: Int = 1000
        private const val IDLE_TIMEOUT_MS: Int = 15_000
        private const val ACTIVE_TIMEOUT_MS: Int = 30_000
        private const val OUTPUT_BUFFER_BYTES: Int = 64 * 1024
    }
}
