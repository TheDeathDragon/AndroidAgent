package la.shiro.agent

import android.accessibilityservice.AccessibilityService
import android.annotation.SuppressLint
import android.annotation.TargetApi
import android.app.UiAutomation
import android.os.Build
import android.os.IBinder
import android.os.SystemClock
import android.view.InputDevice
import android.view.InputEvent
import android.view.KeyCharacterMap
import android.view.KeyEvent
import android.view.MotionEvent
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.lang.reflect.InvocationTargetException
import java.lang.reflect.Method
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.PI
import kotlin.math.cos

enum class GlobalAction(val wireName: String, val actionId: Int) {
    BACK("back", AccessibilityService.GLOBAL_ACTION_BACK),
    HOME("home", AccessibilityService.GLOBAL_ACTION_HOME),
    RECENTS("recents", AccessibilityService.GLOBAL_ACTION_RECENTS),
    NOTIFICATIONS("notifications", AccessibilityService.GLOBAL_ACTION_NOTIFICATIONS),
    QUICK_SETTINGS("quick_settings", AccessibilityService.GLOBAL_ACTION_QUICK_SETTINGS),
    POWER_DIALOG("power_dialog", AccessibilityService.GLOBAL_ACTION_POWER_DIALOG),
    LOCK_SCREEN("lock_screen", AccessibilityService.GLOBAL_ACTION_LOCK_SCREEN),
    SCREENSHOT("screenshot", AccessibilityService.GLOBAL_ACTION_TAKE_SCREENSHOT);

    companion object {
        private val BY_NAME: Map<String, GlobalAction> = values().associateBy { it.wireName }

        fun parseActionId(raw: String): Int {
            val trimmed = raw.trim()
            if (trimmed.isEmpty()) {
                throw BadRequestException("missing global action")
            }
            val named = BY_NAME[trimmed.lowercase(Locale.US)]
            if (named != null) return named.actionId
            val numeric = trimmed.toIntOrNull()
            if (numeric != null && numeric > 0) return numeric
            throw BadRequestException("unknown global action: $raw")
        }
    }
}

sealed class InputAction {
    data class Down(val x: Float, val y: Float) : InputAction()
    data class Move(val x: Float, val y: Float) : InputAction()
    data class Up(val x: Float? = null, val y: Float? = null) : InputAction()
    data class Tap(val x: Float, val y: Float, val durationMs: Long = 0L) : InputAction()
    data class Swipe(
        val x1: Float,
        val y1: Float,
        val x2: Float,
        val y2: Float,
        val durationMs: Long = 300L,
        val steps: Int? = null,
        val holdMs: Long = 0L
    ) : InputAction()
    data class Wait(val durationMs: Long) : InputAction()
    data class Key(
        val keyCodes: List<Int>,
        val metaState: Int = 0,
        val intervalMs: Long = 0L
    ) : InputAction()
    data class Global(val actionId: Int) : InputAction()
}

data class InputCommand(
    val action: InputAction,
    val sync: Boolean = true
)

data class InputBatch(
    val actions: List<InputAction>,
    val sync: Boolean = true
)

data class InputResult(
    val ok: Boolean,
    val source: String
)

// Isolated helper so R8 does not inline the API 34+ 3-arg overload into InputController on API 30-33.
@TargetApi(34)
internal object Api34InputInjector {
    fun inject(uiAutomation: UiAutomation, event: InputEvent, sync: Boolean): Boolean {
        return uiAutomation.injectInputEvent(event, sync, false)
    }
}

class InputController(private val uiAutomation: UiAutomation) : AutoCloseable {

    private class DirectInputInjector {
        @Volatile
        private var directAvailable = true
        private var initialized = false
        private var target: Any? = null
        private var injectMethod: Method? = null

        fun inject(event: InputEvent, sync: Boolean): Boolean? {
            if (!directAvailable) return null
            if (!ensureInitialized()) return null
            val receiver = target ?: return null
            val method = injectMethod ?: return null
            val mode = if (sync) {
                INJECT_INPUT_EVENT_MODE_WAIT_FOR_FINISH
            } else {
                INJECT_INPUT_EVENT_MODE_ASYNC
            }
            return try {
                (method.invoke(receiver, event, mode) as? Boolean) ?: false
            } catch (t: Throwable) {
                disablePermanently(t)
                null
            }
        }

