import Foundation

public struct ClientAudioDiagnostics: Equatable, Sendable {
    public var activeDeviceName = L10n.text("common.unknown")
    public var activeDeviceUID = ""
    public var activeDeviceIsDefault = false
    public var availableInputDevices = 0
    public var sourceSampleRate: Double = 0
    public var sourceChannels: Int = 0
    public var sourceFormat = L10n.text("common.unknown")
    public var sourceInterleaved = false
    public var voiceProcessingEnabled = false
    public var voiceProcessingMuted = false
    public var inputCallbacks = 0
    public var convertedFrames = 0
    public var sentFrames = 0
    public var sentBytes = 0
    public var inputCallbacksPerSecond: Double = 0
    public var sentFramesPerSecond: Double = 0
    public var rmsDBFS: Double = -96
    public var peak: Double = 0
    public var zeroFraction: Double = 1
    public var waveform: [Float] = []
    public var convertedSamples = 0
    public var discontinuities = 0
    public var lastFrameBytes = 0
    public var conversionFailures = 0
    public var channelLevels: [ChannelLevel] = []
    public var selectedChannel: Int?
    public var captureWarning: String?
    public var diagnosticRecording = false
    public var lastDiagnosticFile: String?

    public init() {}
}

public struct ChannelLevel: Equatable, Sendable {
    public let index: Int
    public let rmsDBFS: Double
    public let peak: Double
    public let zeroFraction: Double

    public init(index: Int, rmsDBFS: Double, peak: Double, zeroFraction: Double) {
        self.index = index
        self.rmsDBFS = rmsDBFS
        self.peak = peak
        self.zeroFraction = zeroFraction
    }
}

public struct DownmixResult: Equatable, Sendable {
    public let mono: [Float]
    public let channelLevels: [ChannelLevel]
    public let selectedChannel: Int?
    public let allChannelsDigitalZero: Bool
}

/// Selects one stable, energetic input channel instead of summing microphone,
/// loopback and aggregate-device channels which may cancel in opposite phase.
/// It never normalizes the chosen signal, so pure noise is not amplified.
public struct MultiChannelDownmixer: Sendable {
    private var smoothedEnergy: [Double] = []
    private var selectedChannel: Int?
    private var pendingChannel: Int?
    private var pendingWins = 0

    public init() {}

    public mutating func process(_ channels: [[Float]]) -> DownmixResult {
        guard let frameCount = channels.map(\.count).min(), frameCount > 0 else {
            return DownmixResult(
                mono: [], channelLevels: [], selectedChannel: nil,
                allChannelsDigitalZero: true
            )
        }
        if smoothedEnergy.count != channels.count {
            smoothedEnergy = Array(repeating: 0, count: channels.count)
            selectedChannel = nil
            pendingChannel = nil
            pendingWins = 0
        }

        var levels: [ChannelLevel] = []
        var energies: [Double] = []
        levels.reserveCapacity(channels.count)
        energies.reserveCapacity(channels.count)
        for (index, channel) in channels.enumerated() {
            var squareSum = 0.0
            var peak = 0.0
            var zeroCount = 0
            for sample in channel.prefix(frameCount) {
                let value = Double(sample)
                squareSum += value * value
                peak = max(peak, abs(value))
                if sample == 0 { zeroCount += 1 }
            }
            let energy = squareSum / Double(frameCount)
            let rms = sqrt(energy)
            let db = max(-96, 20 * log10(max(rms, pow(10, -96.0 / 20.0))))
            levels.append(ChannelLevel(
                index: index,
                rmsDBFS: db,
                peak: peak,
                zeroFraction: Double(zeroCount) / Double(frameCount)
            ))
            energies.append(energy)
            smoothedEnergy[index] = smoothedEnergy[index] * 0.75 + energy * 0.25
        }

        let allZero = levels.allSatisfy { $0.peak == 0 }
        let best = smoothedEnergy.indices.max { smoothedEnergy[$0] < smoothedEnergy[$1] }
        if allZero {
            pendingChannel = nil
            pendingWins = 0
        } else if selectedChannel == nil {
            selectedChannel = best
        } else if let best, let current = selectedChannel, best != current {
            let currentEnergy = smoothedEnergy[current]
            let decisivelyStronger = currentEnergy == 0 || smoothedEnergy[best] > currentEnergy * 1.5
            if decisivelyStronger {
                if pendingChannel == best { pendingWins += 1 }
                else { pendingChannel = best; pendingWins = 1 }
                if pendingWins >= 3 || currentEnergy == 0 {
                    selectedChannel = best
                    pendingChannel = nil
                    pendingWins = 0
                }
            } else {
                pendingChannel = nil
                pendingWins = 0
            }
        }

        let mono: [Float]
        if allZero || selectedChannel == nil {
            mono = Array(repeating: 0, count: frameCount)
        } else {
            mono = Array(channels[selectedChannel!].prefix(frameCount))
        }
        return DownmixResult(
            mono: mono,
            channelLevels: levels,
            selectedChannel: selectedChannel,
            allChannelsDigitalZero: allZero
        )
    }
}

