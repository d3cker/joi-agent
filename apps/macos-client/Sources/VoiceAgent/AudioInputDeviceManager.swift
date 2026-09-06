import CoreAudio
import Foundation
import VoiceAgentCore

struct AudioInputDevice: Identifiable, Equatable, Sendable {
    let deviceID: AudioDeviceID
    let uid: String
    let name: String
    let isSystemDefault: Bool

    var id: String { uid }
}

enum AudioInputDeviceManager {
    static func availableDevices() throws -> [AudioInputDevice] {
        let defaultID = defaultInputDevice()
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDevices,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var byteCount: UInt32 = 0
        let sizeStatus = AudioObjectGetPropertyDataSize(
            AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &byteCount
        )
        guard sizeStatus == noErr else {
            throw AudioInputDeviceError.discoveryFailed(sizeStatus)
        }
        guard byteCount > 0 else { return [] }

        var ids = [AudioDeviceID](
            repeating: kAudioObjectUnknown,
            count: Int(byteCount) / MemoryLayout<AudioDeviceID>.size
        )
        let status = ids.withUnsafeMutableBytes { bytes in
            AudioObjectGetPropertyData(
                AudioObjectID(kAudioObjectSystemObject),
                &address,
                0,
                nil,
                &byteCount,
                bytes.baseAddress!
            )
        }
        guard status == noErr else { throw AudioInputDeviceError.discoveryFailed(status) }

        return ids.compactMap { id in
            guard inputStreamCount(deviceID: id) > 0,
                  let uid = stringProperty(
                    object: AudioObjectID(id),
                    selector: kAudioDevicePropertyDeviceUID
                  ),
                  let name = stringProperty(
                    object: AudioObjectID(id),
                    selector: kAudioObjectPropertyName
                  ) else { return nil }
            return AudioInputDevice(
                deviceID: id,
                uid: uid,
                name: name,
                isSystemDefault: id == defaultID
            )
        }
        .sorted {
            if $0.isSystemDefault != $1.isSystemDefault { return $0.isSystemDefault }
            return $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
        }
    }

    static func systemDefaultDevice(in devices: [AudioInputDevice]) -> AudioInputDevice? {
        devices.first(where: \.isSystemDefault)
    }

    private static func inputStreamCount(deviceID: AudioDeviceID) -> Int {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyStreams,
            mScope: kAudioDevicePropertyScopeInput,
            mElement: kAudioObjectPropertyElementMain
        )
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(
            AudioObjectID(deviceID), &address, 0, nil, &size
        ) == noErr else { return 0 }
        return Int(size) / MemoryLayout<AudioStreamID>.size
    }

    private static func defaultInputDevice() -> AudioDeviceID? {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDefaultInputDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var value = AudioDeviceID(kAudioObjectUnknown)
        var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        guard AudioObjectGetPropertyData(
            AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &value
        ) == noErr else { return nil }
        return value
    }

    private static func stringProperty(
        object: AudioObjectID,
        selector: AudioObjectPropertySelector
    ) -> String? {
        var address = AudioObjectPropertyAddress(
            mSelector: selector,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var value: CFString = "" as CFString
        var size = UInt32(MemoryLayout<CFString>.size)
        guard AudioObjectGetPropertyData(
            object, &address, 0, nil, &size, &value
        ) == noErr else { return nil }
        return value as String
    }
}

enum AudioInputDeviceError: LocalizedError {
    case discoveryFailed(OSStatus)

    var errorDescription: String? {
        switch self {
        case .discoveryFailed(let status):
            return L10n.text("error.audio.coreaudio_devices", Int64(status))
        }
    }
}
