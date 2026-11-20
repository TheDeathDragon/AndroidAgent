package la.shiro.agent

import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.ByteArrayOutputStream
import java.io.OutputStream
import java.net.Socket
import java.net.SocketTimeoutException
import java.net.URLDecoder
import java.util.Locale

enum class HttpMethod {
    GET,
    POST,
    HEAD
}

enum class HttpStatus(val code: Int, val reason: String) {
    OK(200, "OK"),
    BAD_REQUEST(400, "Bad Request"),
    NOT_FOUND(404, "Not Found"),
    METHOD_NOT_ALLOWED(405, "Method Not Allowed"),
    PAYLOAD_TOO_LARGE(413, "Payload Too Large"),
    INTERNAL_SERVER_ERROR(500, "Internal Server Error"),
    SERVICE_UNAVAILABLE(503, "Service Unavailable")
}

class BadRequestException(message: String) : IllegalArgumentException(message)

class PayloadTooLargeException(message: String) : IllegalArgumentException(message)

class MethodNotAllowedException(
    message: String,
    val allow: String? = null
) : IllegalArgumentException(message)

data class HttpRequest(
    val method: HttpMethod,
    val path: String,
    val query: Map<String, String>,
    val headers: Map<String, String>,
    val body: ByteArray,
    val keepAlive: Boolean
) {
    fun bodyText(): String = String(body, Charsets.UTF_8)

    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is HttpRequest) return false
        return method == other.method &&
            path == other.path &&
            query == other.query &&
            headers == other.headers &&
            body.contentEquals(other.body) &&
            keepAlive == other.keepAlive
    }

    override fun hashCode(): Int {
        var result = method.hashCode()
        result = 31 * result + path.hashCode()
        result = 31 * result + query.hashCode()
        result = 31 * result + headers.hashCode()
        result = 31 * result + body.contentHashCode()
        result = 31 * result + keepAlive.hashCode()
        return result
    }
}

sealed class ResponseBody {
    abstract val contentLength: Int
    abstract fun writeTo(out: OutputStream)

    class Bytes(
        val data: ByteArray,
        val offset: Int = 0,
        override val contentLength: Int = data.size
    ) : ResponseBody() {
        override fun writeTo(out: OutputStream) {
            if (contentLength > 0) {
                out.write(data, offset, contentLength)
            }
        }
    }

    object Empty : ResponseBody() {
        override val contentLength: Int = 0
        override fun writeTo(out: OutputStream) = Unit
    }
}

data class HttpResponse(
    val status: HttpStatus,
    val contentType: String = "text/plain; charset=utf-8",
    val body: ResponseBody = ResponseBody.Empty,
    val headers: Map<String, String> = emptyMap()
) {
    companion object {
        fun text(
            status: HttpStatus,
            text: String,
            contentType: String = "text/plain; charset=utf-8",
            headers: Map<String, String> = emptyMap()
        ): HttpResponse {
            val bytes = text.toByteArray(Charsets.UTF_8)
            return HttpResponse(status, contentType, ResponseBody.Bytes(bytes), headers)
        }

        fun bytes(
            status: HttpStatus,
            data: ByteArray,
            contentType: String,
            headers: Map<String, String> = emptyMap(),
            offset: Int = 0,
            length: Int = data.size
        ): HttpResponse {
            return HttpResponse(
                status,
                contentType,
                ResponseBody.Bytes(data, offset, length),
                headers
            )
        }
    }
}

object HttpProtocol {

    const val MAX_LINE_BYTES: Int = 8192
    const val MAX_HEADERS: Int = 64
    const val MAX_BODY_BYTES: Int = 1024 * 1024

    private val EMPTY_BYTES: ByteArray = ByteArray(0)

    fun readRequest(
        input: BufferedInputStream,
        socket: Socket,
        idleTimeoutMs: Int,
        activeTimeoutMs: Int
    ): HttpRequest? {
        socket.soTimeout = idleTimeoutMs
        val firstByte: Int = try {
            input.read()
        } catch (_: SocketTimeoutException) {
            return null
        }
        if (firstByte == -1) return null
        socket.soTimeout = activeTimeoutMs

        val requestLine: String = readLineBytes(input, firstByte)
        if (requestLine.isEmpty()) {
            throw BadRequestException("empty request line")
        }

        val parts: List<String> = requestLine.split(' ')
        if (parts.size != 3) {
            throw BadRequestException("malformed request line")
        }
        val method: HttpMethod = parseMethod(parts[0])
        val target: String = parts[1]
        val version: String = parts[2]
        if (version != "HTTP/1.1" && version != "HTTP/1.0") {
            throw BadRequestException("unsupported HTTP version: $version")
        }

        val (path, queryRaw) = splitPathAndQuery(target)
        val query: Map<String, String> = parseQueryParams(queryRaw)
        val headers: Map<String, String> = readHeaders(input)

        val transferEncoding: String? = headers["transfer-encoding"]
        if (transferEncoding != null && transferEncoding.lowercase(Locale.US).contains("chunked")) {
            throw BadRequestException("chunked transfer-encoding is not supported")
        }

        val contentLength: Int = parseContentLength(headers["content-length"])
        val body: ByteArray = if (contentLength > 0) {
            readExactBytes(input, contentLength)
        } else {
            EMPTY_BYTES
        }

        val keepAlive: Boolean = resolveKeepAlive(version, headers["connection"])
        return HttpRequest(method, path, query, headers, body, keepAlive)
    }

