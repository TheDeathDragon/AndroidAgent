package la.shiro.agent

import android.app.UiAutomation
import android.content.Context
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.URLDecoder
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

class SimpleHttpServer(
    private val port: Int,
    uiAutomation: UiAutomation,
    private val context: Context
) {

    private val running: AtomicBoolean = AtomicBoolean(false)
    private val executor = Executors.newFixedThreadPool(4)
    private var serverSocket: ServerSocket? = null
    private val uiHierarchy: UiHierarchy = UiHierarchy(uiAutomation)
    private val screenCapture: ScreenCapture = ScreenCapture(uiAutomation)
    private val packageService: PackageService = PackageService(context)
    private val deviceInfo: DeviceInfo = DeviceInfo(context)

    val isAlive: Boolean get() = running.get()

    fun start() {
        running.set(true)
        Thread({
            // Bind to the loopback interface only. adb forward on the host
            // tunnels to adbd, which connects on-device via 127.0.0.1, so this
            // keeps the endpoints working while blocking same-network peers
            // from reaching /screenshot, /hierarchy, /packages etc.
            val socket = ServerSocket(port, 50, InetAddress.getByName("127.0.0.1"))
            serverSocket = socket
            while (running.get()) {
                try {
                    val client: Socket = socket.accept()
                    executor.submit { handleClient(client) }
                } catch (e: Throwable) {
                    if (running.get()) {
                        System.err.println("[Agent] Accept error: ${e.message}")
                    }
                }
            }
        }, "HttpServerThread").start()
    }

    fun stop() {
        running.set(false)
        serverSocket?.close()
        executor.shutdown()
    }

    private fun handleClient(socket: Socket) {
        socket.use { client ->
            try {
                client.soTimeout = 30_000
                val input = BufferedReader(InputStreamReader(client.getInputStream()))
                val requestLine: String = input.readLine() ?: return
                val parts: List<String> = requestLine.split(" ")
                if (parts.size < 2) return
                val method: String = parts[0]
                val fullPath: String = parts[1]
                val pathAndQuery: Pair<String, String> = splitPathAndQuery(fullPath)
                val path: String = pathAndQuery.first
                val params: Map<String, String> = parseQueryParams(pathAndQuery.second)
                consumeHeaders(input)
                val output: OutputStream = client.getOutputStream()
                route(method, path, params, output)
            } catch (e: Throwable) {
                System.err.println("[Agent] Handle error: ${e.message}")
            }
        }
    }

    private fun route(method: String, path: String, params: Map<String, String>, output: OutputStream) {
        when (path) {
            "/health" -> respondText(output, 200, "ok")
            "/hierarchy" -> handleHierarchy(params, output)
            "/screenshot" -> handleScreenshot(params, output)
            "/packages" -> handlePackages(params, output)
            "/signature" -> handleSignature(params, output)
            "/icon" -> handleIcon(params, output)
            "/info" -> handleInfo(output)
            "/device-info" -> handleDeviceInfo(output)
            "/stop" -> {
                respondText(output, 200, "stopping")
                stop()
            }
            else -> respondText(output, 404, "not found: $path")
        }
    }

    private fun handleHierarchy(params: Map<String, String>, output: OutputStream) {
        try {
            val compressed: Boolean = params["compressed"]?.toBooleanStrictOrNull() ?: true
            val xml: String = uiHierarchy.dump(compressed)
            respondText(output, 200, xml, "application/xml; charset=utf-8")
        } catch (e: Throwable) {
            System.err.println("[Agent] Hierarchy dump failed: ${e.message}")
            respondText(output, 500, "hierarchy dump failed: ${e.message}")
        }
    }

    private fun handleScreenshot(params: Map<String, String>, output: OutputStream) {
        try {
            val quality: Int = params["quality"]?.toIntOrNull() ?: 100
            val png: ByteArray? = screenCapture.capture(quality)
            if (png != null) {
                respondBinary(output, 200, png, "image/png")
            } else {
                respondText(output, 500, "screenshot returned null")
            }
        } catch (e: Throwable) {
            System.err.println("[Agent] Screenshot failed: ${e.message}")
            respondText(output, 500, "screenshot failed: ${e.message}")
        }
    }

    private fun handlePackages(params: Map<String, String>, output: OutputStream) {
        try {
            val userOnly: Boolean = params["user_only"]?.toBooleanStrictOrNull() ?: false
            val json: String = packageService.listPackages(userOnly)
            respondText(output, 200, json, "application/json; charset=utf-8")
        } catch (e: Throwable) {
            System.err.println("[Agent] Package list failed: ${e.message}")
            respondText(output, 500, "package list failed: ${e.message}")
        }
    }

    private fun handleSignature(params: Map<String, String>, output: OutputStream) {
        val packageName: String? = params["pkg"]
        if (packageName.isNullOrEmpty()) {
            respondText(output, 400, "missing pkg parameter")
            return
        }
        try {
            val schemes: String = packageService.getSigningSchemes(packageName)
            respondText(output, 200, schemes, "text/plain; charset=utf-8")
        } catch (e: Throwable) {
            System.err.println("[Agent] Signature scan failed for $packageName: $e")
            respondText(output, 500, "signature failed: ${e.message}")
        }
    }

    private fun handleIcon(params: Map<String, String>, output: OutputStream) {
        val packageName: String? = params["pkg"]
        if (packageName.isNullOrEmpty()) {
            respondText(output, 400, "missing pkg parameter")
            return
        }
        try {
            val png: ByteArray? = packageService.getIcon(packageName)
            if (png != null) {
                respondBinary(output, 200, png, "image/png")
            } else {
                respondText(output, 404, "icon not found")
            }
        } catch (e: Throwable) {
            System.err.println("[Agent] Icon load failed: ${e.message}")
            respondText(output, 500, "icon failed: ${e.message}")
        }
    }

    private fun handleInfo(output: OutputStream) {
        try {
            val json: String = buildDeviceInfoJson()
            respondText(output, 200, json, "application/json; charset=utf-8")
        } catch (e: Throwable) {
            respondText(output, 500, "info failed: ${e.message}")
        }
    }

    private fun handleDeviceInfo(output: OutputStream) {
        try {
            val json: String = deviceInfo.buildJson()
            respondText(output, 200, json, "application/json; charset=utf-8")
        } catch (e: Throwable) {
            System.err.println("[Agent] Device info build failed: ${e.message}")
            respondText(output, 500, "device-info failed: ${e.message}")
        }
    }

    private fun buildDeviceInfoJson(): String {
        val brand: String = android.os.Build.BRAND
        val model: String = android.os.Build.MODEL
        val sdk: Int = android.os.Build.VERSION.SDK_INT
        val release: String = android.os.Build.VERSION.RELEASE
        val version: String = BuildConfig.VERSION_NAME
        val versionCode: Int = BuildConfig.VERSION_CODE
        return """{"brand":"$brand","model":"$model","sdk":$sdk,"release":"$release","agentVersion":"$version","agentVersionCode":$versionCode}"""
    }

    private fun respondText(output: OutputStream, status: Int, body: String, contentType: String = "text/plain; charset=utf-8") {
        val bodyBytes: ByteArray = body.toByteArray(Charsets.UTF_8)
        val header: String = buildResponseHeader(status, contentType, bodyBytes.size)
        output.write(header.toByteArray(Charsets.US_ASCII))
        output.write(bodyBytes)
        output.flush()
    }

    private fun respondBinary(output: OutputStream, status: Int, body: ByteArray, contentType: String) {
        val header: String = buildResponseHeader(status, contentType, body.size)
        output.write(header.toByteArray(Charsets.US_ASCII))
        output.write(body)
        output.flush()
    }

    private fun buildResponseHeader(status: Int, contentType: String, contentLength: Int): String {
        val statusText: String = when (status) {
            200 -> "OK"
            404 -> "Not Found"
            500 -> "Internal Server Error"
            else -> "Unknown"
        }
        return "HTTP/1.1 $status $statusText\r\n" +
                "Content-Type: $contentType\r\n" +
                "Content-Length: $contentLength\r\n" +
                "Connection: close\r\n" +
                "\r\n"
    }

    private fun splitPathAndQuery(fullPath: String): Pair<String, String> {
        val index: Int = fullPath.indexOf('?')
        return if (index >= 0) {
            fullPath.substring(0, index) to fullPath.substring(index + 1)
        } else {
            fullPath to ""
        }
    }

    private fun parseQueryParams(query: String): Map<String, String> {
        if (query.isEmpty()) return emptyMap()
        return query.split("&").mapNotNull { param ->
            val eqIndex: Int = param.indexOf('=')
            if (eqIndex > 0) {
                val key: String = param.substring(0, eqIndex)
                val value: String = URLDecoder.decode(param.substring(eqIndex + 1), "UTF-8")
                key to value
            } else {
                null
            }
        }.toMap()
    }

    private fun consumeHeaders(input: BufferedReader) {
        var count: Int = 0
        while (count < MAX_HEADERS) {
            val line: String? = input.readLine()
            if (line.isNullOrEmpty()) return
            if (line.indexOf(':') <= 0) {
                throw IllegalArgumentException("malformed header: $line")
            }
            count++
        }
        throw IllegalArgumentException("too many headers (>$MAX_HEADERS)")
    }
    companion object {
        private const val MAX_HEADERS: Int = 64
    }
}
