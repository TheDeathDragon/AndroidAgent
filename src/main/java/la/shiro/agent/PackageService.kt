@file:Suppress("DEPRECATION")
@file:SuppressLint("PrivateApi", "DiscouragedPrivateApi")

package la.shiro.agent

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.content.pm.ResolveInfo
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.drawable.BitmapDrawable
import android.graphics.drawable.Drawable
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.RandomAccessFile
import java.security.MessageDigest
import java.util.concurrent.ConcurrentHashMap
import java.util.zip.ZipFile

class PackageService(private val context: Context) {

    companion object {
        private val APK_SIG_BLOCK_MAGIC: ByteArray = "APK Sig Block 42".toByteArray(Charsets.US_ASCII)
        private const val SCHEME_V2_ID: Long = 0x7109871a
        private const val SCHEME_V3_ID: Long = 0xf05368c0L
        private const val SCHEME_V31_ID: Long = 0x1b93ad61
        // ApplicationInfo.FLAG_SUSPENDED — hidden API, value is 1 shl 30.
        private const val FLAG_SUSPENDED: Int = 0x40000000
        private const val ICON_CACHE_MAX_ENTRIES: Int = 200
    }

    // Cache dir is versioned by agent VERSION_CODE so an agent upgrade always invalidates
    // PNGs produced by an earlier (possibly buggy) icon-rendering path. Key by APK length
    // alone wouldn't catch that — the APK hasn't changed, only how we rasterize it has.
    private val iconCacheDir: File =
        File("/data/local/tmp/agent-icons-v${BuildConfig.VERSION_CODE}").apply { mkdirs() }
    // Use the framework PackageManager, not IPackageManager.Stub — the hidden AIDL picked
    // up `long`-flag overloads at API 33, so SDK 36 stubs throw NoSuchMethodError on
    // older devices and the binder call bypasses Exception catches.
    private val packageManager: PackageManager = context.packageManager
    // APK Sig Block scanning does disk I/O per APK (~5–20 ms each). We only need the
    // result when the user opens the info popup for one package, so cache by path+size
    // and compute lazily via getSignatureInfo(). Previously the list endpoint ran this
    // for every package upfront, costing 2–10 s on a large package set.
    private val signatureCache: ConcurrentHashMap<String, String> = ConcurrentHashMap()

    fun listPackages(userOnly: Boolean): String {
        val flags: Int = PackageManager.GET_ACTIVITIES or PackageManager.GET_SIGNING_CERTIFICATES
        val packages: List<PackageInfo> = packageManager.getInstalledPackages(flags)
        val result = JSONArray()
        for (pkg in packages) {
            val appInfo: ApplicationInfo = pkg.applicationInfo ?: continue
            val isSystem: Boolean = (appInfo.flags and ApplicationInfo.FLAG_SYSTEM) != 0
            if (userOnly && isSystem) continue
            val label: String = loadLabel(appInfo)
            val mainActivity: String? = resolveMainActivity(pkg.packageName)
            val apkFile = File(appInfo.sourceDir)
            val iconCacheKey: String = "${pkg.packageName}.${apkFile.length()}.png"
            val obj = JSONObject()
            obj.put("packageName", pkg.packageName)
            obj.put("label", label)
            obj.put("versionName", pkg.versionName ?: "")
            obj.put("versionCode", pkg.longVersionCode)
            obj.put("apkPath", appInfo.sourceDir)
            obj.put("apkSize", apkFile.length())
            obj.put("system", isSystem)
            obj.put("enabled", appInfo.enabled)
            obj.put("suspended", (appInfo.flags and FLAG_SUSPENDED) != 0)
            obj.put("uid", appInfo.uid)
            obj.put("firstInstallTime", pkg.firstInstallTime)
            obj.put("lastUpdateTime", pkg.lastUpdateTime)
            obj.put("targetSdkVersion", appInfo.targetSdkVersion)
            obj.put("minSdkVersion", appInfo.minSdkVersion)
            obj.put("mainActivity", mainActivity ?: JSONObject.NULL)
            obj.put("iconCacheKey", iconCacheKey)
            obj.put("signatureSha256", extractSignatureSha256(pkg))
            obj.put("dataDir", appInfo.dataDir ?: JSONObject.NULL)
            result.put(obj)
        }
        return result.toString()
    }

