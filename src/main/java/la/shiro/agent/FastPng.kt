package la.shiro.agent

import java.io.OutputStream
import java.util.zip.CRC32
import java.util.zip.Deflater
import java.util.zip.DeflaterOutputStream

// Lossless PNG encoder tuned for screenshots. Bitmap.compress(PNG) uses a fixed
// zlib level and measured ~530 ms of CPU per 720x1640 frame on a Unisoc T606,
// most of a 608 ms /screenshot. Deflate at BEST_SPEED with the Up filter (screens
// are mostly flat colour).
//
// Always RGB: captured frames carry premultiplied alpha that is not 255 (a home
// screen read back at alpha 245), while PNG stores straight alpha, so writing it as
// RGBA renders the screenshot translucent and dark. The panel shows the frame over
// black, and a premultiplied colour is exactly that colour over black, so dropping
// alpha yields what is on screen.
object FastPng {

    private val SIGNATURE = byteArrayOf(0x89.toByte(), 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A)

    // rgba: tightly packed premultiplied RGBA_8888, width * height * 4 bytes from offset 0.
    fun encode(rgba: ByteArray, width: Int, height: Int, out: OutputStream) {
        out.write(SIGNATURE)
        val ihdr = ByteArray(13)
        putInt(ihdr, 0, width)
        putInt(ihdr, 4, height)
        ihdr[8] = 8
        ihdr[9] = 2 // truecolour RGB
        writeChunk(out, "IHDR", ihdr, ihdr.size)

        val idat = ChunkStream(out)
        val deflater = Deflater(Deflater.BEST_SPEED)
        try {
            DeflaterOutputStream(idat, deflater, 64 * 1024).use { z ->
                val stride = width * 3
                var prev = ByteArray(stride)
                var cur = ByteArray(stride)
                val line = ByteArray(stride + 1)
                line[0] = 2 // Up filter
                for (y in 0 until height) {
                    var src = y * width * 4
                    var d = 0
                    for (x in 0 until width) {
                        cur[d] = rgba[src]; cur[d + 1] = rgba[src + 1]; cur[d + 2] = rgba[src + 2]
                        d += 3; src += 4
                    }
                    for (k in 0 until stride) {
                        line[k + 1] = (cur[k] - prev[k]).toByte()
                    }
                    z.write(line, 0, line.size)
                    val t = prev; prev = cur; cur = t
                }
            }
        } finally {
            deflater.end()
        }
        writeChunk(out, "IEND", ByteArray(0), 0)
    }

    private fun putInt(b: ByteArray, at: Int, v: Int) {
        b[at] = (v ushr 24).toByte(); b[at + 1] = (v ushr 16).toByte()
        b[at + 2] = (v ushr 8).toByte(); b[at + 3] = v.toByte()
    }

    private fun writeChunk(out: OutputStream, type: String, data: ByteArray, len: Int) {
        val head = ByteArray(8)
        putInt(head, 0, len)
        val typeBytes = type.toByteArray(Charsets.US_ASCII)
        System.arraycopy(typeBytes, 0, head, 4, 4)
        out.write(head)
        if (len > 0) out.write(data, 0, len)
        val crc = CRC32()
        crc.update(typeBytes)
        if (len > 0) crc.update(data, 0, len)
        val tail = ByteArray(4)
        putInt(tail, 0, crc.value.toInt())
        out.write(tail)
    }

    // Buffers deflate output into IDAT chunks so nothing needs the total size up front.
    private class ChunkStream(private val out: OutputStream) : OutputStream() {
        private val buf = ByteArray(256 * 1024)
        private var n = 0

        override fun write(b: Int) {
            if (n == buf.size) flushChunk()
            buf[n++] = b.toByte()
        }

        override fun write(b: ByteArray, off: Int, len: Int) {
            var o = off
            var left = len
            while (left > 0) {
                if (n == buf.size) flushChunk()
                val take = minOf(left, buf.size - n)
                System.arraycopy(b, o, buf, n, take)
                n += take; o += take; left -= take
            }
        }

        override fun close() {
            flushChunk()
        }

        private fun flushChunk() {
            if (n == 0) return
            writeChunk(out, "IDAT", buf, n)
            n = 0
        }
    }
}
