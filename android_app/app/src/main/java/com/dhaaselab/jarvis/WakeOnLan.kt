package com.dhaaselab.jarvis

import android.content.Context
import android.net.wifi.WifiManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress

/**
 * Sends a standard Wake-on-LAN magic packet — 6 bytes of 0xFF followed by the target MAC
 * repeated 16 times, broadcast as one UDP datagram. This only ever propagates on the local
 * L2 broadcast domain the sending device is actually on — there is no way to make this reach
 * a machine that's on a different network (over the internet, over a VPN tunnel) without the
 * router specifically configured to relay it. ConnectionManager only ever calls this when the
 * phone itself is confirmed on wifi, for exactly that reason — see its own comment.
 */
object WakeOnLan {

    fun sendMagicPacket(context: Context, macAddress: String): Boolean {
        val macBytes = parseMac(macAddress) ?: return false
        val packet = ByteArray(6 + 16 * macBytes.size)
        for (i in 0 until 6) packet[i] = 0xFF.toByte()
        for (i in 6 until packet.size step macBytes.size) {
            System.arraycopy(macBytes, 0, packet, i, macBytes.size)
        }

        val broadcastAddress = subnetBroadcastAddress(context) ?: "255.255.255.255"
        return try {
            DatagramSocket().use { socket ->
                socket.broadcast = true
                val address = InetAddress.getByName(broadcastAddress)
                // Port 9 (discard) is the conventional WOL port — the packet's contents are
                // what matters, nothing is actually listening on/replying over this port.
                socket.send(DatagramPacket(packet, packet.size, address, 9))
            }
            true
        } catch (e: Exception) {
            false
        }
    }

    suspend fun sendMagicPacketAsync(context: Context, macAddress: String): Boolean =
        withContext(Dispatchers.IO) { sendMagicPacket(context, macAddress) }

    private fun parseMac(mac: String): ByteArray? {
        val parts = mac.trim().split("-", ":")
        if (parts.size != 6) return null
        return try {
            ByteArray(6) { i -> parts[i].toInt(16).toByte() }
        } catch (e: NumberFormatException) {
            null
        }
    }

    /** Computed from the phone's own current DHCP info (IP & subnet mask) rather than
     * always using the global 255.255.255.255 broadcast — some networks/carriers filter the
     * global broadcast address but allow a subnet-directed one (e.g. 192.168.1.255), so this
     * gives WOL a real second chance at actually being delivered instead of just hoping the
     * global address gets through. Falls back to null (caller uses 255.255.255.255) if wifi
     * info isn't available for any reason — never a hard failure. */
    @Suppress("DEPRECATION")  // WifiManager.dhcpInfo has no ConnectivityManager-based replacement for this specific "give me the subnet mask" use case
    private fun subnetBroadcastAddress(context: Context): String? {
        return try {
            val wifiManager = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
                ?: return null
            val dhcp = wifiManager.dhcpInfo ?: return null
            val broadcast = (dhcp.ipAddress and dhcp.netmask) or dhcp.netmask.inv()
            val bytes = byteArrayOf(
                (broadcast and 0xFF).toByte(),
                (broadcast shr 8 and 0xFF).toByte(),
                (broadcast shr 16 and 0xFF).toByte(),
                (broadcast shr 24 and 0xFF).toByte(),
            )
            InetAddress.getByAddress(bytes).hostAddress
        } catch (e: Exception) {
            null
        }
    }
}
