@preconcurrency import AVFoundation
import Combine
import Foundation
import OSLog
import VoiceAgentCore

@MainActor
final class AudioController: NSObject, ObservableObject {
    @Published private(set) var isCapturing = false
    @Published private(set) var isPlaying = false
    @Published private(set) var diagnostics = ClientAudioDiagnostics()
    @Published private(set) var availableInputDevices: [AudioInputDevice] = []
    @Published private(set) var selectedInputDeviceUID = ""
    @Published private(set) var playbackQueueDepth = 0
    @Published private(set) var playedSegments = 0
    @Published private(set) var currentSegmentIndex: Int?

    var onInputAudio: ((Data) -> Void)?
    var onPlaybackDrained: ((String) -> Void)?

    private let logger = Logger(subsystem: "local.voice-agent", category: "audio")
    private let captureEngine = AVAudioEngine()
    private var playbackQueue = AudioPlaybackQueue()
    private var currentSegment: AudioSegment?
    private var player: AVAudioPlayer?
    private let playbackDelegate = AudioPlaybackDelegate()
    private var diagnosticPlayer: AVAudioPlayer?
    private var diagnosticPCM = Data()
    private var diagnosticHasSignal = false
    private var diagnosticTask: Task<Void, Never>?
    private var captureStartedAt: Date?
    private var lastSourceEndSampleTime: AVAudioFramePosition?
    private var digitalSilenceSamples = 0
    private var zeroWarningShown = false
    private var requestedVoiceProcessing = true
    private var captureCounters = AudioCaptureCounters()
    private var deviceDiscoveryWarning: String?
    private static let systemDefaultSelection = "__system_default__"
    var systemDefaultSelectionID: String { Self.systemDefaultSelection }
    private let targetFormat = AVAudioFormat(
        commonFormat: .pcmFormatInt16,
        sampleRate: 16_000,
        channels: 1,
        interleaved: false
    )!

    override init() {
        super.init()
        loadInputDevices()
        playbackDelegate.onFinished = { [weak self] player, successfully in
            Task { @MainActor in self?.playbackDidFinish(player: player, successfully: successfully) }
        }
        playbackDelegate.onDecodeError = { [weak self] player, error in
            Task { @MainActor in self?.playbackDecodeFailed(player: player, error: error) }
        }
    }

    func startCapture() async throws {
        guard !isCapturing else { return }
        let allowed = await microphonePermission()
        guard allowed else { throw AudioError.microphoneDenied }
        loadInputDevices()
        if deviceDiscoveryWarning == nil {
            let defaultUID = AudioInputDeviceManager.systemDefaultDevice(
                in: availableInputDevices
            )?.uid
            if AudioCapturePolicy.plan(
                permission: .allowed,
                systemDefaultUID: defaultUID,
                requestedUID: nil
            ) == .blockedNoSystemInput {
                throw AudioError.noInputDevice
            }
        }
        try startCaptureEngine(voiceProcessing: requestedVoiceProcessing)
    }

