@file:Suppress("DEPRECATION")
@file:SuppressLint("PrivateApi", "DiscouragedPrivateApi", "MissingPermission")

package la.shiro.agent

import android.annotation.SuppressLint
import android.app.ActivityManager
import android.content.Context
import android.hardware.Camera
import android.hardware.Sensor
import android.hardware.SensorManager
import android.hardware.display.DisplayManager
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.Network
import android.net.NetworkCapabilities
import android.os.Build
import android.os.StatFs
import android.os.storage.StorageManager
import android.telephony.SubscriptionInfo
import android.telephony.SubscriptionManager
import android.telephony.TelephonyManager
import android.util.DisplayMetrics
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

// Aggregates everything DevCheck-style information panels want to render: device IDs,
// build identifiers, RAM/storage, displays, CPU/GPU, sensors, cameras, networking,
// SIM, Treble, system properties. Each section is wrapped in try-catch so a single
// API failure cannot poison the whole response — the host parses partial output and
// renders "N/A" for missing fields.
class DeviceInfo(private val context: Context) {

    fun buildJson(): String {
        val root = JSONObject()
        root.put("device", section { buildDevice() })
        root.put("build", section { buildBuild() })
        root.put("ram", section { buildRam() })
        root.put("storage", sectionArray { buildStorage() })
        root.put("display", sectionArray { buildDisplay() })
        root.put("cpu", section { buildCpu() })
        root.put("gpu", section { buildGpu() })
        root.put("sensors", sectionArray { buildSensors() })
        root.put("cameraV1", sectionArray { buildCameraV1() })
        root.put("cameras", sectionArray { buildCameras() })
        root.put("network", section { buildNetwork() })
        root.put("sim", sectionArray { buildSim() })
        root.put("treble", section { buildTreble() })
        root.put("features", sectionArray { buildFeatures() })
        root.put("properties", section { buildProperties() })
        return root.toString()
    }

    private fun buildFeatures(): JSONArray {
        // PackageManager.getSystemAvailableFeatures() returns the FEATURE_* manifest the
        // device declares. This is the same list `pm list features` produces, but we keep
        // the version/reqGlEsVersion fields that pm strips out.
        val arr = JSONArray()
        try {
            val features = context.packageManager.systemAvailableFeatures
            for (f in features) {
                val obj = JSONObject()
                if (!f.name.isNullOrEmpty()) {
                    obj.put("name", f.name)
                } else if (f.reqGlEsVersion != 0) {
                    obj.put("name", "reqGlEsVersion")
                } else {
                    continue
                }
                if (f.version != 0) obj.put("version", f.version)
                if (f.reqGlEsVersion != 0) {
                    val major = (f.reqGlEsVersion shr 16) and 0xFFFF
                    val minor = f.reqGlEsVersion and 0xFFFF
                    obj.put("glEsVersion", "$major.$minor")
                }
                arr.put(obj)
            }
        } catch (e: Throwable) {
            arr.put(JSONObject().put("error", e.message ?: e.javaClass.simpleName))
        }
        return arr
    }

    private fun section(block: () -> JSONObject): JSONObject {
        return try { block() } catch (e: Throwable) {
            JSONObject().put("error", e.message ?: e.javaClass.simpleName)
        }
    }

    private fun sectionArray(block: () -> JSONArray): JSONArray {
        return try { block() } catch (_: Throwable) { JSONArray() }
    }