public enum MonoPCM16Resampler {
    /// One-shot convenience wrapper around the same stateful converter used by
    /// microphone capture.
    public static func linear(_ input: [Float], from sourceRate: Double, to targetRate: Double = 16_000) -> Data {
        guard var converter = StreamingMonoPCM16Resampler(
            sourceRate: sourceRate,
            targetRate: targetRate
        ) else { return Data() }
        return converter.process(input)
    }
}

/// Stateful, allocation-bounded resampler for the selected mono channel.
/// Common integer ratios (especially 48 kHz → 16 kHz) consume exact groups
/// across callback boundaries. Other rates use continuous linear interpolation.
public struct StreamingMonoPCM16Resampler: Sendable {
    public let sourceRate: Double
    public let targetRate: Double
    private let integerRatio: Int?
    private var pending: [Float] = []
    private var position = 0.0

    public init?(sourceRate: Double, targetRate: Double = 16_000) {
        guard sourceRate > 0, targetRate > 0 else { return nil }
        self.sourceRate = sourceRate
        self.targetRate = targetRate
        let ratio = sourceRate / targetRate
        let rounded = ratio.rounded()
        integerRatio = rounded >= 1 && abs(ratio - rounded) < 0.000_001
            ? Int(rounded) : nil
    }

    public mutating func process(_ input: [Float]) -> Data {
        guard !input.isEmpty else { return Data() }
        pending.append(contentsOf: input)
        if let integerRatio { return processIntegerRatio(integerRatio) }
        return processLinear()
    }

    private mutating func processIntegerRatio(_ ratio: Int) -> Data {
        let outputCount = pending.count / ratio
        guard outputCount > 0 else { return Data() }
        var output = Data(capacity: outputCount * 2)
        var offset = 0
        for _ in 0..<outputCount {
            var sum: Float = 0
            for index in 0..<ratio { sum += pending[offset + index] }
            appendPCM16(sum / Float(ratio), to: &output)
            offset += ratio
        }
        pending.removeFirst(offset)
        return output
    }

    private mutating func processLinear() -> Data {
        let step = sourceRate / targetRate
        var output = Data()
        while position + 1 < Double(pending.count) {
            let lower = Int(position)
            let fraction = Float(position - Double(lower))
            let sample = pending[lower] * (1 - fraction) + pending[lower + 1] * fraction
            appendPCM16(sample, to: &output)
            position += step
        }
        let consumed = min(pending.count, Int(position))
        if consumed > 0 {
            pending.removeFirst(consumed)
            position -= Double(consumed)
        }
        return output
    }

    private func appendPCM16(_ sample: Float, to data: inout Data) {
        let value: Int16
        if !sample.isFinite {
            value = 0
        } else if sample >= 1 {
            value = Int16.max
        } else if sample <= -1 {
            value = Int16.min
        } else {
            value = Int16((sample * Float(Int16.max)).rounded())
        }
        var littleEndian = value.littleEndian
        Swift.withUnsafeBytes(of: &littleEndian) { data.append(contentsOf: $0) }
    }
}