        private fun ensureInitialized(): Boolean {
            if (initialized) return directAvailable
            synchronized(this) {
                if (initialized) return directAvailable
                initialized = true
                val resolved = resolveTargetAndMethod()
                if (resolved == null) {
                    disablePermanently(IllegalStateException("no direct InputManager injection target found"))
                    return false
                }
                target = resolved.first
                injectMethod = resolved.second
                return true
            }
        }

        @SuppressLint("PrivateApi", "DiscouragedPrivateApi")
        private fun resolveTargetAndMethod(): Pair<Any, Method>? {
            resolveStaticSingleton("android.hardware.input.InputManagerGlobal")?.let { return it }
            resolveStaticSingleton("android.hardware.input.InputManager")?.let { return it }
            return resolveBinderService()
        }

        @SuppressLint("PrivateApi", "DiscouragedPrivateApi")
        private fun resolveStaticSingleton(className: String): Pair<Any, Method>? {
            return try {
                val clazz = Class.forName(className)
                val instance = clazz.getMethod("getInstance").invoke(null) ?: return null
                val method = clazz.getMethod(
                    "injectInputEvent",
                    InputEvent::class.java,
                    Int::class.javaPrimitiveType
                )
                instance to method
            } catch (_: Throwable) {
                null
            }
        }

        @SuppressLint("PrivateApi", "DiscouragedPrivateApi")
        private fun resolveBinderService(): Pair<Any, Method>? {
            return try {
                val smClass = Class.forName("android.os.ServiceManager")
                val binder = smClass.getMethod("getService", String::class.java)
                    .invoke(null, "input") as? IBinder ?: return null
                val stubClass = Class.forName("android.hardware.input.IInputManager\$Stub")
                val service = stubClass.getMethod("asInterface", IBinder::class.java)
                    .invoke(null, binder) ?: return null
                val ifaceClass = Class.forName("android.hardware.input.IInputManager")
                val method = ifaceClass.getMethod(
                    "injectInputEvent",
                    InputEvent::class.java,
                    Int::class.javaPrimitiveType
                )
                service to method
            } catch (_: Throwable) {
                null
            }
        }

        private fun disablePermanently(error: Throwable) {
            if (!directAvailable) return
            directAvailable = false
            target = null
            injectMethod = null
            val cause = if (error is InvocationTargetException) error.cause ?: error else error
            System.err.println(
                "[Agent] Direct InputManager injection unavailable, falling back to UiAutomation: " +
                    "${cause.javaClass.simpleName}: ${cause.message}"
            )
        }

        companion object {
            private const val INJECT_INPUT_EVENT_MODE_ASYNC: Int = 0
            private const val INJECT_INPUT_EVENT_MODE_WAIT_FOR_FINISH: Int = 2
        }
    }

    private val inputLock = Any()
    private val cancelled = AtomicBoolean(false)
    private val useApi34Injector = AtomicBoolean(Build.VERSION.SDK_INT >= 34)
    private val directInjector = DirectInputInjector()
    private var batchUsedDirect = false
    private var batchUsedUiAutomation = false

    fun execute(command: InputCommand): InputResult {
        return executeBatch(InputBatch(listOf(command.action), command.sync))
    }

