package com.dhaaselab.jarvis

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.URL

/**
 * Resolves which address actually reaches the main computer right now — local network first
 * (fast, common case at home), then the VPN address (works away from home, or if the local
 * address is temporarily wrong). Same "check before loading, don't just point a WebView at a
 * URL and hope" design as desktop_app's own Tier 4 (_server_is_up()) — consistent pattern
 * across both native shells, and it's what lets this show a clear status/error instead of a
 * WebView's own ugly connection-error page.
 */
object ConnectionManager {

    sealed class Resolution {
        data class Success(val baseUrl: String, val via: Via) : Resolution()
        // wolAttempted: true when we tried to wake the machine and it still never answered
        // — MainActivity uses this to show a different message than "never tried" (Tier
        // 7a's own requirement: be honest about whether waking was even attempted, not
        // just "unreachable" for every failure alike).
        data class Unreachable(val wolAttempted: Boolean) : Resolution()
    }

    enum class Via { LOCAL, VPN }

    private const val LOCAL_TIMEOUT_MS = 1500
    private const val VPN_TIMEOUT_MS = 3000
    private const val WOL_RETRY_WINDOW_MS = 30_000L
    private const val WOL_RETRY_INTERVAL_MS = 2_000L

    /** GET {baseUrl}health, same endpoint server.py already exposes for the desktop app's
     * own health check — true only on a real 200 within timeoutMs, not just "the socket
     * connected" (a connected-but-hung server would otherwise look reachable). Takes a full
     * base URL (scheme included) rather than a bare host:port — local and VPN use different
     * schemes (see ConnectionPrefs' docstring on why VPN is https), this stays agnostic. */
    private suspend fun isReachable(baseUrl: String, timeoutMs: Int): Boolean = withContext(Dispatchers.IO) {
        var connection: HttpURLConnection? = null
        try {
            connection = URL("${baseUrl}health").openConnection() as HttpURLConnection
            connection.connectTimeout = timeoutMs
            connection.readTimeout = timeoutMs
            connection.requestMethod = "GET"
            connection.responseCode == 200
        } catch (e: Exception) {
            false
        } finally {
            connection?.disconnect()
        }
    }

    private fun isOnWifi(context: Context): Boolean {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val network = cm.activeNetwork ?: return false
        val capabilities = cm.getNetworkCapabilities(network) ?: return false
        return capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
    }

    /**
     * Tier 7 (local -> VPN) plus Tier 7a (Wake-on-LAN): if the local address fails AND the
     * phone is actually on wifi right now, send a magic packet and retry the local address
     * for up to 30s before falling back to VPN. Deliberately gated on isOnWifi(context) —
     * a WOL broadcast sent while on cellular/VPN-only can't reach the target machine's LAN
     * at all (see WakeOnLan's own docstring), so skipping it there isn't a missed
     * opportunity, it's not pretending to try something that cannot work — exactly what the
     * spec asked for rather than silently attempting it anyway.
     *
     * `onStatus` reports human-readable progress (mirrors desktop_app's splash countdown)
     * so MainActivity can show "Waking Jarvis… (Ns)" instead of a static spinner during the
     * retry window — optional, defaults to a no-op so ConnectionManager stays usable
     * anywhere (e.g. SettingsActivity's "connected via" check) without needing a UI callback.
     */
    suspend fun resolve(
        context: Context,
        attemptWake: Boolean = true,
        onStatus: (String) -> Unit = {},
    ): Resolution {
        val local = ConnectionPrefs.localAddress(context)
        val localUrl = "http://$local/"
        if (isReachable(localUrl, LOCAL_TIMEOUT_MS)) {
            return Resolution.Success(localUrl, Via.LOCAL)
        }

        // attemptWake=false is SettingsActivity's own "just tell me the current state"
        // check — it would be a bad surprise for opening Settings to silently trigger a
        // 30-second wake-and-retry cycle every time, when all that screen wants is a quick
        // reachability snapshot.
        var wolAttempted = false
        if (attemptWake && isOnWifi(context)) {
            wolAttempted = true
            val mac = ConnectionPrefs.wolMac(context)
            onStatus("Waking ${ConnectionPrefs.computerName(context)}…")
            WakeOnLan.sendMagicPacketAsync(context, mac)

            val deadline = System.currentTimeMillis() + WOL_RETRY_WINDOW_MS
            while (System.currentTimeMillis() < deadline) {
                delay(WOL_RETRY_INTERVAL_MS)
                if (isReachable(localUrl, LOCAL_TIMEOUT_MS)) {
                    return Resolution.Success(localUrl, Via.LOCAL)
                }
                val remaining = ((deadline - System.currentTimeMillis()) / 1000).coerceAtLeast(0)
                onStatus("Waking ${ConnectionPrefs.computerName(context)}… (${remaining}s)")
            }
        }

        // https, not http — see ConnectionPrefs' docstring: this is what makes the VPN path
        // a secure context (Tier 8's requirement for getUserMedia to even be exposed to the
        // page's JS at all), reusing the real cert Caddy already provisions for this domain.
        val vpn = ConnectionPrefs.vpnAddress(context)
        val vpnUrl = "https://$vpn/"
        onStatus("Trying VPN…")
        if (isReachable(vpnUrl, VPN_TIMEOUT_MS)) {
            return Resolution.Success(vpnUrl, Via.VPN)
        }

        return Resolution.Unreachable(wolAttempted)
    }
}
