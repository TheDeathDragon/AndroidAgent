package la.shiro.agent

import android.app.UiAutomation
import android.content.Context
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
    private val packageService: PackageService = PackageService(context)
    private val deviceInfo: DeviceInfo = DeviceInfo(context)

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

    private fun dispatch(
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
        val schemes: String = packageService.getSigningSchemes(packageName)
        return HttpResponse.text(HttpStatus.OK, schemes, "text/plain; charset=utf-8")
    }

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
        }.toString()
        return HttpResponse.text(HttpStatus.OK, json, "application/json; charset=utf-8")
    }

    private fun handleDeviceInfo(): HttpResponse {
        val json: String = deviceInfo.buildJson()
        return HttpResponse.text(HttpStatus.OK, json, "application/json; charset=utf-8")
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