    fun executeBatch(batch: InputBatch): InputResult {
        synchronized(inputLock) {
            checkNotCancelled()
            var touchDown = false
            var touchDownTime = 0L
            var touchX = 0f
            var touchY = 0f
            var allOk = true
            batchUsedDirect = false
            batchUsedUiAutomation = false

            try {
                val actions = batch.actions
                val lastInjectIndex = actions.indexOfLast {
                    it !is InputAction.Wait && it !is InputAction.Global
                }
                for (index in actions.indices) {
                    checkNotCancelled()
                    val action = actions[index]
                    val finalSync = (index == lastInjectIndex) && batch.sync

                    when (action) {
                        is InputAction.Down -> {
                            if (touchDown) {
                                injectMotion(
                                    touchDownTime,
                                    SystemClock.uptimeMillis(),
                                    MotionEvent.ACTION_CANCEL,
                                    touchX,
                                    touchY,
                                    false
                                )
                                touchDown = false
                            }
                            touchDownTime = SystemClock.uptimeMillis()
                            touchX = action.x
                            touchY = action.y
                            if (!injectMotion(
                                    touchDownTime,
                                    touchDownTime,
                                    MotionEvent.ACTION_DOWN,
                                    touchX,
                                    touchY,
                                    finalSync
                                )
                            ) {
                                allOk = false
                            }
                            touchDown = true
                        }
                        is InputAction.Move -> {
                            if (!touchDown) {
                                throw BadRequestException("move requires an active down event")
                            }
                            touchX = action.x
                            touchY = action.y
                            if (!injectMotion(
                                    touchDownTime,
                                    SystemClock.uptimeMillis(),
                                    MotionEvent.ACTION_MOVE,
                                    touchX,
                                    touchY,
                                    finalSync
                                )
                            ) {
                                allOk = false
                            }
                        }
                        is InputAction.Up -> {
                            if (!touchDown) {
                                throw BadRequestException("up requires an active down event")
                            }
                            val upX = action.x ?: touchX
                            val upY = action.y ?: touchY
                            if (!injectMotion(
                                    touchDownTime,
                                    SystemClock.uptimeMillis(),
                                    MotionEvent.ACTION_UP,
                                    upX,
                                    upY,
                                    finalSync
                                )
                            ) {
                                allOk = false
                            }
                            touchDown = false
                        }
                        is InputAction.Tap -> {
                            if (touchDown) {
                                injectMotion(
                                    touchDownTime,
                                    SystemClock.uptimeMillis(),
                                    MotionEvent.ACTION_CANCEL,
                                    touchX,
                                    touchY,
                                    false
                                )
                                touchDown = false
                            }
                            if (!performTapLocked(action, finalSync)) {
                                allOk = false
                            }
                        }
                        is InputAction.Swipe -> {
                            if (touchDown) {
                                injectMotion(
                                    touchDownTime,
                                    SystemClock.uptimeMillis(),
                                    MotionEvent.ACTION_CANCEL,
                                    touchX,
                                    touchY,
                                    false
                                )
                                touchDown = false
                            }
                            if (!performSwipeLocked(action, finalSync)) {
                                allOk = false
                            }
                        }
                        is InputAction.Wait -> {
                            if (action.durationMs > 0L) {
                                sleepUntilUptime(SystemClock.uptimeMillis() + action.durationMs)
                            }
                        }
                        is InputAction.Key -> {
                            if (!performKeyLocked(action, finalSync)) {
                                allOk = false
                            }
                        }
                        is InputAction.Global -> {
                            batchUsedUiAutomation = true
                            if (!uiAutomation.performGlobalAction(action.actionId)) {
                                allOk = false
                            }
                        }
                    }
                }
                val source = if (batchUsedDirect && !batchUsedUiAutomation) {
                    SOURCE_DIRECT
                } else {
                    SOURCE_UIAUTOMATION
                }
                return InputResult(allOk, source)
            } finally {
                if (touchDown) {
                    try {
                        injectMotion(
                            touchDownTime,
                            SystemClock.uptimeMillis(),
                            MotionEvent.ACTION_CANCEL,
                            touchX,
                            touchY,
                            false
                        )
                    } catch (_: Throwable) {
                    }
                }
            }
        }
    }

    override fun close() {
        cancelled.set(true)
    }

    private fun performTapLocked(tap: InputAction.Tap, finalSync: Boolean): Boolean {
        val downTime = SystemClock.uptimeMillis()
        var ok = injectMotion(downTime, downTime, MotionEvent.ACTION_DOWN, tap.x, tap.y, false)
        var pointerDown = true
        try {
            if (tap.durationMs > 0L) {
                sleepUntilUptime(downTime + tap.durationMs)
            }
            val upTime = SystemClock.uptimeMillis()
            if (!injectMotion(downTime, upTime, MotionEvent.ACTION_UP, tap.x, tap.y, finalSync)) {
                ok = false
            }
            pointerDown = false
            return ok
        } finally {
            if (pointerDown) {
                try {
                    injectMotion(downTime, SystemClock.uptimeMillis(), MotionEvent.ACTION_CANCEL, tap.x, tap.y, false)
                } catch (_: Throwable) {
                }
            }
        }
    }

