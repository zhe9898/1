package io.zen70.healthgateway

import okhttp3.OkHttpClient
import okhttp3.tls.HandshakeCertificates
import java.io.File
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit

/**
 * Factory for a TLS-pinned OkHttpClient bound to the gateway CA certificate.
 *
 * Certificate pinning strategy:
 * - Load the gateway self-signed CA from [GatewayBootstrapConfig.gatewayCaFile].
 * - Trust ONLY that CA — the system trust store is intentionally excluded to
 *   prevent MITM attacks via rogue public CAs.
 *
 * Usage:
 * ```kotlin
 * val config = GatewayBootstrapConfig(...)
 * val client = GatewayHttpClient.build(config)
 * val request = Request.Builder().url("${config.gatewayBaseUrl}/v1/nodes/heartbeat").build()
 * client.newCall(request).execute()
 * ```
 *
 * Security notes:
 * - [GatewayBootstrapConfig.nodeToken] must be retrieved from Android Keystore
 *   (EncryptedSharedPreferences) before constructing requests, not held in memory
 *   beyond the lifetime of a single request.
 * - Rotate [gatewayCaFile] via the gateway bootstrap endpoint when the CA renews.
 */
object GatewayHttpClient {

    /**
     * Builds an [OkHttpClient] that trusts only the gateway CA certificate.
     *
     * @param config Bootstrap configuration containing the CA file path.
     * @return A pinned [OkHttpClient] ready for use against the gateway.
     * @throws java.io.IOException if the CA file cannot be read.
     * @throws java.security.cert.CertificateException if the CA file is malformed.
     */
    fun build(config: GatewayBootstrapConfig): OkHttpClient {
        val caFile = File(config.gatewayCaFile)
        val caCert: X509Certificate = caFile.inputStream().use { stream ->
            CertificateFactory.getInstance("X.509")
                .generateCertificate(stream) as X509Certificate
        }

        val certificates = HandshakeCertificates.Builder()
            .addTrustedCertificate(caCert)
            // Do NOT call .addPlatformTrustedCertificates() — system CAs are excluded
            // to enforce pinning to the gateway CA only.
            .build()

        return OkHttpClient.Builder()
            .sslSocketFactory(certificates.sslSocketFactory(), certificates.trustManager)
            .hostnameVerifier { hostname, session ->
                // Verify that the server certificate SAN contains the target hostname.
                // GeneralName type codes: 2 = dNSName, 7 = iPAddress.
                // Only dNSName SANs (type 2) are compared against hostname strings;
                // this prevents IP-address SANs from satisfying DNS hostname checks.
                session.peerCertificates
                    .filterIsInstance<X509Certificate>()
                    .any { cert ->
                        cert.subjectAlternativeNames
                            ?.filterNotNull()
                            ?.any { san ->
                                san.size >= 2 && san[0] == 2 && san[1].toString() == hostname
                            }
                            ?: false
                    }
            }
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .build()
    }
}
