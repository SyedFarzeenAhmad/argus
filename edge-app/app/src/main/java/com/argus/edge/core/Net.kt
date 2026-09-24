package com.argus.edge.core

import java.net.Inet4Address
import java.net.NetworkInterface

object Net {
    const val FRAME_PORT = 7070
    const val API_PORT = 8080

    /**
     * Every site-local IPv4 address of this phone. Includes the hotspot interface, which the
     * ConnectivityManager "active network" does not report when the phone is the hotspot host.
     */
    fun localIpv4(): List<String> = runCatching {
        NetworkInterface.getNetworkInterfaces().toList()
            .filter { it.isUp && !it.isLoopback }
            .flatMap { it.inetAddresses.toList() }
            .filterIsInstance<Inet4Address>()
            .filter { it.isSiteLocalAddress }
            .map { it.hostAddress ?: "" }
            .filter { it.isNotEmpty() }
            .distinct()
    }.getOrDefault(emptyList())
}
