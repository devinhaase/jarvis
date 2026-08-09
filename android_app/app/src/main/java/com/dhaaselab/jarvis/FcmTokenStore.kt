package com.dhaaselab.jarvis

import android.content.Context

/**
 * Holds the most recent FCM token this device has, so MainActivity can inject it into the
 * WebView (via window.registerFcmToken()) any time a connection succeeds — not just the
 * instant onNewToken() fires, which may happen while no WebView/JS context exists yet to
 * receive it (app not running, or still mid-connect). A separate tiny store rather than
 * folding into ConnectionPrefs — this is "what this device's own push identity currently
 * is," a different concern from "where the main computer is."
 */
object FcmTokenStore {
    private const val PREFS_NAME = "jarvis_fcm"
    private const val KEY_TOKEN = "fcm_token"
    private const val KEY_REGISTERED = "fcm_token_registered_with_server"

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun saveToken(context: Context, token: String) {
        // A genuinely new/rotated token needs re-registering with the server even if the
        // last one already succeeded — clear the "registered" flag so MainActivity's
        // injection logic doesn't skip it as already-done.
        prefs(context).edit()
            .putString(KEY_TOKEN, token)
            .putBoolean(KEY_REGISTERED, false)
            .apply()
    }

    fun getToken(context: Context): String? = prefs(context).getString(KEY_TOKEN, null)

    fun isRegistered(context: Context): Boolean = prefs(context).getBoolean(KEY_REGISTERED, false)

    fun markRegistered(context: Context) {
        prefs(context).edit().putBoolean(KEY_REGISTERED, true).apply()
    }
}
