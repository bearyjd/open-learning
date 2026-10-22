package org.openlearning.app

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.net.URI
import java.security.KeyStore
import java.security.MessageDigest
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class Connection(val server: String, val token: String) {
    // Scope includes the account credential as well as server, so account changes cannot expose old caches.
    val scope: String get() = MessageDigest.getInstance("SHA-256").digest("$server\u0000$token".toByteArray()).joinToString("") { "%02x".format(it) }
}
private const val LEGACY_PREVIOUS_SERVER = "previousServer"
class Credentials(context: Context) {
    private val preferences = context.getSharedPreferences("connection", Context.MODE_PRIVATE)
    // The previous-server key of earlier builds is deleted, so devices that stored it lose it.
    private val mutable = MutableStateFlow(read().also { preferences.edit().remove(LEGACY_PREVIOUS_SERVER).apply() })
    val connection = mutable.asStateFlow()
    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        return (store.getKey("open_learning_token", null) as? SecretKey) ?: KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").run {
            init(KeyGenParameterSpec.Builder("open_learning_token", KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
            generateKey()
        }
    }
    private fun read(): Connection? = runCatching {
        val server = preferences.getString("server", null) ?: return null
        val encrypted = Base64.decode(preferences.getString("token", null) ?: return null, Base64.NO_WRAP)
        val iv = Base64.decode(preferences.getString("iv", null) ?: return null, Base64.NO_WRAP)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, iv)); updateAAD(server.toByteArray()) }
        Connection(server, cipher.doFinal(encrypted).toString(Charsets.UTF_8))
    }.getOrNull()
    fun save(serverInput: String, tokenInput: String, allowHttp: Boolean) {
        val server = normalizeServer(serverInput, allowHttp)
        val token = tokenInput.trim()
        require(token.isNotEmpty() && !token.contains('\n') && !token.contains('\r')) { "Enter a valid server token." }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()); updateAAD(server.toByteArray()) }
        val encrypted = cipher.doFinal(token.toByteArray())
        check(preferences.edit().putString("server", server).remove(LEGACY_PREVIOUS_SERVER)
            .putString("token", Base64.encodeToString(encrypted, Base64.NO_WRAP))
            .putString("iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP)).commit()) { "Could not store the connection." }
        mutable.value = Connection(server, token)
    }
}
fun normalizeServer(input: String, allowHttp: Boolean): String {
    val uri = runCatching { URI(input.trim()) }.getOrElse { throw IllegalArgumentException("Enter a complete server address.") }
    require(uri.host != null && uri.userInfo == null && uri.rawQuery == null && uri.rawFragment == null) { "Use a server URL without credentials, query, or fragment." }
    require(uri.scheme == "https" || (uri.scheme == "http" && allowHttp)) { "Use HTTPS, or explicitly allow HTTP for a trusted local server." }
    return uri.toString().trimEnd('/') + "/"
}
fun sharedUrl(text: String): String? = Regex("https?://[^\\s<>]+", RegexOption.IGNORE_CASE).find(text)?.value?.trimEnd('.', ',', ')', ']', '"', '\'')
