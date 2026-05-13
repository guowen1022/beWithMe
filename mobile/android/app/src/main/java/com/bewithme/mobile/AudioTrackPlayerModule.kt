package com.bewithme.mobile

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.util.Base64
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod

/**
 * Streaming PCM16LE playback. The JS side calls `ensureStream(sampleRate)` once
 * per voice turn, then `writePcm16(base64)` for each chunk arriving from
 * /api/speak/stream, then `flush()` when the stream ends.
 *
 * Strict-order playback is naturally enforced — AudioTrack consumes writes in
 * arrival order. The JS layer awaits each writePcm16 promise before sending
 * the next chunk.
 */
class AudioTrackPlayerModule(context: ReactApplicationContext) : ReactContextBaseJavaModule(context) {

    private var track: AudioTrack? = null
    private var currentSampleRate: Int = 0

    override fun getName(): String = "AudioTrackPlayerModule"

    @ReactMethod
    fun ensureStream(sampleRate: Double, promise: Promise) {
        val sr = sampleRate.toInt()
        try {
            synchronized(this) {
                if (track != null && currentSampleRate == sr) {
                    promise.resolve(null)
                    return
                }
                releaseTrack()
                val minBuf = AudioTrack.getMinBufferSize(
                    sr,
                    AudioFormat.CHANNEL_OUT_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                )
                val bufSize = maxOf(minBuf, sr * 2)  // ~1s of headroom (mono 16-bit)
                track = AudioTrack.Builder()
                    .setAudioAttributes(
                        AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                            .build()
                    )
                    .setAudioFormat(
                        AudioFormat.Builder()
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setSampleRate(sr)
                            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                            .build()
                    )
                    .setBufferSizeInBytes(bufSize)
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .build()
                track?.play()
                currentSampleRate = sr
            }
            promise.resolve(null)
        } catch (e: Exception) {
            promise.reject("audio_track_init", e.message, e)
        }
    }

    @ReactMethod
    fun writePcm16(base64Bytes: String, promise: Promise) {
        try {
            val bytes = Base64.decode(base64Bytes, Base64.NO_WRAP)
            val t = track ?: run {
                promise.reject("audio_track_not_initialized", "ensureStream() not called")
                return
            }
            // Blocking write — keeps order, returns when buffer accepts the data.
            t.write(bytes, 0, bytes.size, AudioTrack.WRITE_BLOCKING)
            promise.resolve(null)
        } catch (e: Exception) {
            promise.reject("audio_track_write", e.message, e)
        }
    }

    @ReactMethod
    fun flush(promise: Promise) {
        try {
            // Nothing to flush in MODE_STREAM — buffered samples play out naturally.
            // We just resolve so the JS-side voiceTurn knows "speaker is committed".
            promise.resolve(null)
        } catch (e: Exception) {
            promise.reject("audio_track_flush", e.message, e)
        }
    }

    @ReactMethod
    fun stop(promise: Promise) {
        try {
            synchronized(this) { releaseTrack() }
            promise.resolve(null)
        } catch (e: Exception) {
            promise.reject("audio_track_stop", e.message, e)
        }
    }

    private fun releaseTrack() {
        try {
            track?.pause()
            track?.flush()
            track?.stop()
            track?.release()
        } catch (_: Exception) { /* idempotent */ }
        track = null
        currentSampleRate = 0
    }

    override fun invalidate() {
        synchronized(this) { releaseTrack() }
        super.invalidate()
    }

    // Required by RN's NativeEventEmitter contract — unused but must exist.
    @ReactMethod
    fun addListener(@Suppress("UNUSED_PARAMETER") eventName: String) {}

    @ReactMethod
    fun removeListeners(@Suppress("UNUSED_PARAMETER") count: Int) {}
}