    private fun extractSignatureSha256(pkg: PackageInfo): String {
        return try {
            val signingInfo = pkg.signingInfo ?: return ""
            val cert: ByteArray = if (signingInfo.hasMultipleSigners()) {
                signingInfo.apkContentsSigners?.firstOrNull()?.toByteArray() ?: return ""
            } else {
                signingInfo.signingCertificateHistory?.firstOrNull()?.toByteArray() ?: return ""
            }
            val digest: ByteArray = MessageDigest.getInstance("SHA-256").digest(cert)
            digest.joinToString(":") { "%02X".format(it) }
        } catch (_: Throwable) {
            ""
        }
    }

    fun getSigningSchemes(packageName: String): String {
        return try {
            val appInfo: ApplicationInfo = packageManager.getApplicationInfo(packageName, 0)
            val apkPath: String = appInfo.sourceDir
            val apkSize: Long = File(apkPath).length()
            val cacheKey = "$apkPath:$apkSize"
            signatureCache.getOrPut(cacheKey) { detectSigningSchemes(apkPath) }
        } catch (_: Throwable) {
            ""
        }
    }

    private fun detectSigningSchemes(apkPath: String): String {
        val schemes: MutableList<String> = mutableListOf()
        val file = File(apkPath)
        if (!file.exists()) return ""
        try {
            ZipFile(file).use { zip ->
                val hasSf: Boolean = zip.entries().asSequence().any {
                    it.name.startsWith("META-INF/") && it.name.endsWith(".SF")
                }
                if (hasSf) schemes.add("V1")
            }
        } catch (_: Exception) { }
        try {
            RandomAccessFile(file, "r").use { raf ->
                val eocdOffset: Long = findEocdOffset(raf) ?: return@use
                raf.seek(eocdOffset + 16)
                val cdOffset: Long = readUint32LE(raf)
                if (cdOffset < 24) return@use
                raf.seek(cdOffset - 16)
                val magic = ByteArray(16)
                raf.readFully(magic)
                if (!magic.contentEquals(APK_SIG_BLOCK_MAGIC)) return@use
                raf.seek(cdOffset - 24)
                val blockSize: Long = readUint64LE(raf)
                val blockStart: Long = cdOffset - blockSize - 8
                if (blockStart < 0) return@use
                var pos: Long = blockStart + 8
                val pairsEnd: Long = cdOffset - 24
                while (pos + 12 <= pairsEnd) {
                    raf.seek(pos)
                    val pairLen: Long = readUint64LE(raf)
                    if (pairLen < 4) break
                    val id: Long = readUint32LE(raf)
                    when (id) {
                        SCHEME_V2_ID -> schemes.add("V2")
                        SCHEME_V3_ID -> schemes.add("V3")
                        SCHEME_V31_ID -> schemes.add("V3.1")
                    }
                    pos += 8 + pairLen
                }
            }
        } catch (_: Exception) { }
        if (File("$apkPath.idsig").exists()) schemes.add("V4")
        return schemes.joinToString("+")
    }

    private fun findEocdOffset(raf: RandomAccessFile): Long? {
        val fileLen: Long = raf.length()
        val searchLen: Int = minOf(fileLen, 65557L).toInt()
        val buf = ByteArray(searchLen)
        val start: Long = fileLen - searchLen
        raf.seek(start)
        raf.readFully(buf)
        for (i in buf.size - 22 downTo 0) {
            if (buf[i] == 0x50.toByte() && buf[i + 1] == 0x4B.toByte() &&
                buf[i + 2] == 0x05.toByte() && buf[i + 3] == 0x06.toByte()
            ) {
                return start + i
            }
        }
        return null
    }

    private fun readUint32LE(raf: RandomAccessFile): Long {
        val b = ByteArray(4)
        raf.readFully(b)
        return (b[0].toLong() and 0xFF) or
                ((b[1].toLong() and 0xFF) shl 8) or
                ((b[2].toLong() and 0xFF) shl 16) or
                ((b[3].toLong() and 0xFF) shl 24)
    }

    private fun readUint64LE(raf: RandomAccessFile): Long {
        val b = ByteArray(8)
        raf.readFully(b)
        return (b[0].toLong() and 0xFF) or
                ((b[1].toLong() and 0xFF) shl 8) or
                ((b[2].toLong() and 0xFF) shl 16) or
                ((b[3].toLong() and 0xFF) shl 24) or
                ((b[4].toLong() and 0xFF) shl 32) or
                ((b[5].toLong() and 0xFF) shl 40) or
                ((b[6].toLong() and 0xFF) shl 48) or
                ((b[7].toLong() and 0xFF) shl 56)
    }