    private fun performSwipeLocked(swipe: InputAction.Swipe, finalSync: Boolean): Boolean {
        val totalSteps = swipe.steps ?: (swipe.durationMs / 8L).toInt().coerceIn(2, 120)
        val downTime = SystemClock.uptimeMillis()
        var ok = injectMotion(downTime, downTime, MotionEvent.ACTION_DOWN, swipe.x1, swipe.y1, false)
        var pointerDown = true
        var lastX = swipe.x1
        var lastY = swipe.y1
        try {
            for (step in 1..totalSteps) {
                val t = step.toFloat() / totalSteps.toFloat()
                val eased = 0.5f * (1f - cos(PI * t).toFloat())
                val cx = swipe.x1 + (swipe.x2 - swipe.x1) * eased
                val cy = swipe.y1 + (swipe.y2 - swipe.y1) * eased
                if (swipe.durationMs > 0L) {
                    val targetUptime = downTime + (swipe.durationMs * step) / totalSteps
                    sleepUntilUptime(targetUptime)
                }
                if (!injectMotion(downTime, SystemClock.uptimeMillis(), MotionEvent.ACTION_MOVE, cx, cy, false)) {
                    ok = false
                }
                lastX = cx
                lastY = cy
            }
            if (swipe.holdMs > 0L) {
                sleepUntilUptime(SystemClock.uptimeMillis() + swipe.holdMs)
            }
            if (!injectMotion(
                    downTime,
                    SystemClock.uptimeMillis(),
                    MotionEvent.ACTION_UP,
                    swipe.x2,
                    swipe.y2,
                    finalSync
                )
            ) {
                ok = false
            }
            pointerDown = false
            return ok
        } finally {
            if (pointerDown) {
                try {
                    injectMotion(downTime, SystemClock.uptimeMillis(), MotionEvent.ACTION_CANCEL, lastX, lastY, false)
                } catch (_: Throwable) {
                }
            }
        }
    }

    private fun performKeyLocked(key: InputAction.Key, finalSync: Boolean): Boolean {
        var ok = true
        val codes = key.keyCodes
        for (index in codes.indices) {
            checkNotCancelled()
            val code = codes[index]
            val isLast = index == codes.lastIndex
            val now = SystemClock.uptimeMillis()
            val downEvent = KeyEvent(
                now,
                now,
                KeyEvent.ACTION_DOWN,
                code,
                0,
                key.metaState,
                KeyCharacterMap.VIRTUAL_KEYBOARD,
                0,
                0,
                InputDevice.SOURCE_KEYBOARD
            )
            if (!inject(downEvent, false)) {
                ok = false
            }
            val upTime = SystemClock.uptimeMillis()
            val upEvent = KeyEvent(
                now,
                upTime,
                KeyEvent.ACTION_UP,
                code,
                0,
                key.metaState,
                KeyCharacterMap.VIRTUAL_KEYBOARD,
                0,
                0,
                InputDevice.SOURCE_KEYBOARD
            )
            if (!inject(upEvent, isLast && finalSync)) {
                ok = false
            }
            if (!isLast && key.intervalMs > 0L) {
                sleepUntilUptime(SystemClock.uptimeMillis() + key.intervalMs)
            }
        }
        return ok
    }

    private fun injectMotion(
        downTime: Long,
        eventTime: Long,
        action: Int,
        x: Float,
        y: Float,
        sync: Boolean
    ): Boolean {
        val event = MotionEvent.obtain(downTime, eventTime, action, x, y, 0)
        return try {
            event.source = InputDevice.SOURCE_TOUCHSCREEN
            inject(event, sync)
        } finally {
            event.recycle()
        }
    }

    private fun inject(event: InputEvent, sync: Boolean): Boolean {
        val directResult = directInjector.inject(event, sync)
        if (directResult != null) {
            batchUsedDirect = true
            return directResult
        }
        batchUsedUiAutomation = true
        if (useApi34Injector.get()) {
            try {
                return Api34InputInjector.inject(uiAutomation, event, sync)
            } catch (_: NoSuchMethodError) {
                useApi34Injector.set(false)
            }
        }
        return uiAutomation.injectInputEvent(event, sync)
    }

