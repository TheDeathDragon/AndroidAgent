package la.shiro.agent

import android.annotation.SuppressLint
import android.app.ActivityThread
import android.app.UiAutomation
import android.app.UiAutomationConnection
import android.os.HandlerThread
import android.os.Looper
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.system.exitProcess

object Server {

    private const val DEFAULT_PORT: Int = 9500
    private const val EXIT_CONNECT_FAILED: Int = 2
    private const val CONNECT_BUDGET_MS: Long = 30_000L
    private const val CONNECT_RETRY_INTERVAL_MS: Long = 500L
    private const val SERVICE_POLL_INTERVAL_MS: Long = 200L

    private class UiAutomationSession(
        val uiAutomation: UiAutomation,
        private val handlerThread: HandlerThread
    ) : AutoCloseable {
        private val closed = AtomicBoolean(false)

        override fun close() {
            if (!closed.compareAndSet(false, true)) return
            safeDisconnect(uiAutomation)
            try {
                handlerThread.quitSafely()
            } catch (_: Throwable) {
            }
        }
    }

    @JvmStatic
    fun main(args: Array<String>) {
        val port: Int = parsePort(args)
        println("[Agent] Starting agent server v${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})")
        println("[Agent] Port: $port")
        val context = initializeAndroidRuntime()
        val session: UiAutomationSession = try {
            connectUiAutomation()
        } catch (e: Throwable) {
            println("[Agent] UiAutomation unavailable: ${e.message ?: e.javaClass.simpleName}")
            println("[Agent] Exiting cleanly without starting HTTP server")
            exitProcess(EXIT_CONNECT_FAILED)
        }
        println("[Agent] UiAutomation connected")
        val server = SimpleHttpServer(port, session.uiAutomation, context)
        val shutdownDone = AtomicBoolean(false)
        fun shutdown() {
            if (!shutdownDone.compareAndSet(false, true)) return
            println("[Agent] Shutting down...")
            try { server.stop() } catch (_: Throwable) {}
            session.close()
        }
        Runtime.getRuntime().addShutdownHook(Thread(::shutdown, "ShutdownHook"))
        try {
            server.start()
            println("[Agent] HTTP server listening on 127.0.0.1:$port")
            server.awaitStop()
        } finally {
            shutdown()
        }
    }

    private fun initializeAndroidRuntime(): android.content.Context {
        Looper.prepareMainLooper()
        val activityThread: ActivityThread = ActivityThread.systemMain()
        val context: android.content.Context = activityThread.systemContext
        println("[Agent] Android runtime initialized")
        return context
    }

    private fun connectUiAutomation(): UiAutomationSession {
        awaitAccessibilityService(CONNECT_BUDGET_MS)
        val handlerThread = HandlerThread("UiAutomationThread")
        handlerThread.start()
        val deadline: Long = System.currentTimeMillis() + CONNECT_BUDGET_MS
        var attempt = 0
        while (true) {
            attempt++
            val connection = UiAutomationConnection()
            val uiAutomation = UiAutomation(handlerThread.looper, connection)
            try {
                uiAutomation.connect(UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES)
                if (attempt > 1) {
                    println("[Agent] UiAutomation connected after $attempt attempts")
                }
                return UiAutomationSession(uiAutomation, handlerThread)
            } catch (e: Throwable) {
                // First connect() may have registered with AMS even when the local wait timed out;
                // without disconnect(), retries fail with "UiAutomationService ... already registered!".
                safeDisconnect(uiAutomation)
                if (System.currentTimeMillis() >= deadline) {
                    handlerThread.quitSafely()
                    throw IllegalStateException(
                        "UiAutomation.connect failed after $attempt attempts within ${CONNECT_BUDGET_MS}ms: " +
                            "${e.javaClass.simpleName}: ${e.message}",
                        e
                    )
                }
                println(
                    "[Agent] UiAutomation.connect attempt $attempt failed: " +
                        "${e.javaClass.simpleName}: ${e.message}; retrying in ${CONNECT_RETRY_INTERVAL_MS}ms"
                )
                Thread.sleep(CONNECT_RETRY_INTERVAL_MS)
            }
        }
    }

    private fun safeDisconnect(uiAutomation: UiAutomation) {
        try {
            uiAutomation.disconnect()
        } catch (_: Throwable) {
        }
    }

    private fun awaitAccessibilityService(timeoutMs: Long) {
        val serviceManagerClass: Class<*> = try {
            Class.forName("android.os.ServiceManager")
        } catch (e: Throwable) {
            println("[Agent] ServiceManager unavailable for readiness probe: ${e.message}")
            return
        }
        val getService = try {
            serviceManagerClass.getMethod("getService", String::class.java)
        } catch (e: Throwable) {
            println("[Agent] ServiceManager.getService unavailable: ${e.message}")
            return
        }
        val deadline: Long = System.currentTimeMillis() + timeoutMs
        var waits = 0
        while (System.currentTimeMillis() < deadline) {
            try {
                val binder = getService.invoke(null, android.content.Context.ACCESSIBILITY_SERVICE)
                if (binder != null) {
                    if (waits > 0) {
                        val waitedMs: Long = waits * SERVICE_POLL_INTERVAL_MS
                        println("[Agent] Accessibility service registered after ${waitedMs}ms")
                    }
                    return
                }
            } catch (_: Throwable) {
            }
            waits++
            Thread.sleep(SERVICE_POLL_INTERVAL_MS)
        }
        println("[Agent] Accessibility service still missing after ${timeoutMs}ms; falling through to connect")
    }

    private fun parsePort(args: Array<String>): Int {
        val index: Int = args.indexOf("--port")
        if (index >= 0 && index + 1 < args.size) {
            return args[index + 1].toIntOrNull() ?: DEFAULT_PORT
        }
        return DEFAULT_PORT
    }
}