public struct AudioCaptureCounters: Equatable, Sendable {
    public private(set) var inputCallbacks = 0
    public private(set) var convertedFrames = 0
    public private(set) var sentFrames = 0
    public private(set) var sentBytes = 0

    public init() {}
    public mutating func recordInputCallback() { inputCallbacks += 1 }
    public mutating func recordConvertedFrame() { convertedFrames += 1 }
    public mutating func recordSentFrame(bytes: Int) {
        sentFrames += 1
        sentBytes += max(0, bytes)
    }
}

public struct PCM16ChunkMetrics: Equatable, Sendable {
    public let sampleCount: Int
    public let rmsDBFS: Double
    public let peak: Double
    public let zeroFraction: Double
    public let waveform: [Float]

    public init(
        sampleCount: Int,
        rmsDBFS: Double,
        peak: Double,
        zeroFraction: Double,
        waveform: [Float]
    ) {
        self.sampleCount = sampleCount
        self.rmsDBFS = rmsDBFS
        self.peak = peak
        self.zeroFraction = zeroFraction
        self.waveform = waveform
    }
}

public enum PCM16Diagnostics {
    public static func analyze(_ data: Data, waveformPoints: Int = 48) -> PCM16ChunkMetrics {
        guard data.count >= 2 else {
            return PCM16ChunkMetrics(
                sampleCount: 0,
                rmsDBFS: -96,
                peak: 0,
                zeroFraction: 1,
                waveform: []
            )
        }
        let sampleCount = data.count / 2
        var squareSum = 0.0
        var peak = 0.0
        var zeroCount = 0
        var samples = [Float]()
        samples.reserveCapacity(sampleCount)
        data.withUnsafeBytes { raw in
            for index in 0..<sampleCount {
                let low = UInt16(raw[index * 2])
                let high = UInt16(raw[index * 2 + 1]) << 8
                let signed = Int16(bitPattern: low | high)
                let normalized = Double(signed) / 32_768.0
                squareSum += normalized * normalized
                peak = max(peak, abs(normalized))
                if signed == 0 { zeroCount += 1 }
                samples.append(Float(normalized))
            }
        }
        let rms = sqrt(squareSum / Double(sampleCount))
        let rmsDBFS = max(-96, 20 * log10(max(rms, pow(10, -96.0 / 20.0))))
        let requestedPoints = max(1, waveformPoints)
        let stride = max(1, sampleCount / requestedPoints)
        var waveform = [Float]()
        waveform.reserveCapacity(requestedPoints)
        var start = 0
        while start < sampleCount, waveform.count < requestedPoints {
            let end = min(sampleCount, start + stride)
            let value = samples[start..<end].max(by: { abs($0) < abs($1) }) ?? 0
            waveform.append(value)
            start = end
        }
        return PCM16ChunkMetrics(
            sampleCount: sampleCount,
            rmsDBFS: rmsDBFS,
            peak: peak,
            zeroFraction: Double(zeroCount) / Double(sampleCount),
            waveform: waveform
        )
    }
}

public enum PCM16WAVEncoder {
    public static func encodeMono16K(_ pcm: Data) -> Data {
        var result = Data()
        result.append(contentsOf: Array("RIFF".utf8))
        append(UInt32(36 + pcm.count), to: &result)
        result.append(contentsOf: Array("WAVEfmt ".utf8))
        append(UInt32(16), to: &result)
        append(UInt16(1), to: &result)
        append(UInt16(1), to: &result)
        append(UInt32(16_000), to: &result)
        append(UInt32(32_000), to: &result)
        append(UInt16(2), to: &result)
        append(UInt16(16), to: &result)
        result.append(contentsOf: Array("data".utf8))
        append(UInt32(pcm.count), to: &result)
        result.append(pcm)
        return result
    }

    private static func append<T: FixedWidthInteger>(_ value: T, to data: inout Data) {
        var littleEndian = value.littleEndian
        Swift.withUnsafeBytes(of: &littleEndian) { data.append(contentsOf: $0) }
    }
}
