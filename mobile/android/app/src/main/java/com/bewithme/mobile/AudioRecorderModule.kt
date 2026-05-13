package com.bewithme.mobile

import android.Manifest
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import androidx.core.content.ContextCompat
import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.ReadableMap
import com.facebook.react.modules.core.DeviceEventManagerModule
import java.io.File
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * 16 kHz mono Int16 frame stream from AudioRecord. Uses VOICE_COMMUNICATION
 * source for built-in AEC; defensively also enables AcousticEchoCanceler and
 * NoiseSuppressor on the active audio session (some OEMs don't auto-enable
 * via the VOICE_COMMUNICATION source alone).
 *
 * Frames are emitted via DeviceEventEmitter as "AudioRecorderFrame" events
 * carrying { data: number[] } (one frame = 512 samples = 32 ms @ 16 kHz).
 */
class AudioRecorderModule(private val ctx: ReactApplicationContext) :
    ReactContextBaseJavaModule(ctx) {

    private var record: AudioRecord? = null
    private var aec: AcousticEchoCanceler? = null
    private var ns: NoiseSuppressor? = null
    private var captureThread: Thread? = null
    @Volatile private var running: Boolean = false

    // Accumulator: when active, we ALSO write samples to a WAV file on disk so
    // JS can hand the URI to /api/transcribe without needing to build a Blob.
    // Bytes are appended in PCM16LE; the WAV header is rewritten on stop.
    private var wavFile: File? = null
    private var wavRaf: RandomAccessFile? = null
    private var wavSampleRate: Int = 16000
    private var wavBytesWritten: Long = 0L

    override fun getName(): String = "AudioRecorderModule"

    @ReactMethod
    fun start(opts: ReadableMap, promise: Promise) {
        try {
            val granted = ContextCompat.checkSelfPermission(
                ctx, Manifest.permission.RECORD_AUDIO
            ) == PackageManager.PERMISSION_GRANTED
            if (!granted) {
                promise.reject("permission_denied", "RECORD_AUDIO not granted")
                return
            }

            synchronized(this) {
                if (running) { promise.resolve(null); return }
                val sampleRate = if (opts.hasKey("sampleRate")) opts.getInt("sampleRate") else 16000
                val frameSamples = if (opts.hasKey("frameSamples")) opts.getInt("frameSamples") else 512
                // PTT mode wants the recorder to accumulate samples to a WAV file
                // on the side so stop() can return a URI for transcribe. Ambient
                // mode does its own per-phrase WAV write in JS (after VAD detects
                // a phrase end), so it disables this to avoid an unbounded WAV
                // file growing for the entire ambient session.
                val accumulateWav = if (opts.hasKey("accumulateWav")) opts.getBoolean("accumulateWav") else true
                val minBuf = AudioRecord.getMinBufferSize(
                    sampleRate,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                )
                // 4 frames of headroom keeps the driver buffer from underrunning
                // even if the consumer (JS thread) blips.
                val bufSize = maxOf(minBuf, frameSamples * 2 * 4)
                val rec = AudioRecord(
                    MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                    sampleRate,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    bufSize,
                )
                if (rec.state != AudioRecord.STATE_INITIALIZED) {
                    rec.release()
                    promise.reject("audio_record_init", "AudioRecord failed to initialize")
                    return
                }
                record = rec
                attachAudioFx(rec.audioSessionId)
                if (accumulateWav) openWavSink(sampleRate)
                rec.startRecording()
                running = true
                captureThread = Thread({ captureLoop(rec, frameSamples) }, "BeWithMe-AudioCapture").also { it.start() }
            }
            promise.resolve(null)
        } catch (e: Exception) {
            promise.reject("audio_record_start", e.message, e)
        }
    }

    @ReactMethod
    fun stop(promise: Promise) {
        try {
            val uri: String?
            synchronized(this) {
                releaseRecord()
                uri = closeWavSink()
            }
            promise.resolve(uri)  // file:// URI or null if nothing recorded
        } catch (e: Exception) {
            promise.reject("audio_record_stop", e.message, e)
        }
    }

    private fun captureLoop(rec: AudioRecord, frameSamples: Int) {
        val frame = ShortArray(frameSamples)
        val byteBuf = ByteBuffer.allocate(frameSamples * 2).order(ByteOrder.LITTLE_ENDIAN)
        while (running) {
            val n = try {
                rec.read(frame, 0, frame.size, AudioRecord.READ_BLOCKING)
            } catch (_: Exception) { -1 }
            if (n <= 0) {
                if (!running) break
                continue
            }

            // Append PCM16LE to the WAV sink for the JS-friendly URI path.
            byteBuf.clear()
            for (i in 0 until n) byteBuf.putShort(frame[i])
            byteBuf.flip()
            try {
                wavRaf?.write(byteBuf.array(), 0, byteBuf.limit())
                wavBytesWritten += byteBuf.limit()
            } catch (_: Exception) { /* drop on disk error */ }

            // Also emit the frame to JS for VAD (Phase 1 step 7).
            val arr = Arguments.createArray()
            for (i in 0 until n) arr.pushInt(frame[i].toInt())
            val payload = Arguments.createMap().apply { putArray("data", arr) }
            try {
                ctx.getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
                    .emit("AudioRecorderFrame", payload)
            } catch (_: Exception) {
                // Bridge tear-down race during reload; drop silently.
            }
        }
    }

    private fun openWavSink(sampleRate: Int) {
        try {
            val cacheDir = ctx.cacheDir
            val file = File(cacheDir, "turn_${System.currentTimeMillis()}.wav")
            val raf = RandomAccessFile(file, "rw")
            raf.setLength(0)
            // Pre-write a 44-byte placeholder header. Sizes get patched on close.
            writeWavHeader(raf, sampleRate, 0)
            wavFile = file
            wavRaf = raf
            wavSampleRate = sampleRate
            wavBytesWritten = 0L
        } catch (_: Exception) {
            wavFile = null
            wavRaf = null
        }
    }

    private fun closeWavSink(): String? {
        val raf = wavRaf ?: return null
        val file = wavFile
        val dataSize = wavBytesWritten
        try {
            raf.seek(0)
            writeWavHeader(raf, wavSampleRate, dataSize.toInt())
            raf.close()
        } catch (_: Exception) { /* ignore */ }
        wavRaf = null
        wavFile = null
        wavBytesWritten = 0L
        return file?.let { "file://${it.absolutePath}" }
    }

    private fun writeWavHeader(raf: RandomAccessFile, sampleRate: Int, dataSize: Int) {
        val numChannels = 1
        val bytesPerSample = 2
        val byteRate = sampleRate * numChannels * bytesPerSample
        val blockAlign = numChannels * bytesPerSample

        val header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
        header.put("RIFF".toByteArray(Charsets.US_ASCII))
        header.putInt(36 + dataSize)
        header.put("WAVE".toByteArray(Charsets.US_ASCII))
        header.put("fmt ".toByteArray(Charsets.US_ASCII))
        header.putInt(16)
        header.putShort(1)                       // PCM
        header.putShort(numChannels.toShort())
        header.putInt(sampleRate)
        header.putInt(byteRate)
        header.putShort(blockAlign.toShort())
        header.putShort(16)                      // bits per sample
        header.put("data".toByteArray(Charsets.US_ASCII))
        header.putInt(dataSize)
        raf.write(header.array())
    }

    private fun attachAudioFx(sessionId: Int) {
        try {
            if (AcousticEchoCanceler.isAvailable()) {
                aec = AcousticEchoCanceler.create(sessionId)?.apply { enabled = true }
            }
            if (NoiseSuppressor.isAvailable()) {
                ns = NoiseSuppressor.create(sessionId)?.apply { enabled = true }
            }
        } catch (_: Exception) { /* best-effort */ }
    }

    private fun releaseRecord() {
        running = false
        try { captureThread?.join(500) } catch (_: Exception) {}
        captureThread = null
        try { record?.stop() } catch (_: Exception) {}
        try { record?.release() } catch (_: Exception) {}
        record = null
        try { aec?.release() } catch (_: Exception) {}
        try { ns?.release() } catch (_: Exception) {}
        aec = null
        ns = null
    }

    override fun invalidate() {
        synchronized(this) { releaseRecord() }
        super.invalidate()
    }

    @ReactMethod
    fun addListener(@Suppress("UNUSED_PARAMETER") eventName: String) {}

    @ReactMethod
    fun removeListeners(@Suppress("UNUSED_PARAMETER") count: Int) {}
}