    fun getIcon(packageName: String): ByteArray? {
        try {
            val appInfo: ApplicationInfo = packageManager.getApplicationInfo(packageName, 0)
            val apkFile = File(appInfo.sourceDir)
            val cacheKey: String = "$packageName.${apkFile.length()}.png"
            val cacheFile = File(iconCacheDir, cacheKey)
            if (cacheFile.exists()) {
                cacheFile.setLastModified(System.currentTimeMillis())
                return cacheFile.readBytes()
            }
            val drawable: Drawable = loadAppIconDrawable(packageName, appInfo) ?: return null
            val png: ByteArray = renderDrawableToPng(drawable)
            cacheFile.writeBytes(png)
            evictIconCacheIfNeeded()
            return png
        } catch (t: Throwable) {
            System.err.println("[Agent] Icon load failed for $packageName: $t")
            return null
        }
    }

    private fun evictIconCacheIfNeeded() {
        val files: Array<File> = iconCacheDir.listFiles() ?: return
        if (files.size <= ICON_CACHE_MAX_ENTRIES) return
        files.sortBy { it.lastModified() }
        val toRemove: Int = files.size - ICON_CACHE_MAX_ENTRIES
        for (i in 0 until toRemove) {
            try { files[i].delete() } catch (_: Throwable) { }
        }
    }

    // app_process runs with the system_server-style context, whose Resources object
    // cannot expand AdaptiveIconDrawable XML (mipmap-anydpi-v26) from a foreign
    // package — XML inflation needs the target package's own Theme to resolve attrs
    // referenced by adaptive backgrounds. createPackageContext binds Resources +
    // Theme to the target package, after which getDrawable returns a real adaptive
    // icon instead of the framework's default Android pictograph.
    private fun loadAppIconDrawable(packageName: String, appInfo: ApplicationInfo): Drawable? {
        try {
            val pkgCtx: Context = context.createPackageContext(
                packageName,
                Context.CONTEXT_IGNORE_SECURITY or Context.CONTEXT_INCLUDE_CODE
            )
            val res: android.content.res.Resources = pkgCtx.resources
            val iconRes: Int = pkgCtx.applicationInfo.icon
            if (iconRes != 0) {
                val d: Drawable? = res.getDrawableForDensity(iconRes, 480, pkgCtx.theme)
                    ?: res.getDrawable(iconRes, pkgCtx.theme)
                if (d != null) return d
            }
        } catch (_: Throwable) { }
        try {
            return packageManager.getApplicationIcon(packageName)
        } catch (_: Throwable) { }
        try {
            return appInfo.loadIcon(packageManager)
        } catch (_: Throwable) { }
        return null
    }

    private fun loadLabel(appInfo: ApplicationInfo): String {
        return try {
            val resources = context.packageManager.getResourcesForApplication(appInfo)
            if (appInfo.labelRes != 0) {
                resources.getString(appInfo.labelRes)
            } else {
                appInfo.packageName
            }
        } catch (_: Exception) {
            appInfo.packageName
        }
    }

    private fun resolveMainActivity(packageName: String): String? {
        return try {
            val intent = Intent(Intent.ACTION_MAIN).apply {
                addCategory(Intent.CATEGORY_LAUNCHER)
                setPackage(packageName)
            }
            val resolveInfo: ResolveInfo? = packageManager.resolveActivity(intent, 0)
            if (resolveInfo?.activityInfo != null) {
                "$packageName/${resolveInfo.activityInfo.name}"
            } else {
                null
            }
        } catch (_: Throwable) {
            null
        }
    }

    private fun renderDrawableToPng(drawable: Drawable): ByteArray {
        val size: Int = 192
        val bitmap: Bitmap = if (drawable is BitmapDrawable && drawable.bitmap != null) {
            Bitmap.createScaledBitmap(drawable.bitmap, size, size, true)
        } else {
            val bmp = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
            val canvas = Canvas(bmp)
            drawable.setBounds(0, 0, size, size)
            drawable.draw(canvas)
            bmp
        }
        val stream = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.PNG, 20, stream)
        bitmap.recycle()
        return stream.toByteArray()
    }
}