    private func startCaptureEngine(voiceProcessing: Bool) throws {
        let input = captureEngine.inputNode
        var startupWarning = deviceDiscoveryWarning
        try? input.setVoiceProcessingEnabled(false)
        if voiceProcessing {
            do {
                try input.setVoiceProcessingEnabled(true)
                input.isVoiceProcessingInputMuted = false
                logger.info("Voice processing/AEC enabled and input explicitly unmuted")
            } catch {
                try? input.setVoiceProcessingEnabled(false)
                let nsError = error as NSError
                startupWarning = L10n.text(
                    "error.audio.aec_unavailable",
                    nsError.domain,
                    Int64(nsError.code),
                    nsError.localizedDescription
                )
                logger.warning("Voice processing unavailable [\(nsError.domain, privacy: .public): \(nsError.code)]; using raw microphone: \(nsError.localizedDescription, privacy: .public)")
            }
        } else {
            try? input.setVoiceProcessingEnabled(false)
        }

        let sourceFormat = input.outputFormat(forBus: 0)
        guard sourceFormat.sampleRate > 0, sourceFormat.channelCount > 0 else {
            throw AudioError.noInputDeviceOrFormat(
                sampleRate: sourceFormat.sampleRate,
                channels: Int(sourceFormat.channelCount)
            )
        }
        guard let pipeline = CapturePipeline(
            sourceFormat: sourceFormat,
            targetFormat: targetFormat
        ) else {
            throw AudioError.pipelineInitialization(
                format: Self.formatDescription(sourceFormat),
                reason: L10n.text("error.audio.invalid_rate")
            )
        }
        let actualDevice = AudioInputDeviceManager.systemDefaultDevice(in: availableInputDevices)
        diagnostics = ClientAudioDiagnostics()
        diagnostics.activeDeviceName = actualDevice?.name ?? L10n.text("error.audio.unknown_device")
        diagnostics.activeDeviceUID = actualDevice?.uid ?? ""
        diagnostics.activeDeviceIsDefault = actualDevice?.isSystemDefault ?? false
        diagnostics.availableInputDevices = availableInputDevices.count
        diagnostics.sourceSampleRate = sourceFormat.sampleRate
        diagnostics.sourceChannels = Int(sourceFormat.channelCount)
        diagnostics.sourceFormat = String(describing: sourceFormat.commonFormat)
        diagnostics.sourceInterleaved = sourceFormat.isInterleaved
        diagnostics.voiceProcessingEnabled = input.isVoiceProcessingEnabled
        diagnostics.voiceProcessingMuted = input.isVoiceProcessingEnabled
            ? input.isVoiceProcessingInputMuted : false
        diagnostics.captureWarning = startupWarning
        captureCounters = AudioCaptureCounters()
        captureStartedAt = Date()
        lastSourceEndSampleTime = nil
        digitalSilenceSamples = 0
        zeroWarningShown = false

        input.installTap(onBus: 0, bufferSize: 1_024, format: sourceFormat) { [weak self] buffer, when in
            let packet = pipeline.convert(buffer: buffer, when: when)
            let conversionFailure = packet == nil ? pipeline.lastFailure : nil
            // The tap runs on the realtime audio thread. Copy/convert there, then
            // cross explicitly to MainActor before touching observable state.
            Task { @MainActor [weak self] in
                self?.consumeCapture(
                    packet,
                    sourceSampleTime: when.isSampleTimeValid ? when.sampleTime : -1,
                    sourceFrameLength: buffer.frameLength,
                    conversionFailure: conversionFailure
                )
            }
        }
        captureEngine.prepare()
        do {
            try captureEngine.start()
            isCapturing = true
            logger.info(
                "Microphone capture source=\(sourceFormat.sampleRate)Hz channels=\(sourceFormat.channelCount) format=\(String(describing: sourceFormat.commonFormat), privacy: .public) interleaved=\(sourceFormat.isInterleaved) target=16000Hz/mono/int16 voiceProcessing=\(input.isVoiceProcessingEnabled) muted=\(self.diagnostics.voiceProcessingMuted)"
            )
        } catch {
            input.removeTap(onBus: 0)
            let nsError = error as NSError
            throw AudioError.engineStart(
                domain: nsError.domain,
                code: nsError.code,
                message: nsError.localizedDescription,
                format: Self.formatDescription(sourceFormat)
            )
        }
    }

    func stopCapture() {
        guard isCapturing else { return }
        captureEngine.inputNode.removeTap(onBus: 0)
        captureEngine.stop()
        diagnosticTask?.cancel()
        diagnosticTask = nil
        diagnostics.diagnosticRecording = false
        isCapturing = false
        logger.info("Microphone capture stopped")
    }

    func enqueue(_ segment: AudioSegment) {
        guard !segment.data.isEmpty else { return }
        playbackQueue.enqueue(segment)
        playbackQueueDepth = playbackQueue.count
        logger.info(
            "Enqueued response=\(segment.descriptor.responseID, privacy: .public) segment=\(segment.descriptor.index) bytes=\(segment.data.count) depth=\(self.playbackQueue.count)"
        )
        playNextIfNeeded()
    }

    func hasPendingPlayback(responseID: String) -> Bool {
        currentSegment?.descriptor.responseID == responseID
            || playbackQueue.contains(responseID: responseID)
    }