    private fun sleepUntilUptime(targetUptimeMs: Long) {
        while (true) {
            checkNotCancelled()
            val remaining = targetUptimeMs - SystemClock.uptimeMillis()
            if (remaining <= 0L) return
            try {
                Thread.sleep(minOf(remaining, SLEEP_SLICE_MS))
            } catch (e: InterruptedException) {
                Thread.currentThread().interrupt()
                throw IllegalStateException("input interrupted", e)
            }
        }
    }

    private fun checkNotCancelled() {
        if (cancelled.get()) {
            throw IllegalStateException("input controller closed")
        }
    }

    companion object {
        private const val SOURCE_DIRECT: String = "direct"
        private const val SOURCE_UIAUTOMATION: String = "uiautomation"
        private const val MAX_DURATION_MS: Long = 30_000L
        private const val MAX_HOLD_MS: Long = 10_000L
        private const val MIN_STEPS: Int = 1
        private const val MAX_STEPS: Int = 300
        private const val MAX_KEY_CODES: Int = 64
        private const val MAX_BATCH_ACTIONS: Int = 256
        private const val SLEEP_SLICE_MS: Long = 50L

        fun parseTap(request: HttpRequest): InputCommand {
            val json = parseOptionalJsonObject(request)
            val x = requireFloat(json, request.query, "x")
            val y = requireFloat(json, request.query, "y")
            val durationMs = optionalLong(json, request.query, "duration", "durationMs") ?: 0L
            validateRange(durationMs, 0L, MAX_DURATION_MS, "durationMs")
            val sync = optionalBoolean(json, request.query, "sync") ?: true
            return InputCommand(InputAction.Tap(x, y, durationMs), sync)
        }

        fun parseSwipe(request: HttpRequest): InputCommand {
            val json = parseOptionalJsonObject(request)
            val x1 = requireFloat(json, request.query, "x1")
            val y1 = requireFloat(json, request.query, "y1")
            val x2 = requireFloat(json, request.query, "x2")
            val y2 = requireFloat(json, request.query, "y2")
            val durationMs = optionalLong(json, request.query, "duration", "durationMs") ?: 300L
            validateRange(durationMs, 0L, MAX_DURATION_MS, "durationMs")
            val steps = optionalInt(json, request.query, "steps")
            if (steps != null && steps !in MIN_STEPS..MAX_STEPS) {
                throw BadRequestException("steps out of range $MIN_STEPS..$MAX_STEPS: $steps")
            }
            val holdMs = optionalLong(json, request.query, "hold", "holdMs") ?: 0L
            validateRange(holdMs, 0L, MAX_HOLD_MS, "holdMs")
            val sync = optionalBoolean(json, request.query, "sync") ?: true
            return InputCommand(InputAction.Swipe(x1, y1, x2, y2, durationMs, steps, holdMs), sync)
        }

        fun parseKey(request: HttpRequest): InputCommand {
            val json = parseOptionalJsonObject(request)
            val keyCodes = parseKeyCodes(json, request.query)
            val metaState = optionalInt(json, request.query, "meta", "metaState") ?: 0
            val intervalMs = optionalLong(json, request.query, "interval", "intervalMs") ?: 0L
            validateRange(intervalMs, 0L, MAX_DURATION_MS, "intervalMs")
            val sync = optionalBoolean(json, request.query, "sync") ?: true
            return InputCommand(InputAction.Key(keyCodes, metaState, intervalMs), sync)
        }

        fun parseGlobal(request: HttpRequest): InputCommand {
            val json = parseOptionalJsonObject(request)
            val actionRaw: String = extractGlobalActionRaw(json, request.query)
            val actionId = GlobalAction.parseActionId(actionRaw)
            return InputCommand(InputAction.Global(actionId), sync = true)
        }

        fun parseBatch(request: HttpRequest): InputBatch {
            val bodyText = request.bodyText().trim()
            if (bodyText.isEmpty()) {
                throw BadRequestException("batch body is required")
            }
            val actionsArray: JSONArray
            val sync: Boolean
            try {
                if (bodyText.startsWith("[")) {
                    actionsArray = JSONArray(bodyText)
                    sync = optionalBoolean(null, request.query, "sync") ?: true
                } else {
                    val root = JSONObject(bodyText)
                    actionsArray = root.optJSONArray("actions")
                        ?: throw BadRequestException("missing actions array")
                    sync = optionalBoolean(root, request.query, "sync") ?: true
                }
            } catch (e: BadRequestException) {
                throw e
            } catch (e: JSONException) {
                throw BadRequestException("invalid JSON body: ${e.message}")
            }

            if (actionsArray.length() !in 1..MAX_BATCH_ACTIONS) {
                throw BadRequestException("batch actions count must be in 1..$MAX_BATCH_ACTIONS")
            }

            val parsed = ArrayList<InputAction>(actionsArray.length())
            var touchDown = false
            for (i in 0 until actionsArray.length()) {
                val obj = actionsArray.optJSONObject(i)
                    ?: throw BadRequestException("batch action at index $i must be an object")
                val action = parseBatchAction(obj, i)
                when (action) {
                    is InputAction.Down -> touchDown = true
                    is InputAction.Move -> {
                        if (!touchDown) {
                            throw BadRequestException("move at index $i requires preceding down")
                        }
                    }
                    is InputAction.Up -> {
                        if (!touchDown) {
                            throw BadRequestException("up at index $i requires preceding down")
                        }
                        touchDown = false
                    }
                    is InputAction.Tap, is InputAction.Swipe -> touchDown = false
                    is InputAction.Wait, is InputAction.Key, is InputAction.Global -> Unit
                }
                parsed.add(action)
            }
            return InputBatch(parsed, sync)
        }

        private fun parseBatchAction(obj: JSONObject, index: Int): InputAction {
            val rawType = obj.optString("type", "").trim().lowercase(Locale.US)
            if (rawType.isEmpty()) {
                throw BadRequestException("missing type at action index $index")
            }
            return when (rawType) {
                "down" -> InputAction.Down(
                    x = requireFloat(obj, emptyMap(), "x"),
                    y = requireFloat(obj, emptyMap(), "y")
                )
                "move" -> InputAction.Move(
                    x = requireFloat(obj, emptyMap(), "x"),
                    y = requireFloat(obj, emptyMap(), "y")
                )
                "up" -> InputAction.Up(
                    x = optionalFloat(obj, emptyMap(), "x"),
                    y = optionalFloat(obj, emptyMap(), "y")
                )
                "tap" -> {
                    val durationMs = optionalLong(obj, emptyMap(), "duration", "durationMs") ?: 0L
                    validateRange(durationMs, 0L, MAX_DURATION_MS, "durationMs")
                    InputAction.Tap(
                        x = requireFloat(obj, emptyMap(), "x"),
                        y = requireFloat(obj, emptyMap(), "y"),
                        durationMs = durationMs
                    )
                }
                "swipe" -> {
                    val durationMs = optionalLong(obj, emptyMap(), "duration", "durationMs") ?: 300L
                    validateRange(durationMs, 0L, MAX_DURATION_MS, "durationMs")
                    val steps = optionalInt(obj, emptyMap(), "steps")
                    if (steps != null && steps !in MIN_STEPS..MAX_STEPS) {
                        throw BadRequestException("steps out of range $MIN_STEPS..$MAX_STEPS: $steps")
                    }
                    val holdMs = optionalLong(obj, emptyMap(), "hold", "holdMs") ?: 0L
                    validateRange(holdMs, 0L, MAX_HOLD_MS, "holdMs")
                    InputAction.Swipe(
                        x1 = requireFloat(obj, emptyMap(), "x1"),
                        y1 = requireFloat(obj, emptyMap(), "y1"),
                        x2 = requireFloat(obj, emptyMap(), "x2"),
                        y2 = requireFloat(obj, emptyMap(), "y2"),
                        durationMs = durationMs,
                        steps = steps,
                        holdMs = holdMs
                    )
                }
                "wait" -> {
                    val durationMs = optionalLong(obj, emptyMap(), "duration", "durationMs", "ms")
                        ?: throw BadRequestException("missing durationMs for wait at index $index")
                    validateRange(durationMs, 0L, MAX_DURATION_MS, "durationMs")
                    InputAction.Wait(durationMs)
                }
                "key" -> {
                    val codes = parseKeyCodes(obj, emptyMap())
                    val metaState = optionalInt(obj, emptyMap(), "meta", "metaState") ?: 0
                    val intervalMs = optionalLong(obj, emptyMap(), "interval", "intervalMs") ?: 0L
                    validateRange(intervalMs, 0L, MAX_DURATION_MS, "intervalMs")
                    InputAction.Key(codes, metaState, intervalMs)
                }
                "global" -> {
                    val raw = extractGlobalActionRaw(obj, emptyMap())
                    InputAction.Global(GlobalAction.parseActionId(raw))
                }
                else -> throw BadRequestException("unknown batch action type at index $index: $rawType")
            }
        }

        private fun parseOptionalJsonObject(request: HttpRequest): JSONObject? {
            if (request.body.isEmpty()) return null
            val text = request.bodyText().trim()
            if (text.isEmpty()) return null
            return try {
                JSONObject(text)
            } catch (e: JSONException) {
                throw BadRequestException("invalid JSON body: ${e.message}")
            }
        }

        private fun requireFloat(json: JSONObject?, query: Map<String, String>, key: String): Float {
            return optionalFloat(json, query, key)
                ?: throw BadRequestException("missing parameter: $key")
        }

        private fun optionalFloat(json: JSONObject?, query: Map<String, String>, key: String): Float? {
            if (json != null && json.has(key) && !json.isNull(key)) {
                val raw = json.get(key)
                val value: Float = when (raw) {
                    is Number -> raw.toFloat()
                    is String -> raw.toFloatOrNull()
                        ?: throw BadRequestException("invalid number for $key: $raw")
                    else -> throw BadRequestException("invalid number for $key")
                }
                if (!value.isFinite()) {
                    throw BadRequestException("non-finite coordinate for $key")
                }
                return value
            }
            val queryVal = query[key] ?: return null
            val parsed = queryVal.toFloatOrNull()
                ?: throw BadRequestException("invalid number for $key: $queryVal")
            if (!parsed.isFinite()) {
                throw BadRequestException("non-finite coordinate for $key")
            }
            return parsed
        }

        private fun optionalLong(
            json: JSONObject?,
            query: Map<String, String>,
            vararg keys: String
        ): Long? {
            for (key in keys) {
                if (json != null && json.has(key) && !json.isNull(key)) {
                    return when (val raw = json.get(key)) {
                        is Int -> raw.toLong()
                        is Long -> raw
                        is Number -> {
                            val d = raw.toDouble()
                            if (!d.isFinite() || d % 1.0 != 0.0) {
                                throw BadRequestException("invalid integer for $key: $raw")
                            }
                            d.toLong()
                        }
                        is String -> raw.toLongOrNull()
                            ?: throw BadRequestException("invalid integer for $key: $raw")
                        else -> throw BadRequestException("invalid integer for $key")
                    }
                }
            }
            for (key in keys) {
                val queryVal = query[key]
                if (queryVal != null) {
                    return queryVal.toLongOrNull()
                        ?: throw BadRequestException("invalid integer for $key: $queryVal")
                }
            }
            return null
        }

        private fun optionalInt(
            json: JSONObject?,
            query: Map<String, String>,
            vararg keys: String
        ): Int? {
            val value = optionalLong(json, query, *keys) ?: return null
            if (value < Int.MIN_VALUE || value > Int.MAX_VALUE) {
                throw BadRequestException("integer overflow for ${keys.first()}: $value")
            }
            return value.toInt()
        }

        private fun optionalBoolean(
            json: JSONObject?,
            query: Map<String, String>,
            key: String
        ): Boolean? {
            if (json != null && json.has(key) && !json.isNull(key)) {
                return when (val raw = json.get(key)) {
                    is Boolean -> raw
                    is String -> raw.trim().lowercase(Locale.US).toBooleanStrictOrNull()
                        ?: throw BadRequestException("invalid boolean for $key: $raw")
                    else -> throw BadRequestException("invalid boolean for $key")
                }
            }
            val queryVal = query[key] ?: return null
            return queryVal.trim().lowercase(Locale.US).toBooleanStrictOrNull()
                ?: throw BadRequestException("invalid boolean for $key: $queryVal")
        }

        private fun parseKeyCodes(json: JSONObject?, query: Map<String, String>): List<Int> {
            val result = ArrayList<Int>()
            val arrayKeys = arrayOf("keyCodes", "keycodes", "codes")
            val singleKeys = arrayOf("keyCode", "keycode", "code")

            if (json != null) {
                for (key in arrayKeys) {
                    if (json.has(key) && !json.isNull(key)) {
                        val raw = json.get(key)
                        if (raw is JSONArray) {
                            for (i in 0 until raw.length()) {
                                result.add(parseSingleKeyCodeValue(raw.get(i)))
                            }
                            return validateKeyCodes(result)
                        } else if (raw is String) {
                            parseCommaKeyCodes(raw, result)
                            return validateKeyCodes(result)
                        } else {
                            throw BadRequestException("invalid keyCodes value")
                        }
                    }
                }
                for (key in singleKeys) {
                    if (json.has(key) && !json.isNull(key)) {
                        result.add(parseSingleKeyCodeValue(json.get(key)))
                        return validateKeyCodes(result)
                    }
                }
            }

            for (key in arrayKeys) {
                val queryVal = query[key]
                if (queryVal != null) {
                    parseCommaKeyCodes(queryVal, result)
                    return validateKeyCodes(result)
                }
            }
            for (key in singleKeys) {
                val queryVal = query[key]
                if (queryVal != null) {
                    result.add(parseSingleKeyCodeString(queryVal))
                    return validateKeyCodes(result)
                }
            }

            throw BadRequestException("missing keyCode or keyCodes parameter")
        }

        private fun parseCommaKeyCodes(raw: String, out: MutableList<Int>) {
            for (part in raw.split(',')) {
                val trimmed = part.trim()
                if (trimmed.isNotEmpty()) {
                    out.add(parseSingleKeyCodeString(trimmed))
                }
            }
        }

        private fun parseSingleKeyCodeValue(raw: Any?): Int = when (raw) {
            is Int -> validateKeyCode(raw)
            is Long -> {
                if (raw < Int.MIN_VALUE || raw > Int.MAX_VALUE) {
                    throw BadRequestException("invalid keycode: $raw")
                }
                validateKeyCode(raw.toInt())
            }
            is String -> parseSingleKeyCodeString(raw)
            else -> throw BadRequestException("invalid keycode: $raw")
        }

        private fun parseSingleKeyCodeString(raw: String): Int {
            val trimmed = raw.trim()
            val asInt = trimmed.toIntOrNull()
            if (asInt != null) return validateKeyCode(asInt)
            val upper = trimmed.uppercase(Locale.US)
            val direct = KeyEvent.keyCodeFromString(upper)
            if (direct != KeyEvent.KEYCODE_UNKNOWN) return direct
            val prefixed = KeyEvent.keyCodeFromString("KEYCODE_$upper")
            if (prefixed != KeyEvent.KEYCODE_UNKNOWN) return prefixed
            throw BadRequestException("unknown keycode: $raw")
        }

        private fun validateKeyCode(code: Int): Int {
            if (code <= 0) {
                throw BadRequestException("invalid keycode: $code")
            }
            return code
        }

        private fun validateKeyCodes(codes: List<Int>): List<Int> {
            if (codes.size !in 1..MAX_KEY_CODES) {
                throw BadRequestException("keyCodes count must be in 1..$MAX_KEY_CODES")
            }
            return codes
        }

        private fun extractGlobalActionRaw(json: JSONObject?, query: Map<String, String>): String {
            if (json != null && json.has("action") && !json.isNull("action")) {
                return json.get("action").toString()
            }
            return query["action"] ?: throw BadRequestException("missing action parameter")
        }

        private fun validateRange(value: Long, min: Long, max: Long, name: String) {
            if (value !in min..max) {
                throw BadRequestException("$name out of range $min..$max: $value")
            }
        }
    }
}
