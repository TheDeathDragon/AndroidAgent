@file:Suppress("DEPRECATION")
@file:SuppressLint("PrivateApi", "DiscouragedPrivateApi")

package la.shiro.agent

import android.annotation.SuppressLint
import android.app.UiAutomation
import android.graphics.Point
import android.graphics.Rect
import android.hardware.display.DisplayManagerGlobal
import android.view.accessibility.AccessibilityNodeInfo
import android.util.Xml
import org.xmlpull.v1.XmlSerializer
import java.io.StringWriter

class UiHierarchy(private val uiAutomation: UiAutomation) {

    fun dump(compressed: Boolean): String {
        configureCompression(compressed)
        val display = DisplayManagerGlobal.getInstance().getRealDisplay(0)
        val rotation: Int = display.rotation
        val size = Point()
        display.getRealSize(size)
        val root: AccessibilityNodeInfo = uiAutomation.rootInActiveWindow
            ?: return "<?xml version=\"1.0\" encoding=\"UTF-8\"?><hierarchy rotation=\"$rotation\"/>"
        return try {
            buildXml(root, rotation, size.x, size.y)
        } finally {
            root.recycle()
        }
    }

    private fun configureCompression(compressed: Boolean) {
        val serviceInfo = uiAutomation.serviceInfo ?: return
        if (compressed) {
            serviceInfo.flags = serviceInfo.flags and FLAG_INCLUDE_NOT_IMPORTANT_VIEWS.inv()
        } else {
            serviceInfo.flags = serviceInfo.flags or FLAG_INCLUDE_NOT_IMPORTANT_VIEWS
        }
        uiAutomation.serviceInfo = serviceInfo
    }

    private fun buildXml(root: AccessibilityNodeInfo, rotation: Int, width: Int, height: Int): String {
        val writer = StringWriter()
        val serializer: XmlSerializer = Xml.newSerializer()
        serializer.setOutput(writer)
        serializer.startDocument("UTF-8", true)
        serializer.startTag(NS, "hierarchy")
        serializer.attribute(NS, "rotation", rotation.toString())
        dumpNode(root, serializer, 0, width, height)
        serializer.endTag(NS, "hierarchy")
        serializer.endDocument()
        return writer.toString()
    }

    private fun dumpNode(node: AccessibilityNodeInfo, serializer: XmlSerializer, index: Int, width: Int, height: Int) {
        serializer.startTag(NS, "node")
        serializer.attribute(NS, "index", index.toString())
        serializer.attribute(NS, "text", sanitize(node.text))
        serializer.attribute(NS, "resource-id", sanitize(node.viewIdResourceName))
        serializer.attribute(NS, "class", sanitize(node.className))
        serializer.attribute(NS, "package", sanitize(node.packageName))
        serializer.attribute(NS, "content-desc", sanitize(node.contentDescription))
        serializer.attribute(NS, "checkable", node.isCheckable.toString())
        serializer.attribute(NS, "checked", node.isChecked.toString())
        serializer.attribute(NS, "clickable", node.isClickable.toString())
        serializer.attribute(NS, "enabled", node.isEnabled.toString())
        serializer.attribute(NS, "focusable", node.isFocusable.toString())
        serializer.attribute(NS, "focused", node.isFocused.toString())
        serializer.attribute(NS, "scrollable", node.isScrollable.toString())
        serializer.attribute(NS, "long-clickable", node.isLongClickable.toString())
        serializer.attribute(NS, "password", node.isPassword.toString())
        serializer.attribute(NS, "selected", node.isSelected.toString())
        val bounds = Rect()
        node.getBoundsInScreen(bounds)
        bounds.intersect(0, 0, width, height)
        serializer.attribute(NS, "bounds", "[${bounds.left},${bounds.top}][${bounds.right},${bounds.bottom}]")
        val childCount: Int = node.childCount
        for (i in 0 until childCount) {
            val child: AccessibilityNodeInfo = node.getChild(i) ?: continue
            try {
                if (child.isVisibleToUser) {
                    dumpNode(child, serializer, i, width, height)
                }
            } finally {
                child.recycle()
            }
        }
        serializer.endTag(NS, "node")
    }

    private fun sanitize(cs: CharSequence?): String {
        if (cs == null) return ""
        val sb = StringBuilder(cs.length)
        for (ch in cs) {
            if (isValidXmlChar(ch)) {
                sb.append(ch)
            } else {
                sb.append('.')
            }
        }
        return sb.toString()
    }

    private fun isValidXmlChar(ch: Char): Boolean {
        val code: Int = ch.code
        return code == 0x9 || code == 0xA || code == 0xD ||
                code in 0x20..0xD7FF ||
                code in 0xE000..0xFFFD
    }

    companion object {
        private const val NS: String = ""
        private const val FLAG_INCLUDE_NOT_IMPORTANT_VIEWS: Int = 2
    }
}