    func stopPlayback(responseID: String? = nil) {
        if let responseID {
            playbackQueue.remove(responseID: responseID)
            playbackQueueDepth = playbackQueue.count
            guard currentSegment?.descriptor.responseID == responseID else {
                logger.info("Removed queued playback for response=\(responseID, privacy: .public)")
                return
            }
            logger.info("Stopping cancelled playback response=\(responseID, privacy: .public)")
        } else {
            playbackQueue.removeAll()
            playbackQueueDepth = 0
            logger.info("Stopping all playback")
        }

        player?.stop()
        player = nil
        currentSegment = nil
        currentSegmentIndex = nil
        isPlaying = false
        if responseID != nil {
            playNextIfNeeded()
        }
    }

    private func playbackDidFinish(player finishedPlayer: AVAudioPlayer, successfully: Bool) {
        guard player === finishedPlayer else {
            logger.debug("Ignoring stale AVAudioPlayer completion callback")
            return
        }
        let finished = currentSegment
        logger.info(
            "Finished response=\(finished?.descriptor.responseID ?? "unknown", privacy: .public) segment=\(finished?.descriptor.index ?? -1) success=\(successfully)"
        )
        player = nil
        currentSegment = nil
        currentSegmentIndex = nil
        isPlaying = false
        if successfully { playedSegments += 1 }
        playNextIfNeeded(drainedResponseID: finished?.descriptor.responseID)
    }

    private func playbackDecodeFailed(player failedPlayer: AVAudioPlayer, error: Error?) {
        guard player === failedPlayer else { return }
        let failed = currentSegment
        logger.error(
            "Decode failed response=\(failed?.descriptor.responseID ?? "unknown", privacy: .public) segment=\(failed?.descriptor.index ?? -1): \(error?.localizedDescription ?? "unknown error", privacy: .public)"
        )
        player = nil
        currentSegment = nil
        currentSegmentIndex = nil
        isPlaying = false
        playNextIfNeeded(drainedResponseID: failed?.descriptor.responseID)
    }

    private func playNextIfNeeded(drainedResponseID: String? = nil) {
        guard player == nil else { return }
        guard let segment = playbackQueue.dequeue() else {
            playbackQueueDepth = 0
            if let drainedResponseID {
                logger.info("Playback queue drained for response=\(drainedResponseID, privacy: .public)")
                onPlaybackDrained?(drainedResponseID)
            }
            return
        }
        playbackQueueDepth = playbackQueue.count

        do {
            let nextPlayer = try AVAudioPlayer(data: segment.data)
            nextPlayer.delegate = playbackDelegate
            nextPlayer.prepareToPlay()
            currentSegment = segment
            currentSegmentIndex = segment.descriptor.index
            player = nextPlayer
            isPlaying = nextPlayer.play()
            logger.info(
                "Started response=\(segment.descriptor.responseID, privacy: .public) segment=\(segment.descriptor.index) duration=\(nextPlayer.duration, format: .fixed(precision: 3)) depth=\(self.playbackQueue.count)"
            )
            if !isPlaying {
                logger.error(
                    "AVAudioPlayer refused response=\(segment.descriptor.responseID, privacy: .public) segment=\(segment.descriptor.index)"
                )
                player = nil
                currentSegment = nil
                currentSegmentIndex = nil
                playNextIfNeeded(drainedResponseID: segment.descriptor.responseID)
            }
        } catch {
            logger.error(
                "Could not create player response=\(segment.descriptor.responseID, privacy: .public) segment=\(segment.descriptor.index): \(error.localizedDescription, privacy: .public)"
            )
            currentSegment = nil
            currentSegmentIndex = nil
            player = nil
            playNextIfNeeded(drainedResponseID: segment.descriptor.responseID)
        }
    }

    func setVoiceProcessingRequested(_ enabled: Bool) {
        requestedVoiceProcessing = enabled
        guard isCapturing else { return }
        stopCapture()
        do {
            try startCaptureEngine(voiceProcessing: enabled)
        } catch {
            diagnostics.captureWarning = error.localizedDescription
        }
    }

    func refreshInputDevices() {
        let wasCapturing = isCapturing
        if wasCapturing { stopCapture() }
        loadInputDevices()
        guard wasCapturing else { return }
        do {
            try startCaptureEngine(voiceProcessing: requestedVoiceProcessing)
        } catch {
            diagnostics.captureWarning = error.localizedDescription
        }
    }

