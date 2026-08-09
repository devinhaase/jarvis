package com.dhaaselab.jarvis

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

/**
 * Tier 10 — receives FCM messages and turns them into real Android notifications, plus
 * keeps the server's copy of this device's token current. Mirrors sw.js's own `push` +
 * `notificationclick` handlers on the desktop/web side — same four categories, same
 * data-only payload shape (see push_notifications.py's docstring on why data-only, not a
 * "notification" block), same deep-link-to-conversation idea — just the Android-native
 * equivalent of that same job.
 */
class JarvisFirebaseMessagingService : FirebaseMessagingService() {

    override fun onNewToken(token: String) {
        super.onNewToken(token)
        // Just persisted here — MainActivity is what actually injects it into the WebView
        // (via window.registerFcmToken()) the next time a connection succeeds, since this
        // service has no WebView/JS context of its own to call into directly, and may well
        // fire while the app isn't even running.
        FcmTokenStore.saveToken(applicationContext, token)
    }

    override fun onMessageReceived(message: RemoteMessage) {
        super.onMessageReceived(message)
        val data = message.data
        val title = data["title"] ?: getString(R.string.app_name)
        val body = data["body"] ?: ""
        val category = data["category"] ?: "messages"
        val conversationId = data["conversation_id"]?.takeIf { it != "null" }
        // "dashboard" (team/approval_id deep link) is a known, deliberate scope boundary
        // here — see android_app/README.md: this app only ever loads the chat page (Tier
        // 7), never the standalone /dashboard page the desktop/web clients have, so a
        // dashboard-targeted notification still opens straight into chat rather than
        // pretending to deep-link somewhere this app doesn't have a view for.

        ensureChannel(category)

        val tapIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            if (conversationId != null) putExtra(MainActivity.EXTRA_DEEP_LINK_CONV, conversationId)
        }
        val pendingIntent = PendingIntent.getActivity(
            this, System.currentTimeMillis().toInt(), tapIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val notification = NotificationCompat.Builder(this, channelId(category))
            .setContentTitle(title)
            .setContentText(body)
            .setSmallIcon(android.R.drawable.ic_dialog_info)  // placeholder — see android_app/README.md's Tier 11 note on a proper monochrome notification icon
            .setAutoCancel(true)
            .setContentIntent(pendingIntent)
            .setPriority(if (category == "approvals") NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_DEFAULT)
            .build()

        val tag = data["tag"] ?: category
        val notificationManager = getSystemService(NotificationManager::class.java)
        notificationManager.notify(tag, 0, notification)
    }

    private fun channelId(category: String) = "jarvis_$category"

    /** One channel per push category (approvals/alerts/briefing/messages), matching
     * push_notifications.py's own CATEGORIES exactly — lets Devin control each one
     * independently from Android's own system notification settings, the native
     * equivalent of the web settings modal's per-category toggles, not a separate concept. */
    private fun ensureChannel(category: String) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val label = when (category) {
            "approvals" -> "Approvals"
            "alerts" -> "Security alerts"
            "briefing" -> "Daily briefing"
            else -> "Messages"
        }
        val importance = if (category == "approvals") NotificationManager.IMPORTANCE_HIGH else NotificationManager.IMPORTANCE_DEFAULT
        val channel = NotificationChannel(channelId(category), label, importance)
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }
}