    private fun buildDevice(): JSONObject {
        val o = JSONObject()
        o.put("manufacturer", Build.MANUFACTURER)
        o.put("brand", Build.BRAND)
        o.put("model", Build.MODEL)
        o.put("device", Build.DEVICE)
        o.put("product", Build.PRODUCT)
        o.put("hardware", Build.HARDWARE)
        o.put("board", Build.BOARD)
        o.put("fingerprint", Build.FINGERPRINT)
        o.put("display", Build.DISPLAY)
        o.put("id", Build.ID)
        o.put("host", Build.HOST)
        o.put("user", Build.USER)
        o.put("type", Build.TYPE)
        o.put("tags", Build.TAGS)
        o.put("bootloader", Build.BOOTLOADER)
        o.put("radio", safe { Build.getRadioVersion() })
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            o.put("socManufacturer", Build.SOC_MANUFACTURER)
            o.put("socModel", Build.SOC_MODEL)
            o.put("sku", Build.SKU)
            o.put("odmSku", Build.ODM_SKU)
        }
        o.put("supportedAbis", JSONArray(Build.SUPPORTED_ABIS.toList()))
        o.put("supported32Abis", JSONArray(Build.SUPPORTED_32_BIT_ABIS.toList()))
        o.put("supported64Abis", JSONArray(Build.SUPPORTED_64_BIT_ABIS.toList()))
        return o
    }

    private fun buildBuild(): JSONObject {
        val o = JSONObject()
        o.put("release", Build.VERSION.RELEASE)
        o.put("sdkInt", Build.VERSION.SDK_INT)
        o.put("codename", Build.VERSION.CODENAME)
        o.put("incremental", Build.VERSION.INCREMENTAL)
        o.put("securityPatch", safe { Build.VERSION.SECURITY_PATCH })
        o.put("baseOs", safe { Build.VERSION.BASE_OS })
        o.put("previewSdk", Build.VERSION.PREVIEW_SDK_INT)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            o.put("mediaPerformanceClass", Build.VERSION.MEDIA_PERFORMANCE_CLASS)
        }
        return o
    }

    private fun buildRam(): JSONObject {
        val o = JSONObject()
        val am = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val info = ActivityManager.MemoryInfo()
        am.getMemoryInfo(info)
        o.put("totalMem", info.totalMem)
        o.put("availMem", info.availMem)
        o.put("threshold", info.threshold)
        o.put("lowMemory", info.lowMemory)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            o.put("advertisedMem", info.advertisedMem)
        }
        return o
    }

    private fun buildStorage(): JSONArray {
        val arr = JSONArray()
        val sm = context.getSystemService(Context.STORAGE_SERVICE) as? StorageManager
        val volumes = try { sm?.storageVolumes } catch (_: Throwable) { null }
        if (volumes != null) {
            for (v in volumes) {
                val obj = JSONObject()
                obj.put("description", safe { v.getDescription(context) })
                obj.put("uuid", v.uuid ?: JSONObject.NULL)
                obj.put("isPrimary", v.isPrimary)
                obj.put("isRemovable", v.isRemovable)
                obj.put("isEmulated", v.isEmulated)
                obj.put("state", safe { v.state })
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    obj.put("directory", v.directory?.absolutePath ?: JSONObject.NULL)
                    obj.put("mediaStoreVolumeName", v.mediaStoreVolumeName ?: JSONObject.NULL)
                    val dir: File? = v.directory
                    if (dir != null && dir.exists()) {
                        try {
                            val stat = StatFs(dir.absolutePath)
                            obj.put("totalBytes", stat.totalBytes)
                            obj.put("availableBytes", stat.availableBytes)
                        } catch (_: Throwable) { /* unreadable */ }
                    }
                }
                arr.put(obj)
            }
        }
        // /data fallback for the case where StorageManager.getStorageVolumes() returned
        // nothing (shell uid on some Android 14+ builds). When it did return volumes,
        // the primary emulated one already maps to the same backing partition as /data,
        // so adding /data here would duplicate the bytes/availability.
        if (arr.length() == 0) {
            try {
                val data = StatFs("/data")
                val obj = JSONObject()
                obj.put("description", "/data")
                obj.put("totalBytes", data.totalBytes)
                obj.put("availableBytes", data.availableBytes)
                obj.put("isPrimary", false)
                obj.put("isEmulated", false)
                obj.put("isRemovable", false)
                arr.put(obj)
            } catch (_: Throwable) { /* unmounted */ }
        }
        return arr
    }

    private fun buildDisplay(): JSONArray {
        val arr = JSONArray()
        val dm = context.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager
        for (display in dm.displays) {
            val obj = JSONObject()
            obj.put("id", display.displayId)
            obj.put("name", display.name)
            obj.put("rotation", display.rotation)
            obj.put("refreshRate", display.refreshRate)
            obj.put("isHdr", display.isHdr)
            obj.put("isWideColorGamut", display.isWideColorGamut)
            val mode = display.mode
            if (mode != null) {
                obj.put("modeWidth", mode.physicalWidth)
                obj.put("modeHeight", mode.physicalHeight)
                obj.put("modeRefreshRate", mode.refreshRate)
            }
            val supported = JSONArray()
            for (m in display.supportedModes) {
                val mo = JSONObject()
                mo.put("id", m.modeId)
                mo.put("width", m.physicalWidth)
                mo.put("height", m.physicalHeight)
                mo.put("refreshRate", m.refreshRate)
                supported.put(mo)
            }
            obj.put("supportedModes", supported)
            try {
                val caps = display.hdrCapabilities
                if (caps != null) {
                    val hdr = JSONArray()
                    for (t in caps.supportedHdrTypes) {
                        hdr.put(when (t) {
                            1 -> "HDR10"
                            2 -> "HLG"
                            3 -> "HDR10+"
                            4 -> "DOLBY_VISION"
                            else -> "TYPE_$t"
                        })
                    }
                    obj.put("hdrTypes", hdr)
                    obj.put("hdrLuminanceMin", caps.desiredMinLuminance)
                    obj.put("hdrLuminanceMaxAvg", caps.desiredMaxAverageLuminance)
                    obj.put("hdrLuminanceMax", caps.desiredMaxLuminance)
                }
            } catch (_: Throwable) { /* HDR API not present */ }
            val metrics = DisplayMetrics()
            display.getRealMetrics(metrics)
            obj.put("widthPx", metrics.widthPixels)
            obj.put("heightPx", metrics.heightPixels)
            obj.put("density", metrics.density)
            obj.put("densityDpi", metrics.densityDpi)
            obj.put("xdpi", metrics.xdpi)
            obj.put("ydpi", metrics.ydpi)
            arr.put(obj)
        }
        return arr
    }

    private fun buildCpu(): JSONObject {
        val o = JSONObject()
        val cores = Runtime.getRuntime().availableProcessors()
        o.put("cores", cores)
        // /proc/cpuinfo gives a vendor/model line per core. Extract one representative
        // "Hardware" / "model name" line; per-core entries are usually identical except
        // for "processor: N".
        val cpuInfo = try { File("/proc/cpuinfo").readText() } catch (_: Throwable) { "" }
        val hardware = Regex("Hardware\\s*:\\s*(.+)").find(cpuInfo)?.groupValues?.getOrNull(1)?.trim()
        val modelName = Regex("model name\\s*:\\s*(.+)").find(cpuInfo)?.groupValues?.getOrNull(1)?.trim()
        val processor = Regex("Processor\\s*:\\s*(.+)").find(cpuInfo)?.groupValues?.getOrNull(1)?.trim()
        if (!hardware.isNullOrEmpty()) o.put("hardware", hardware)
        if (!modelName.isNullOrEmpty()) o.put("modelName", modelName)
        if (!processor.isNullOrEmpty()) o.put("processor", processor)
        val perCore = JSONArray()
        for (i in 0 until cores) {
            val item = JSONObject()
            item.put("core", i)
            item.put("minKHz", readLong("/sys/devices/system/cpu/cpu$i/cpufreq/cpuinfo_min_freq"))
            item.put("maxKHz", readLong("/sys/devices/system/cpu/cpu$i/cpufreq/cpuinfo_max_freq"))
            item.put("curKHz", readLong("/sys/devices/system/cpu/cpu$i/cpufreq/scaling_cur_freq"))
            item.put("governor", readString("/sys/devices/system/cpu/cpu$i/cpufreq/scaling_governor"))
            perCore.put(item)
        }
        o.put("perCore", perCore)
        return o
    }

    private fun buildGpu(): JSONObject {
        val o = JSONObject()
        // SurfaceFlinger dump exposes the active GLES driver string via "GLES:" line.
        // The agent runs as shell, so the dumpsys binder call succeeds.
        try {
            val proc = ProcessBuilder("dumpsys", "SurfaceFlinger")
                .redirectErrorStream(true).start()
            val text = proc.inputStream.bufferedReader().use { it.readText() }
            proc.waitFor()
            val gles = Regex("GLES:\\s*([^\\n]+)").find(text)?.groupValues?.getOrNull(1)?.trim()
            if (!gles.isNullOrEmpty()) {
                o.put("glesRaw", gles)
                val parts = gles.split(",").map { it.trim() }
                if (parts.isNotEmpty()) o.put("vendor", parts.getOrNull(0))
                if (parts.size > 1) o.put("renderer", parts.getOrNull(1))
                if (parts.size > 2) o.put("version", parts.getOrNull(2))
            }
            val ext = Regex("GLES extensions:\\s*([^\\n]+)").find(text)?.groupValues?.getOrNull(1)?.trim()
            if (!ext.isNullOrEmpty()) o.put("extensionsCount", ext.split(" ").size)
        } catch (_: Throwable) { /* dumpsys not available */ }
        return o
    }

    private fun buildSensors(): JSONArray {
        val arr = JSONArray()
        val sm = context.getSystemService(Context.SENSOR_SERVICE) as? SensorManager ?: return arr
        for (s in sm.getSensorList(Sensor.TYPE_ALL)) {
            val obj = JSONObject()
            obj.put("name", s.name)
            obj.put("vendor", s.vendor)
            obj.put("type", s.type)
            obj.put("stringType", s.stringType)
            obj.put("version", s.version)
            obj.put("power", s.power)
            obj.put("maxRange", s.maximumRange)
            obj.put("resolution", s.resolution)
            obj.put("minDelay", s.minDelay)
            obj.put("maxDelay", s.maxDelay)
            obj.put("isWakeUp", s.isWakeUpSensor)
            obj.put("isDynamic", s.isDynamicSensor)
            obj.put("reportingMode", s.reportingMode)
            arr.put(obj)
        }
        return arr
    }

    private fun buildCameraV1(): JSONArray {
        // Camera.getCameraInfo() goes through a binder call to the camera service which
        // applies the same package/uid check as Camera2 ("Given calling package android
        // does not match caller's uid 2000"). Camera.getNumberOfCameras() is a static
        // count and works fine. So: take the count from v1, then read facing/orientation
        // from the dumpsys API1 info blocks for ids 0..count-1. v1 only ever sees the
        // dense FRONT/BACK ids, unlike v2 which exposes physical ids 20/30/etc.
        val arr = JSONArray()
        val count = try { Camera.getNumberOfCameras() } catch (_: Throwable) { 0 }
        if (count <= 0) return arr
        val text = try { exec("dumpsys", "media.camera") } catch (_: Throwable) { return arr }
        val header = Regex("==\\s*Camera HAL device device@[\\d.]+/[^/]+/(\\d+)\\s*\\(.*?\\)\\s*static information:\\s*==")
        val matches = header.findAll(text).toList()
        for (i in matches.indices) {
            val m = matches[i]
            val id = m.groupValues[1].toIntOrNull() ?: continue
            if (id >= count) continue
            val end = if (i + 1 < matches.size) matches[i + 1].range.first else text.length
            val block = text.substring(m.range.last + 1, end)
            val obj = JSONObject()
            obj.put("index", id)
            // dumpsys API1 prints "Back"/"Front" while API2 metadata stores "BACK"/"FRONT".
            // Normalize to uppercase here so cameraV1 and cameras share a single style.
            Regex("Facing:\\s*(\\S+)").find(block)?.let { obj.put("facing", it.groupValues[1].uppercase()) }
            Regex("Orientation:\\s*(\\d+)").find(block)?.let { obj.put("orientation", it.groupValues[1].toInt()) }
            Regex("Has a flash unit:\\s*(true|false)").find(block)?.let { obj.put("hasFlash", it.groupValues[1].toBoolean()) }
            arr.put(obj)
        }
        return arr
    }

    private fun buildCameras(): JSONArray {
        // CameraManager rejects shell uid (2000) with "Given calling package android does
        // not match caller's uid 2000" because Camera2 enforces a package/uid match for
        // every characteristics call. Parse `dumpsys media.camera` instead — that uses a
        // binder dump path which is open to the shell user. We filter to the
        // user-facing cameras only (the dumpsys also lists physical sub-cameras with
        // synthetic ids 20/30/etc., which aren't useful to surface to QA).
        val arr = JSONArray()
        try {
            val text = exec("dumpsys", "media.camera")
            val publicCount = Regex("Number of public camera devices visible to API1:\\s*(\\d+)")
                .find(text)?.groupValues?.getOrNull(1)?.toIntOrNull() ?: Int.MAX_VALUE
            val header = Regex("==\\s*Camera HAL device device@[\\d.]+/[^/]+/(\\d+)\\s*\\(.*?\\)\\s*static information:\\s*==")
            val matches = header.findAll(text).toList()
            for (i in matches.indices) {
                val m = matches[i]
                val id = m.groupValues[1].toIntOrNull() ?: continue
                if (id >= publicCount) continue
                val end = if (i + 1 < matches.size) matches[i + 1].range.first else text.length
                val block = text.substring(m.range.last + 1, end)
                val obj = JSONObject()
                obj.put("id", id.toString())
                addBracketByteValue(block, obj, "hardwareLevel", "android.info.supportedHardwareLevel")
                addBracketByteValue(block, obj, "facing", "android.lens.facing")
                Regex("Facing:\\s*(\\S+)").find(block)?.let {
                    if (!obj.has("facing")) obj.put("facing", it.groupValues[1].uppercase())
                }
                Regex("Orientation:\\s*(\\d+)").find(block)?.let { obj.put("orientation", it.groupValues[1]) }
                Regex("Has a flash unit:\\s*(true|false)").find(block)?.let { obj.put("hasFlash", it.groupValues[1].toBoolean()) }
                addFloatArrayJoined(block, obj, "focalLengths", "android.lens.info.availableFocalLengths")
                addFloatArrayJoined(block, obj, "apertures", "android.lens.info.availableApertures")
                Regex("android\\.sensor\\.info\\.physicalSize\\s*\\(\\w+\\):\\s*float\\[2\\]\\s*\\[\\s*([\\d.]+)\\s+([\\d.]+)\\s*\\]")
                    .find(block)?.let {
                        obj.put("sensorWidthMm", it.groupValues[1].toDoubleOrNull() ?: 0.0)
                        obj.put("sensorHeightMm", it.groupValues[2].toDoubleOrNull() ?: 0.0)
                    }
                Regex("android\\.sensor\\.info\\.pixelArraySize\\s*\\(\\w+\\):\\s*int32\\[2\\]\\s*\\[\\s*(\\d+)\\s+(\\d+)\\s*\\]")
                    .find(block)?.let {
                        obj.put("pixelArrayWidth", it.groupValues[1].toInt())
                        obj.put("pixelArrayHeight", it.groupValues[2].toInt())
                    }
                Regex("android\\.lens\\.info\\.minimumFocusDistance\\s*\\(\\w+\\):\\s*float\\[1\\]\\s*\\[\\s*([\\d.]+)\\s*\\]")
                    .find(block)?.let { obj.put("minFocusDistance", it.groupValues[1].toDoubleOrNull() ?: 0.0) }
                obj.put("photoSizes", parseJpegOutputSizes(block))
                arr.put(obj)
            }
        } catch (e: Throwable) {
            arr.put(JSONObject().put("error", e.message ?: e.javaClass.simpleName))
        }
        return arr
    }

    // The streamConfigurations stanza in dumpsys media.camera is multi-line:
    //   android.scaler.availableStreamConfigurations (d000a): int32[N]
    //     [33 4208 3120 OUTPUT ]
    //     [33 4000 3000 OUTPUT ]
    //     ...
    // Format 33 = HAL_PIXEL_FORMAT_BLOB (JPEG). We list the JPEG OUTPUT entries
    // sorted desc by area so users see the highest-resolution capture sizes first.
    private fun parseJpegOutputSizes(block: String): JSONArray {
        val arr = JSONArray()
        val anchor = Regex("android\\.scaler\\.availableStreamConfigurations\\s*\\(\\w+\\):\\s*int32\\[\\d+\\]")
            .find(block) ?: return arr
        val rest = block.substring(anchor.range.last + 1)
        val rowRe = Regex("\\[\\s*(\\d+)\\s+(\\d+)\\s+(\\d+)\\s+(OUTPUT|INPUT)\\s*\\]")
        val sizes = sortedSetOf<Pair<Int, Int>>(compareByDescending { it.first.toLong() * it.second })
        for (m in rowRe.findAll(rest)) {
            val fmt = m.groupValues[1].toInt()
            val w = m.groupValues[2].toInt()
            val h = m.groupValues[3].toInt()
            val dir = m.groupValues[4]
            if (fmt != 33 || dir != "OUTPUT") continue
            // Stop scanning once we hit the next named property (a non-bracket line
            // followed by a "(<hex>):" marker would land outside our row regex anyway,
            // so the regex naturally terminates).
            sizes.add(w to h)
        }
        for ((w, h) in sizes) {
            val o = JSONObject()
            o.put("width", w)
            o.put("height", h)
            o.put("megapixels", w.toLong() * h / 1_000_000.0)
            arr.put(o)
        }
        return arr
    }

    // Matches "android.info.supportedHardwareLevel (150000): byte[1]\n    [LIMITED ]"
    // and stores the value (LIMITED / FULL / BACK / ...).
    private fun addBracketByteValue(block: String, obj: JSONObject, key: String, name: String) {
        val esc = Regex.escape(name)
        val m = Regex("$esc\\s*\\(\\w+\\):\\s*byte\\[\\d+\\][^\\[]*\\[\\s*([A-Z_0-9]+)\\s*\\]").find(block) ?: return
        obj.put(key, m.groupValues[1])
    }

    private fun addFloatArrayJoined(block: String, obj: JSONObject, key: String, name: String) {
        val esc = Regex.escape(name)
        val m = Regex("$esc\\s*\\(\\w+\\):\\s*float\\[\\d+\\][^\\[]*\\[\\s*([\\d. ]+)\\s*\\]").find(block) ?: return
        val list = m.groupValues[1].trim().split(Regex("\\s+"))
        obj.put(key, list.joinToString(", "))
    }

    private fun buildNetwork(): JSONObject {
        // Device may have no active network at all (no WiFi/cell). Always enumerate all
        // interfaces via java.net.NetworkInterface — that works under shell uid and
        // doesn't depend on a default route. ConnectivityManager active-network info is
        // emitted as a top-level "active" sub-object when available.
        val o = JSONObject()
        try {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            val active: Network? = cm?.activeNetwork
            if (active != null) {
                val activeObj = JSONObject()
                val link: LinkProperties? = cm.getLinkProperties(active)
                val caps: NetworkCapabilities? = cm.getNetworkCapabilities(active)
                if (link != null) {
                    activeObj.put("interface", link.interfaceName ?: JSONObject.NULL)
                    val addrs = JSONArray()
                    for (la in link.linkAddresses) addrs.put(la.address.hostAddress)
                    activeObj.put("addresses", addrs)
                    val dns = JSONArray()
                    for (d in link.dnsServers) dns.put(d.hostAddress)
                    activeObj.put("dns", dns)
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                        activeObj.put("mtu", link.mtu)
                    }
                }
                if (caps != null) {
                    val tr = JSONArray()
                    if (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) tr.put("WIFI")
                    if (caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) tr.put("CELLULAR")
                    if (caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) tr.put("ETHERNET")
                    if (caps.hasTransport(NetworkCapabilities.TRANSPORT_BLUETOOTH)) tr.put("BLUETOOTH")
                    if (caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) tr.put("VPN")
                    activeObj.put("transports", tr)
                }
                o.put("activeInterface", activeObj.optString("interface", ""))
                if (activeObj.has("addresses")) o.put("activeAddresses", activeObj.getJSONArray("addresses"))
                if (activeObj.has("dns")) o.put("dns", activeObj.getJSONArray("dns"))
                if (activeObj.has("transports")) o.put("transports", activeObj.getJSONArray("transports"))
                if (activeObj.has("mtu")) o.put("activeMtu", activeObj.getInt("mtu"))
            } else {
                o.put("activeInterface", "")
            }
        } catch (_: Throwable) { /* fall through */ }
        // Always enumerate interfaces.
        try {
            val ifaces = JSONArray()
            val all = java.net.NetworkInterface.getNetworkInterfaces()
            while (all != null && all.hasMoreElements()) {
                val ni = all.nextElement()
                if (ni.isLoopback) continue
                val obj = JSONObject()
                obj.put("name", ni.name)
                obj.put("displayName", ni.displayName ?: ni.name)
                obj.put("up", ni.isUp)
                obj.put("mtu", ni.mtu)
                val mac = ni.hardwareAddress
                if (mac != null) {
                    obj.put("mac", mac.joinToString(":") { "%02x".format(it) })
                }
                val v4 = JSONArray()
                val v6 = JSONArray()
                for (ia in ni.interfaceAddresses) {
                    val host = ia.address.hostAddress ?: continue
                    if (host.contains(':')) v6.put("$host/${ia.networkPrefixLength}")
                    else v4.put("$host/${ia.networkPrefixLength}")
                }
                if (v4.length() > 0) obj.put("ipv4", v4)
                if (v6.length() > 0) obj.put("ipv6", v6)
                ifaces.put(obj)
            }
            o.put("interfaces", ifaces)
        } catch (e: Throwable) {
            o.put("error", e.message ?: e.javaClass.simpleName)
        }
        return o
    }

    private fun exec(vararg cmd: String): String {
        val proc = ProcessBuilder(*cmd).redirectErrorStream(true).start()
        val text = proc.inputStream.bufferedReader().use { it.readText() }
        proc.waitFor()
        return text
    }

    private fun buildSim(): JSONArray {
        val arr = JSONArray()
        val tm = context.getSystemService(Context.TELEPHONY_SERVICE) as? TelephonyManager
            ?: return arr
        val sm = context.getSystemService(Context.TELEPHONY_SUBSCRIPTION_SERVICE) as? SubscriptionManager
        val activeCount = try {
            sm?.activeSubscriptionInfoCount ?: 0
        } catch (_: Throwable) { 0 }
        if (activeCount == 0) {
            val obj = JSONObject()
            obj.put("slotIndex", 0)
            obj.put("simState", simStateName(tm.simState))
            obj.put("phoneType", phoneTypeName(tm.phoneType))
            obj.put("networkOperatorName", tm.networkOperatorName ?: "")
            obj.put("simOperatorName", tm.simOperatorName ?: "")
            obj.put("isoCountry", tm.networkCountryIso ?: "")
            arr.put(obj)
            return arr
        }
        val list: List<SubscriptionInfo>? = try { sm?.activeSubscriptionInfoList } catch (_: Throwable) { null }
        if (list != null) {
            for (info in list) {
                val obj = JSONObject()
                obj.put("slotIndex", info.simSlotIndex)
                obj.put("subId", info.subscriptionId)
                obj.put("displayName", info.displayName?.toString() ?: "")
                obj.put("carrierName", info.carrierName?.toString() ?: "")
                obj.put("countryIso", info.countryIso ?: "")
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    obj.put("isEmbedded", info.isEmbedded)
                }
                arr.put(obj)
            }
        }
        return arr
    }

    private fun buildTreble(): JSONObject {
        val o = JSONObject()
        o.put("trebleEnabled", systemProperty("ro.treble.enabled"))
        o.put("vndkVersion", systemProperty("ro.vndk.version"))
        o.put("vndkLite", systemProperty("ro.vndk.lite"))
        o.put("productFirstApiLevel", systemProperty("ro.product.first_api_level"))
        o.put("vendorApiLevel", systemProperty("ro.vendor.api_level"))
        o.put("boardApiLevel", systemProperty("ro.board.api_level"))
        o.put("boardFirstApiLevel", systemProperty("ro.board.first_api_level"))
        return o
    }

    private fun buildProperties(): JSONObject {
        val o = JSONObject()
        try {
            val proc = ProcessBuilder("getprop").redirectErrorStream(true).start()
            val text = proc.inputStream.bufferedReader().use { it.readText() }
            proc.waitFor()
            val regex = Regex("\\[(.+?)\\]:\\s*\\[(.*?)\\]")
            for (m in regex.findAll(text)) {
                o.put(m.groupValues[1], m.groupValues[2])
            }
        } catch (e: Throwable) {
            o.put("error", e.message ?: e.javaClass.simpleName)
        }
        return o
    }

    private fun systemProperty(key: String): String {
        return try {
            val cls = Class.forName("android.os.SystemProperties")
            val method = cls.getMethod("get", String::class.java)
            (method.invoke(null, key) as? String) ?: ""
        } catch (_: Throwable) { "" }
    }

    private fun readLong(path: String): Long {
        return try {
            File(path).readText().trim().toLongOrNull() ?: -1L
        } catch (_: Throwable) { -1L }
    }

    private fun readString(path: String): String {
        return try { File(path).readText().trim() } catch (_: Throwable) { "" }
    }

    private fun safe(block: () -> String?): Any {
        return try { block() ?: JSONObject.NULL } catch (_: Throwable) { JSONObject.NULL }
    }

    private fun simStateName(value: Int): String = when (value) {
        TelephonyManager.SIM_STATE_ABSENT -> "ABSENT"
        TelephonyManager.SIM_STATE_PIN_REQUIRED -> "PIN_REQUIRED"
        TelephonyManager.SIM_STATE_PUK_REQUIRED -> "PUK_REQUIRED"
        TelephonyManager.SIM_STATE_NETWORK_LOCKED -> "NETWORK_LOCKED"
        TelephonyManager.SIM_STATE_READY -> "READY"
        TelephonyManager.SIM_STATE_NOT_READY -> "NOT_READY"
        TelephonyManager.SIM_STATE_PERM_DISABLED -> "PERM_DISABLED"
        TelephonyManager.SIM_STATE_CARD_IO_ERROR -> "CARD_IO_ERROR"
        TelephonyManager.SIM_STATE_CARD_RESTRICTED -> "CARD_RESTRICTED"
        else -> "UNKNOWN"
    }

    private fun phoneTypeName(value: Int): String = when (value) {
        TelephonyManager.PHONE_TYPE_NONE -> "NONE"
        TelephonyManager.PHONE_TYPE_GSM -> "GSM"
        TelephonyManager.PHONE_TYPE_CDMA -> "CDMA"
        TelephonyManager.PHONE_TYPE_SIP -> "SIP"
        else -> "UNKNOWN"
    }
}