    private func loadInputDevices() {
        do {
            availableInputDevices = try AudioInputDeviceManager.availableDevices()
            deviceDiscoveryWarning = nil
        } catch {
            availableInputDevices = []
            deviceDiscoveryWarning = error.localizedDescription
        }
        selectedInputDeviceUID = Self.systemDefaultSelection
        diagnostics.availableInputDevices = availableInputDevices.count
    }

    func selectInputDevice(uid: String) {
        guard uid != Self.systemDefaultSelection else {
            selectedInputDeviceUID = Self.systemDefaultSelection
            return
        }
        guard let requested = availableInputDevices.first(where: { $0.uid == uid }) else {
            return
        }
        selectedInputDeviceUID = Self.systemDefaultSelection
        let defaultUID = AudioInputDeviceManager.systemDefaultDevice(
            in: availableInputDevices
        )?.uid
        let plan = AudioCapturePolicy.plan(
            permission: .allowed,
            systemDefaultUID: defaultUID,
            requestedUID: requested.uid
        )
        if case .systemDefault = plan {
            diagnostics.captureWarning = nil
        } else {
            diagnostics.captureWarning = L10n.text("error.audio.routing_fallback", requested.name)
        }
    }

    func startDiagnosticSample(seconds: Double = 5) {
        guard isCapturing, !diagnostics.diagnosticRecording else { return }
        diagnosticPCM.removeAll(keepingCapacity: true)
        diagnosticHasSignal = false
        diagnostics.diagnosticRecording = true
        diagnostics.lastDiagnosticFile = nil
        diagnosticTask?.cancel()
        diagnosticTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(max(1, seconds) * 1_000_000_000))
            guard !Task.isCancelled else { return }
            await MainActor.run { self?.finishDiagnosticSample() }
        }
    }

    func finishDiagnosticSample() {
        guard diagnostics.diagnosticRecording else { return }
        diagnostics.diagnosticRecording = false
        diagnosticTask?.cancel()
        diagnosticTask = nil
        guard !diagnosticPCM.isEmpty, diagnosticHasSignal else {
            diagnosticPCM.removeAll(keepingCapacity: true)
            let levels = diagnostics.channelLevels.map {
                L10n.text("error.audio.channel_level", Int64($0.index), $0.rmsDBFS)
            }.joined(separator: ", ")
            diagnostics.captureWarning = L10n.text(
                "error.audio.all_channels_zero",
                diagnostics.activeDeviceName,
                levels
            )
            return
        }
        do {
            let manager = FileManager.default
            let base = try manager.url(
                for: .applicationSupportDirectory,
                in: .userDomainMask,
                appropriateFor: nil,
                create: true
            )
            let directory = base.appendingPathComponent("LocalVoiceAgent/Diagnostics", isDirectory: true)
            try manager.createDirectory(at: directory, withIntermediateDirectories: true)
            let formatter = DateFormatter()
            formatter.dateFormat = "yyyyMMdd-HHmmss"
            let url = directory.appendingPathComponent("microphone-\(formatter.string(from: Date())).wav")
            try PCM16WAVEncoder.encodeMono16K(diagnosticPCM).write(to: url, options: .atomic)
            diagnostics.lastDiagnosticFile = url.path
            logger.info("Saved local microphone diagnostic WAV bytes=\(self.diagnosticPCM.count) path=\(url.path, privacy: .public)")
        } catch {
            diagnostics.captureWarning = L10n.text("error.audio.save_sample", error.localizedDescription)
        }
    }

    func playDiagnosticSample() {
        guard let path = diagnostics.lastDiagnosticFile else { return }
        do {
            diagnosticPlayer = try AVAudioPlayer(contentsOf: URL(fileURLWithPath: path))
            diagnosticPlayer?.prepareToPlay()
            diagnosticPlayer?.play()
        } catch {
            diagnostics.captureWarning = L10n.text("error.audio.play_sample", error.localizedDescription)
        }
    }

    /// Exercises the exact signed-app capture path without opening a WebSocket.
    /// The captured bytes are the same PCM16 frames normally handed to the socket.
    func runMicrophoneSmoke(seconds: Double, wavURL: URL) async -> MicrophoneSmokeReport {
        let previousHandler = onInputAudio
        var capturedPCM = Data()
        onInputAudio = { data in capturedPCM.append(data) }
        var failure: String?

        do {
            try await startCapture()
            try await Task.sleep(
                nanoseconds: UInt64(max(0.25, seconds) * 1_000_000_000)
            )
        } catch {
            failure = error.localizedDescription
        }
        if isCapturing { stopCapture() }
        onInputAudio = previousHandler

        let metrics = PCM16Diagnostics.analyze(capturedPCM)
        var wavPath: String?
        if !capturedPCM.isEmpty, metrics.peak > 0 {
            do {
                try PCM16WAVEncoder.encodeMono16K(capturedPCM).write(
                    to: wavURL,
                    options: .atomic
                )
                wavPath = wavURL.path
            } catch {
                failure = L10n.text("error.audio.save_wav", error.localizedDescription)
            }
        } else if failure == nil {
            failure = capturedPCM.isEmpty
                ? L10n.text("error.audio.capture_no_frames")
                : L10n.text("error.audio.capture_zero")
        }

        return MicrophoneSmokeReport(
            version: "0.1.6",
            passed: failure == nil && wavPath != nil,
            error: failure,
            activeDeviceName: diagnostics.activeDeviceName,
            activeDeviceUID: diagnostics.activeDeviceUID,
            sourceSampleRate: diagnostics.sourceSampleRate,
            sourceChannels: diagnostics.sourceChannels,
            sourceFormat: diagnostics.sourceFormat,
            sourceInterleaved: diagnostics.sourceInterleaved,
            inputCallbacks: diagnostics.inputCallbacks,
            convertedFrames: diagnostics.convertedFrames,
            sentFrames: diagnostics.sentFrames,
            sentBytes: diagnostics.sentBytes,
            conversionFailures: diagnostics.conversionFailures,
            selectedChannel: diagnostics.selectedChannel,
            peak: metrics.peak,
            rmsDBFS: metrics.rmsDBFS,
            zeroFraction: metrics.zeroFraction,
            pcmSamples: metrics.sampleCount,
            durationSeconds: Double(metrics.sampleCount) / 16_000,
            wavPath: wavPath,
            captureWarning: diagnostics.captureWarning,
            channelLevels: diagnostics.channelLevels.map {
                MicrophoneSmokeReport.Channel(
                    index: $0.index,
                    rmsDBFS: $0.rmsDBFS,
                    peak: $0.peak,
                    zeroFraction: $0.zeroFraction
                )
            }
        )
    }

    private func consumeCapture(
        _ packet: ConvertedCapturePacket?,
        sourceSampleTime: AVAudioFramePosition,
        sourceFrameLength: AVAudioFrameCount,
        conversionFailure: String?
    ) {
        captureCounters.recordInputCallback()
        diagnostics.inputCallbacks = captureCounters.inputCallbacks
        if let started = captureStartedAt {
            let elapsed = max(0.001, Date().timeIntervalSince(started))
            diagnostics.inputCallbacksPerSecond = Double(captureCounters.inputCallbacks) / elapsed
            diagnostics.sentFramesPerSecond = Double(captureCounters.sentFrames) / elapsed
        }
        guard let packet else {
            diagnostics.conversionFailures += 1
            let reason = conversionFailure ?? L10n.text("common.unknown")
            diagnostics.captureWarning = L10n.text("error.audio.pipeline_runtime", reason)
            logger.error("Capture conversion failed: \(reason, privacy: .public)")
            return
        }
        captureCounters.recordConvertedFrame()
        diagnostics.convertedFrames = captureCounters.convertedFrames
        diagnostics.lastFrameBytes = packet.data.count
        diagnostics.convertedSamples += packet.metrics.sampleCount
        diagnostics.rmsDBFS = packet.metrics.rmsDBFS
        diagnostics.peak = packet.metrics.peak
        diagnostics.zeroFraction = packet.metrics.zeroFraction
        diagnostics.waveform = packet.metrics.waveform
        diagnostics.channelLevels = packet.channelLevels
        diagnostics.selectedChannel = packet.selectedChannel
        if let expected = lastSourceEndSampleTime,
           sourceSampleTime >= 0,
           sourceSampleTime != expected {
            diagnostics.discontinuities += 1
        }
        if sourceSampleTime >= 0 {
            lastSourceEndSampleTime = sourceSampleTime + AVAudioFramePosition(sourceFrameLength)
        }

        if packet.allChannelsDigitalZero {
            digitalSilenceSamples += packet.metrics.sampleCount
        } else {
            digitalSilenceSamples = 0
            if zeroWarningShown {
                zeroWarningShown = false
                diagnostics.captureWarning = nil
            }
        }
        if !zeroWarningShown, digitalSilenceSamples >= 32_000 {
            zeroWarningShown = true
            diagnostics.captureWarning = L10n.text(
                "error.audio.device_zero",
                diagnostics.activeDeviceName,
                Int64(diagnostics.sourceChannels)
            )
            logger.error("All input channels produced two seconds of digital silence; waiting for explicit device/AEC choice")
        }

        if diagnostics.diagnosticRecording {
            diagnosticPCM.append(packet.data)
            diagnosticHasSignal = diagnosticHasSignal || !packet.allChannelsDigitalZero
        } else {
            onInputAudio?(packet.data)
            captureCounters.recordSentFrame(bytes: packet.data.count)
            diagnostics.sentFrames = captureCounters.sentFrames
            diagnostics.sentBytes = captureCounters.sentBytes
        }
    }

    private func microphonePermission() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return true
        case .denied, .restricted: return false
        case .notDetermined:
            return await withCheckedContinuation { continuation in
                AVCaptureDevice.requestAccess(for: .audio) { continuation.resume(returning: $0) }
            }
        @unknown default: return false
        }
    }

    private static func formatDescription(_ format: AVAudioFormat) -> String {
        "rate=\(format.sampleRate)Hz, channels=\(format.channelCount), commonFormat=\(format.commonFormat.rawValue), interleaved=\(format.isInterleaved)"
    }
}

