@file:SuppressLint("PrivateApi", "DiscouragedPrivateApi")

package la.shiro.agent

import android.annotation.SuppressLint
import android.content.res.Configuration
import android.content.res.Resources
import android.os.LocaleList
import org.json.JSONArray
import org.json.JSONObject
import java.lang.reflect.InvocationTargetException
import java.lang.reflect.Method

// System-language control. Reads the framework-supported translation locales from the
// system AssetManager and switches the persistent system locale through IActivityManager
// (same path SettingsProvider uses), so the change survives app restarts and reboots.
class LocaleService {

    companion object {
        // Attribution package must match the binder calling uid (shell, 2000) for the
        // WRITE_SETTINGS appop check inside updatePersistentConfigurationWithAttribution.
        private const val SHELL_PACKAGE: String = "com.android.shell"
    }

    fun buildLocalesJson(): String {
        val tags: List<String> = Resources.getSystem().assets.locales
            .filter { !it.isNullOrEmpty() }
            .sorted()
        val root = JSONObject()
        root.put("current", currentLocaleTag())
        root.put("locales", JSONArray(tags))
        return root.toString()
    }

    // Accepts a comma-joined tag list so restoring a captured "previous" value keeps
    // secondary locales; a single tag behaves exactly as before.
    fun setLocale(tag: String): String {
        val normalized: String = tag.split(",").joinToString(",") { it.trim() }
        val list: LocaleList = LocaleList.forLanguageTags(normalized)
        if (tag.isBlank() || list.isEmpty || (0 until list.size()).any { list[it].language.isEmpty() }) {
            throw IllegalArgumentException("unparsable locale tag: $tag")
        }
        val previous: String = currentLocaleTag()
        val am: Any = activityManagerService()
        val config: Configuration = getConfiguration(am)
        config.setLocales(list)
        markUserSetLocale(config)
        updatePersistentConfiguration(am, config)
        // updatePersistentConfiguration applies the config live, but its own
        // persistence write can be silently rejected by the WRITE_SETTINGS
        // appop for the shell uid - the next config recomputation then
        // reverts the locale to the persisted value. Persist explicitly via
        // the settings shell command, which uses the shell-privileged path.
        persistSystemLocales(list.toLanguageTags())
        val root = JSONObject()
        root.put("ok", true)
        root.put("locale", list.toLanguageTags())
        root.put("previous", previous)
        return root.toString()
    }

    fun errorJson(message: String): String {
        return JSONObject().put("ok", false).put("error", message).toString()
    }

    private fun currentLocaleTag(): String {
        return try {
            val am: Any = activityManagerService()
            val config: Configuration = getConfiguration(am)
            config.locales.toLanguageTags()
        } catch (e: Throwable) {
            LocaleList.getDefault().toLanguageTags()
        }
    }

    private fun activityManagerService(): Any {
        try {
            val getService: Method = Class.forName("android.app.ActivityManager").getMethod("getService")
            val am: Any? = getService.invoke(null)
            if (am != null) return am
        } catch (e: Throwable) {
            System.err.println("[Agent] ActivityManager.getService failed: ${e.message}")
        }
        val getDefault: Method = Class.forName("android.app.ActivityManagerNative").getMethod("getDefault")
        return getDefault.invoke(null) ?: throw IllegalStateException("IActivityManager unavailable")
    }

    // Look methods up on the IActivityManager interface, not am.javaClass — the binder
    // proxy class is package-private and invoking its methods throws IllegalAccessException.
    private fun getConfiguration(am: Any): Configuration {
        val method: Method = Class.forName("android.app.IActivityManager").getMethod("getConfiguration")
        return invokeUnwrapped(method, am) as Configuration
    }

    private fun persistSystemLocales(tags: String) {
        val proc: Process = Runtime.getRuntime()
            .exec(arrayOf("settings", "put", "system", "system_locales", tags))
        val exit: Int = proc.waitFor()
        val err: String = proc.errorStream.bufferedReader().readText().trim()
        val out: String = proc.inputStream.bufferedReader().readText().trim()
        if (exit != 0 || err.isNotEmpty()) {
            val detail: String = if (err.isNotEmpty()) err else out
            throw IllegalStateException("settings put system_locales failed: exit=$exit $detail")
        }
        try {
            Runtime.getRuntime()
                .exec(arrayOf("setprop", "persist.sys.locale", tags.substringBefore(",")))
                .waitFor()
        } catch (e: Throwable) {
            System.err.println("[Agent] setprop persist.sys.locale failed: ${e.message}")
        }
    }

    private fun markUserSetLocale(config: Configuration) {
        try {
            Configuration::class.java.getField("userSetLocale").setBoolean(config, true)
        } catch (e: Throwable) {
            System.err.println("[Agent] userSetLocale field not settable: ${e.message}")
        }
    }

    private fun updatePersistentConfiguration(am: Any, config: Configuration) {
        val iam: Class<*> = Class.forName("android.app.IActivityManager")
        val plain: Method? = try {
            iam.getMethod("updatePersistentConfiguration", Configuration::class.java)
        } catch (e: Throwable) {
            null
        }
        if (plain != null) {
            invokeUnwrapped(plain, am, config)
            return
        }
        val attributed: Method = try {
            iam.getMethod(
                "updatePersistentConfigurationWithAttribution",
                Configuration::class.java,
                String::class.java,
                String::class.java
            )
        } catch (e: Throwable) {
            throw IllegalStateException("no updatePersistentConfiguration variant on IActivityManager")
        }
        invokeUnwrapped(attributed, am, config, SHELL_PACKAGE, null)
    }

    private fun invokeUnwrapped(method: Method, target: Any, vararg args: Any?): Any? {
        return try {
            method.invoke(target, *args)
        } catch (e: Throwable) {
            val cause: Throwable? = (e as? InvocationTargetException)?.targetException
            throw cause ?: e
        }
    }
}
