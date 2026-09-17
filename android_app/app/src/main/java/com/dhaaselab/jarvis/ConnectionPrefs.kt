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
 * VPN address is the *https* domain (ai.dhaaselab.com) — kept purely as the away-from-home
 * fallback now (see localHttpsPort below for the at-home secure-context path). Reuses the
 * real trusted cert Caddy already provisions via DNS-01 (see caddy/Caddyfile).
 *
 * localHttpsPort adds a second, LAN-only secure context: a Caddy site bound to this
 * machine's LAN IP with an mkcert-issued cert (see REMOTE_ACCESS.md's "Local HTTPS on the
 * LAN" section) trusted on-device via mkcert's root CA. ConnectionManager tries
 * https://<local>:<localHttpsPort> before falling back to plain http://<local> (pre-setup
 * or if the cert isn't trusted on this device yet) and finally the VPN domain — so mic,
 * push, and PWA install all now work at home too, without needing Tailscale/Twingate
 * connected at all. VPN stays reserved for genuinely being away from home.
 */
object ConnectionPrefs {
    private const val PREFS_NAME = "jarvis_connection"
    private const val KEY_COMPUTER_NAME = "computer_name"
    private const val KEY_LOCAL_ADDRESS = "local_address"
    private const val KEY_LOCAL_HTTPS_PORT = "local_https_port"
    private const val KEY_VPN_ADDRESS = "vpn_address"
    private const val KEY_WOL_MAC = "wol_mac"

    // Devin's own confirmed values from Part 2's interview — editable in-app afterward,
    // this is only ever the first-run default.
    private const val DEFAULT_COMPUTER_NAME = "Jarvis"
    private const val DEFAULT_LOCAL_ADDRESS = "192.168.1.147:8765"
    private const val DEFAULT_LOCAL_HTTPS_PORT = "8443"
    private const val DEFAULT_VPN_ADDRESS = "ai.dhaaselab.com"
    private const val DEFAULT_WOL_MAC = "38-F7-CD-D1-8B-00"

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun computerName(context: Context): String =
        prefs(context).getString(KEY_COMPUTER_NAME, DEFAULT_COMPUTER_NAME) ?: DEFAULT_COMPUTER_NAME

    fun localAddress(context: Context): String =
        prefs(context).getString(KEY_LOCAL_ADDRESS, DEFAULT_LOCAL_ADDRESS) ?: DEFAULT_LOCAL_ADDRESS

    fun localHttpsPort(context: Context): String =
        prefs(context).getString(KEY_LOCAL_HTTPS_PORT, DEFAULT_LOCAL_HTTPS_PORT) ?: DEFAULT_LOCAL_HTTPS_PORT

    fun vpnAddress(context: Context): String =
        prefs(context).getString(KEY_VPN_ADDRESS, DEFAULT_VPN_ADDRESS) ?: DEFAULT_VPN_ADDRESS

    fun wolMac(context: Context): String =
        prefs(context).getString(KEY_WOL_MAC, DEFAULT_WOL_MAC) ?: DEFAULT_WOL_MAC

    fun save(
        context: Context,
        computerName: String,
        localAddress: String,
        vpnAddress: String,
        localHttpsPort: String = DEFAULT_LOCAL_HTTPS_PORT,
    ) {
        prefs(context).edit()
            .putString(KEY_COMPUTER_NAME, computerName)
            .putString(KEY_LOCAL_ADDRESS, localAddress)
            .putString(KEY_LOCAL_HTTPS_PORT, localHttpsPort)
            .putString(KEY_VPN_ADDRESS, vpnAddress)
            .apply()
    }
}