struct MicrophoneSmokeReport: Codable {
    struct Channel: Codable {
        let index: Int
        let rmsDBFS: Double
        let peak: Double
        let zeroFraction: Double
    }

    let version: String
    let passed: Bool
    let error: String?
    let activeDeviceName: String
    let activeDeviceUID: String
    let sourceSampleRate: Double
    let sourceChannels: Int
    let sourceFormat: String
    let sourceInterleaved: Bool
    let inputCallbacks: Int
    let convertedFrames: Int
    let sentFrames: Int
    let sentBytes: Int
    let conversionFailures: Int
    let selectedChannel: Int?
    let peak: Double
    let rmsDBFS: Double
    let zeroFraction: Double
    let pcmSamples: Int
    let durationSeconds: Double
    let wavPath: String?
    let captureWarning: String?
    let channelLevels: [Channel]
}

private final class CapturePipeline: @unchecked Sendable {
    private let sourceFormat: AVAudioFormat
    private var resampler: StreamingMonoPCM16Resampler
    private var downmixer = MultiChannelDownmixer()
    private(set) var lastFailure: String?

    init?(sourceFormat: AVAudioFormat, targetFormat: AVAudioFormat) {
        guard let resampler = StreamingMonoPCM16Resampler(
            sourceRate: sourceFormat.sampleRate,
            targetRate: targetFormat.sampleRate
        ) else {
            return nil
        }
        self.sourceFormat = sourceFormat
        self.resampler = resampler
    }

