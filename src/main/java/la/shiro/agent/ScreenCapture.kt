package la.shiro.agent

import android.app.UiAutomation
import android.graphics.Bitmap
import java.io.ByteArrayOutputStream

class ScreenCapture(private val uiAutomation: UiAutomation) {

    fun capture(quality: Int = 100): ByteArray? {
        val bitmap: Bitmap = uiAutomation.takeScreenshot() ?: return null
        return try {
            val stream = ByteArrayOutputStream()
            bitmap.compress(Bitmap.CompressFormat.PNG, quality, stream)
            stream.toByteArray()
        } finally {
            bitmap.recycle()
        }
    }
}
