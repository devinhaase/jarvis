package com.dhaaselab.jarvis

import android.content.Context

/**
 * Where "the main computer" actually is — stored in SharedPreferences, not hardcoded, so
 * Devin can update it from the Settings screen without a rebuild if the network changes
 * (Tier 7's own requirement).
 *
 * Local address is plain "host:port" (no scheme) — Wake-on-LAN (Tier 7a) needs the bare
 * host separately from the http:// URL a WebView actually loads, and it's genuinely served
 * over plain HTTP (server.py has no TLS of its own on the LAN).
 *
 * VPN address is the *https* domain (ai.dhaaselab.com), not the bare Tailscale IP+port —
 * real bug hit live building Tier 8: Android WebView only exposes getUserMedia() (and other
 * powerful APIs) in a secure context, and plain http://<IP> over Tailscale doesn't qualify
 * any more than a LAN IP does. ai.dhaaselab.com already has a real trusted cert via Caddy +
 * DNS-01 (see caddy/Caddyfile, set up earlier this same project for exactly this class of
 * problem — the desktop browser hit the identical secure-context wall for mic access
 * before this Android work even started) — reusing that existing infrastructure instead of
 * standing up something new. The local/LAN path has no equivalent fix here: it stays plain
 * HTTP, so mic input specifically won't work while connected that way (everything else —
 * chat, dashboard, notifications — is unaffected, this is scoped to getUserMedia only).
 */
object ConnectionPrefs {
    private const val PREFS_NAME = "jarvis_connection"
    private const val KEY_COMPUTER_NAME = "computer_name"
    private const val KEY_LOCAL_ADDRESS = "local_address"
    private const val KEY_VPN_ADDRESS = "vpn_address"
    private const val KEY_WOL_MAC = "wol_mac"

    // Devin's own confirmed values from Part 2's interview — editable in-app afterward,
    // this is only ever the first-run default.
    private const val DEFAULT_COMPUTER_NAME = "Jarvis"
    private const val DEFAULT_LOCAL_ADDRESS = "192.168.1.147:8765"
    private const val DEFAULT_VPN_ADDRESS = "ai.dhaaselab.com"
    private const val DEFAULT_WOL_MAC = "38-F7-CD-D1-8B-00"

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun computerName(context: Context): String =
        prefs(context).getString(KEY_COMPUTER_NAME, DEFAULT_COMPUTER_NAME) ?: DEFAULT_COMPUTER_NAME

    fun localAddress(context: Context): String =
        prefs(context).getString(KEY_LOCAL_ADDRESS, DEFAULT_LOCAL_ADDRESS) ?: DEFAULT_LOCAL_ADDRESS

    fun vpnAddress(context: Context): String =
        prefs(context).getString(KEY_VPN_ADDRESS, DEFAULT_VPN_ADDRESS) ?: DEFAULT_VPN_ADDRESS

    fun wolMac(context: Context): String =
        prefs(context).getString(KEY_WOL_MAC, DEFAULT_WOL_MAC) ?: DEFAULT_WOL_MAC

    fun save(context: Context, computerName: String, localAddress: String, vpnAddress: String) {
        prefs(context).edit()
            .putString(KEY_COMPUTER_NAME, computerName)
            .putString(KEY_LOCAL_ADDRESS, localAddress)
            .putString(KEY_VPN_ADDRESS, vpnAddress)
            .apply()
    }
}