    func convert(buffer: AVAudioPCMBuffer, when: AVAudioTime) -> ConvertedCapturePacket? {
        guard let channels = extractChannels(from: buffer) else {
            lastFailure = L10n.text(
                "error.audio.read_samples",
                formatDescription(buffer.format),
                Int64(buffer.frameLength),
                Int64(buffer.frameCapacity),
                Int64(buffer.audioBufferList.pointee.mNumberBuffers)
            )
            return nil
        }
        let downmix = downmixer.process(channels)
        guard !downmix.mono.isEmpty else {
            lastFailure = L10n.text("error.audio.empty_input_buffer", formatDescription(buffer.format))
            return nil
        }
        let data = resampler.process(downmix.mono)
        guard !data.isEmpty else {
            lastFailure = L10n.text(
                "error.audio.resampler_empty",
                sourceFormat.sampleRate,
                Int64(downmix.mono.count)
            )
            return nil
        }
        lastFailure = nil
        return ConvertedCapturePacket(
            data: data,
            metrics: PCM16Diagnostics.analyze(data),
            channelLevels: downmix.channelLevels,
            selectedChannel: downmix.selectedChannel,
            allChannelsDigitalZero: downmix.allChannelsDigitalZero
        )
    }

    private func extractChannels(from buffer: AVAudioPCMBuffer) -> [[Float]]? {
        let frames = Int(buffer.frameLength)
        let channelCount = Int(buffer.format.channelCount)
        guard frames > 0, channelCount > 0 else { return nil }

        if !buffer.format.isInterleaved {
            switch buffer.format.commonFormat {
            case .pcmFormatFloat32:
                guard let pointers = buffer.floatChannelData else { return nil }
                return (0..<channelCount).map {
                    Array(UnsafeBufferPointer(start: pointers[$0], count: frames))
                }
            case .pcmFormatInt16:
                guard let pointers = buffer.int16ChannelData else { return nil }
                return (0..<channelCount).map { channel in
                    UnsafeBufferPointer(start: pointers[channel], count: frames).map {
                        Float($0) / 32_768
                    }
                }
            case .pcmFormatInt32:
                guard let pointers = buffer.int32ChannelData else { return nil }
                return (0..<channelCount).map { channel in
                    UnsafeBufferPointer(start: pointers[channel], count: frames).map {
                        Float($0) / 2_147_483_648
                    }
                }
            default:
                return nil
            }
        }

        let buffers = UnsafeMutableAudioBufferListPointer(buffer.mutableAudioBufferList)
        guard buffers.count == 1, let raw = buffers[0].mData else { return nil }
        switch buffer.format.commonFormat {
        case .pcmFormatFloat32:
            let samples = raw.bindMemory(to: Float.self, capacity: frames * channelCount)
            return (0..<channelCount).map { channel in
                (0..<frames).map { samples[$0 * channelCount + channel] }
            }
        case .pcmFormatInt16:
            let samples = raw.bindMemory(to: Int16.self, capacity: frames * channelCount)
            return (0..<channelCount).map { channel in
                (0..<frames).map { Float(samples[$0 * channelCount + channel]) / 32_768 }
            }
        case .pcmFormatInt32:
            let samples = raw.bindMemory(to: Int32.self, capacity: frames * channelCount)
            return (0..<channelCount).map { channel in
                (0..<frames).map { Float(samples[$0 * channelCount + channel]) / 2_147_483_648 }
            }
        default:
            return nil
        }
    }

