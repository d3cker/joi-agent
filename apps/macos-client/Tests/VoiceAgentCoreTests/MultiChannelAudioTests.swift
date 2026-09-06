import Foundation
import XCTest
@testable import VoiceAgentCore

final class MultiChannelAudioTests: XCTestCase {
    func testSixChannelPlanarInputSelectsNonZeroSpeechChannelAndProduces16KPCM() {
        let count = 4_800
        var channels = Array(repeating: Array(repeating: Float.zero, count: count), count: 6)
        for index in 0..<count {
            channels[4][index] = sin(Float(index) * 0.09) * 0.35
        }

        var downmixer = MultiChannelDownmixer()
        let result = downmixer.process(channels)
        XCTAssertEqual(result.selectedChannel, 4)
        XCTAssertFalse(result.allChannelsDigitalZero)
        XCTAssertEqual(result.channelLevels[0].zeroFraction, 1)
        XCTAssertGreaterThan(result.channelLevels[4].peak, 0.3)

        let pcm = MonoPCM16Resampler.linear(result.mono, from: 48_000)
        XCTAssertEqual(pcm.count, 1_600 * 2)
        XCTAssertGreaterThan(PCM16Diagnostics.analyze(pcm).peak, 0.3)
        let wav = PCM16WAVEncoder.encodeMono16K(pcm)
        XCTAssertEqual(String(data: wav.prefix(4), encoding: .ascii), "RIFF")
        XCTAssertEqual(wav.count, 44 + pcm.count)
    }

    func testAllZeroChannelsRemainZeroAndHaveNoSelectedChannel() {
        var downmixer = MultiChannelDownmixer()
        let result = downmixer.process(
            Array(repeating: Array(repeating: Float.zero, count: 1_024), count: 6)
        )
        XCTAssertTrue(result.allChannelsDigitalZero)
        XCTAssertNil(result.selectedChannel)
        XCTAssertTrue(result.mono.allSatisfy { $0 == 0 })
        XCTAssertTrue(result.channelLevels.allSatisfy { $0.zeroFraction == 1 })
    }

    func testOppositePhaseChannelsCannotCancelBecauseOneChannelIsSelected() {
        let positive = (0..<1_024).map { sin(Float($0) * 0.1) * 0.5 }
        let negative = positive.map(-)
        var downmixer = MultiChannelDownmixer()
        let result = downmixer.process([positive, negative])

        XCTAssertNotNil(result.selectedChannel)
        XCTAssertGreaterThan(result.mono.map { abs($0) }.max() ?? 0, 0.49)
        XCTAssertNotEqual(result.mono, Array(repeating: 0, count: result.mono.count))
    }

    func testCaptureCountersSeparateCallbacksConversionsAndNetworkFrames() {
        var counters = AudioCaptureCounters()
        counters.recordInputCallback()
        counters.recordInputCallback()
        counters.recordConvertedFrame()
        counters.recordSentFrame(bytes: 640)

        XCTAssertEqual(counters.inputCallbacks, 2)
        XCTAssertEqual(counters.convertedFrames, 1)
        XCTAssertEqual(counters.sentFrames, 1)
        XCTAssertEqual(counters.sentBytes, 640)
    }

    func testRealFormat48KSixChannel1024FrameCallbacksAreContinuousForFiveSeconds() throws {
        var downmixer = MultiChannelDownmixer()
        var resampler = try XCTUnwrap(
            StreamingMonoPCM16Resampler(sourceRate: 48_000, targetRate: 16_000)
        )
        var counters = AudioCaptureCounters()
        var pcm = Data()
        let totalSourceFrames = 48_000 * 5
        var sourceOffset = 0

        while sourceOffset < totalSourceFrames {
            let frameCount = min(1_024, totalSourceFrames - sourceOffset)
            var channels = Array(
                repeating: Array(repeating: Float.zero, count: frameCount),
                count: 6
            )
            for frame in 0..<frameCount {
                let phase = Float(sourceOffset + frame) * 2 * .pi * 440 / 48_000
                channels[4][frame] = sin(phase) * 0.25
            }
            counters.recordInputCallback()
            let selected = downmixer.process(channels)
            XCTAssertEqual(selected.selectedChannel, 4)
            let chunk = resampler.process(selected.mono)
            XCTAssertFalse(chunk.isEmpty)
            counters.recordConvertedFrame()
            counters.recordSentFrame(bytes: chunk.count)
            pcm.append(chunk)
            sourceOffset += frameCount
        }

        XCTAssertEqual(counters.inputCallbacks, 235)
        XCTAssertEqual(counters.convertedFrames, 235)
        XCTAssertEqual(counters.sentFrames, 235)
        XCTAssertEqual(pcm.count, 16_000 * 5 * 2)
        XCTAssertEqual(counters.sentBytes, pcm.count)
        XCTAssertGreaterThan(PCM16Diagnostics.analyze(pcm).peak, 0.2)

        let wav = PCM16WAVEncoder.encodeMono16K(pcm)
        XCTAssertEqual(wav.count, 44 + 16_000 * 5 * 2)
    }
}