    fun writeResponse(
        out: BufferedOutputStream,
        response: HttpResponse,
        keepAlive: Boolean,
        headOnly: Boolean = false
    ) {
        val sb = StringBuilder(256)
        sb.append("HTTP/1.1 ")
            .append(response.status.code)
            .append(' ')
            .append(response.status.reason)
            .append("\r\n")
        sb.append("Content-Type: ").append(response.contentType).append("\r\n")
        sb.append("Content-Length: ").append(response.body.contentLength).append("\r\n")
        for ((name, value) in response.headers) {
            sb.append(name).append(": ").append(value).append("\r\n")
        }
        sb.append("Connection: ").append(if (keepAlive) "keep-alive" else "close").append("\r\n")
        sb.append("\r\n")
        out.write(sb.toString().toByteArray(Charsets.US_ASCII))
        if (!headOnly) {
            response.body.writeTo(out)
        }
        out.flush()
    }

    private fun parseMethod(raw: String): HttpMethod = when (raw) {
        "GET" -> HttpMethod.GET
        "POST" -> HttpMethod.POST
        "HEAD" -> HttpMethod.HEAD
        else -> throw MethodNotAllowedException("unsupported method: $raw", "GET, POST, HEAD")
    }

    private fun readHeaders(input: BufferedInputStream): Map<String, String> {
        val headers = LinkedHashMap<String, String>()
        var count = 0
        while (true) {
            val firstByte = input.read()
            if (firstByte == -1) {
                throw BadRequestException("unexpected EOF in headers")
            }
            val line = readLineBytes(input, firstByte)
            if (line.isEmpty()) return headers
            if (count >= MAX_HEADERS) {
                throw BadRequestException("too many headers (>$MAX_HEADERS)")
            }
            val colon = line.indexOf(':')
            if (colon <= 0) {
                throw BadRequestException("malformed header")
            }
            val key = line.substring(0, colon).trim().lowercase(Locale.US)
            if (key.isEmpty()) {
                throw BadRequestException("malformed header name")
            }
            val value = line.substring(colon + 1).trim()
            val existing = headers[key]
            if (existing == null) {
                headers[key] = value
            } else if (key == "content-length") {
                if (existing != value) {
                    throw BadRequestException("conflicting content-length headers")
                }
            } else {
                headers[key] = "$existing, $value"
            }
            count++
        }
    }

    private fun parseContentLength(raw: String?): Int {
        if (raw == null) return 0
        val parsed: Long = raw.toLongOrNull()
            ?: throw BadRequestException("invalid content-length: $raw")
        if (parsed < 0L) {
            throw BadRequestException("negative content-length: $raw")
        }
        if (parsed > MAX_BODY_BYTES.toLong()) {
            throw PayloadTooLargeException("request body exceeds $MAX_BODY_BYTES bytes")
        }
        return parsed.toInt()
    }

    private fun readExactBytes(input: BufferedInputStream, length: Int): ByteArray {
        val data = ByteArray(length)
        var offset = 0
        while (offset < length) {
            val read = input.read(data, offset, length - offset)
            if (read == -1) {
                throw BadRequestException("unexpected EOF in request body")
            }
            offset += read
        }
        return data
    }

    private fun readLineBytes(input: BufferedInputStream, firstByte: Int): String {
        if (firstByte == '\n'.code) return ""
        val buf = ByteArrayOutputStream(128)
        var current = firstByte
        while (true) {
            if (current == '\n'.code) break
            if (buf.size() >= MAX_LINE_BYTES) {
                throw BadRequestException("line exceeds $MAX_LINE_BYTES bytes")
            }
            buf.write(current)
            current = input.read()
            if (current == -1) {
                throw BadRequestException("unexpected EOF while reading line")
            }
        }
        val bytes = buf.toByteArray()
        val end = if (bytes.isNotEmpty() && bytes[bytes.size - 1] == '\r'.code.toByte()) {
            bytes.size - 1
        } else {
            bytes.size
        }
        return String(bytes, 0, end, Charsets.UTF_8)
    }

    private fun resolveKeepAlive(version: String, connectionHeader: String?): Boolean {
        val tokens: List<String> = connectionHeader
            ?.lowercase(Locale.US)
            ?.split(',')
            ?.map { it.trim() }
            ?: emptyList()
        return if (version == "HTTP/1.1") {
            !tokens.contains("close")
        } else {
            tokens.contains("keep-alive")
        }
    }

    private fun splitPathAndQuery(target: String): Pair<String, String> {
        val index = target.indexOf('?')
        return if (index >= 0) {
            target.substring(0, index) to target.substring(index + 1)
        } else {
            target to ""
        }
    }

    private fun parseQueryParams(query: String): Map<String, String> {
        if (query.isEmpty()) return emptyMap()
        val result = LinkedHashMap<String, String>()
        for (param in query.split('&')) {
            val eqIndex = param.indexOf('=')
            if (eqIndex > 0) {
                try {
                    val key = URLDecoder.decode(param.substring(0, eqIndex), "UTF-8")
                    val value = URLDecoder.decode(param.substring(eqIndex + 1), "UTF-8")
                    result[key] = value
                } catch (_: IllegalArgumentException) {
                    throw BadRequestException("malformed query encoding")
                }
            }
        }
        return result
    }
}