    private func formatDescription(_ format: AVAudioFormat) -> String {
        "rate=\(format.sampleRate), channels=\(format.channelCount), commonFormat=\(format.commonFormat.rawValue), interleaved=\(format.isInterleaved)"
    }
}

private struct ConvertedCapturePacket: @unchecked Sendable {
    let data: Data
    let metrics: PCM16ChunkMetrics
    let channelLevels: [ChannelLevel]
    let selectedChannel: Int?
    let allChannelsDigitalZero: Bool
}

private final class AudioPlaybackDelegate: NSObject, AVAudioPlayerDelegate {
    var onFinished: ((AVAudioPlayer, Bool) -> Void)?
    var onDecodeError: ((AVAudioPlayer, Error?) -> Void)?

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        onFinished?(player, flag)
    }

    func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        onDecodeError?(player, error)
    }
}

enum AudioError: LocalizedError {
    case microphoneDenied
    case noInputDevice
    case noInputDeviceOrFormat(sampleRate: Double, channels: Int)
    case pipelineInitialization(format: String, reason: String)
    case engineStart(domain: String, code: Int, message: String, format: String)

    var errorDescription: String? {
        switch self {
        case .microphoneDenied:
            return L10n.text("error.audio.no_permission")
        case .noInputDevice:
            return L10n.text("error.audio.no_device")
        case .noInputDeviceOrFormat(let sampleRate, let channels):
            return L10n.text("error.audio.no_format", sampleRate, Int64(channels))
        case .pipelineInitialization(let format, let reason):
            return L10n.text("error.audio.pipeline", format, reason)
        case .engineStart(let domain, let code, let message, let format):
            return L10n.text("error.audio.engine_start", domain, Int64(code), format, message)
        }
    }
}
