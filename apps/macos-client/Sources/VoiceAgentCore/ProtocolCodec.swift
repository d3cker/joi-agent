import Foundation

public enum ProtocolCodecError: Error, Equatable {
    case invalidObject
    case missingType
    case invalidBase64Audio
}

public enum ProtocolCodec {
    public static func decode(text: String) throws -> ServerEvent {
        guard let data = text.data(using: .utf8),
              let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw ProtocolCodecError.invalidObject
        }
        guard let type = object["type"] as? String else {
            throw ProtocolCodecError.missingType
        }

        let textValue = (object["text"] as? String)
            ?? (object["delta"] as? String)
            ?? ((object["transcript"] as? [String: Any])?["text"] as? String)
        switch type {
        case "session.ready":
            return .sessionReady(
                sessionID: object["session_id"] as? String,
                resumed: (object["resumed"] as? Bool) ?? false
            )
        case "session.created":
            return .ready
        case "session.history":
            let messages = (object["messages"] as? [[String: Any]] ?? []).compactMap {
                item -> SessionHistoryItem? in
                guard let role = item["role"] as? String,
                      let text = item["text"] as? String else { return nil }
                return SessionHistoryItem(role: role, text: text)
            }
            return .sessionHistory(messages)
        case "session.state", "state":
            return .state((object["state"] as? String) ?? "unknown")
        case "transcript.partial", "input_audio_transcription.delta":
            return .transcriptPartial(textValue ?? "")
        case "transcript.final", "input_audio_transcription.completed":
            return .transcriptFinal(textValue ?? "")
        case "assistant.reasoning.delta", "response.reasoning.delta":
            return .reasoningDelta(textValue ?? "")
        case "assistant.delta", "response.output_text.delta", "response.text.delta":
            return .assistantDelta(textValue ?? "")
        case "assistant.done", "response.output_text.done", "response.done":
            return .assistantDone(textValue)
        case "audio.start", "response.audio.start":
            return .audioStart(
                responseID: responseID(object),
                sampleRate: number(object["sample_rate"]),
                channels: number(object["channels"]).map(Int.init),
                encoding: (object["encoding"] as? String) ?? (object["format"] as? String)
            )
        case "audio.chunk", "response.audio.delta":
            let responseID = responseID(object)
            let index = number(object["index"]).map(Int.init) ?? 0
            let mimeType = object["mime_type"] as? String
            let sampleRate = number(object["sample_rate"])
            let segmentText = object["text"] as? String
            if object["encoding"] as? String == "binary-next-frame" {
                guard let byteLength = number(object["byte_length"]).map(Int.init), byteLength >= 0 else {
                    throw ProtocolCodecError.invalidObject
                }
                return .audioChunkDescriptor(AudioChunkDescriptor(
                    responseID: responseID,
                    index: index,
                    byteLength: byteLength,
                    mimeType: mimeType,
                    sampleRate: sampleRate,
                    text: segmentText
                ))
            }
            guard let encoded = (object["audio"] as? String) ?? (object["data"] as? String),
                  let audio = Data(base64Encoded: encoded) else {
                throw ProtocolCodecError.invalidBase64Audio
            }
            let descriptor = AudioChunkDescriptor(
                responseID: responseID,
                index: index,
                byteLength: audio.count,
                mimeType: mimeType,
                sampleRate: sampleRate,
                text: segmentText
            )
            return .audioSegment(AudioSegment(descriptor: descriptor, data: audio))
        case "audio.end", "response.audio.done":
            return .audioEnd(
                responseID: responseID(object),
                cancelled: (object["cancelled"] as? Bool) ?? (object["canceled"] as? Bool) ?? false
            )
        case "response.cancelled", "response.canceled":
            return .responseCancelled(responseID: object["response_id"] as? String)
        case "input_audio.speech_started", "speech.started", "speech_started":
            return .speechStarted(
                bargeIn: (object["barge_in"] as? Bool) ?? false,
                probability: number(object["probability"])
            )
        case "input_audio.speech_stopped", "speech.stopped", "speech_stopped":
            return .speechStopped(
                silenceMS: number(object["silence_ms"]).map(Int.init),
                audioMS: number(object["audio_ms"]).map(Int.init)
            )
        case "input_audio.committed", "input_audio.commit.completed", "committed":
            return .inputCommitted(
                reason: object["reason"] as? String,
                audioMS: number(object["audio_ms"]).map(Int.init)
            )
        case "input_audio.rejected":
            return .inputRejected(
                reason: (object["reason"] as? String) ?? "unknown",
                message: (object["message"] as? String) ?? L10n.text("error.audio.no_speech")
            )
        case "input_audio.vad":
            var patch = BackendDiagnosticsPatch()
            patch.vadProbability = number(object["probability"])
            patch.rmsDBFS = number(object["rms_dbfs"])
            patch.peak = number(object["peak"])
            patch.zeroFraction = number(object["zero_fraction"])
            patch.vadState = object["state"] as? String
            patch.speechMS = number(object["speech_ms"]).map(Int.init)
            patch.silenceMS = number(object["silence_ms"]).map(Int.init)
            patch.bufferedMS = number(object["buffered_ms"]).map(Int.init)
            patch.processedFrames = number(object["processed_frames"]).map(Int.init)
            patch.receivedMessages = number(object["received_messages"]).map(Int.init)
            patch.receivedBytes = number(object["received_bytes"]).map(Int.init)
            patch.receivedBytesPerSecond = number(object["received_bytes_per_second"])
            patch.lastMessageBytes = number(object["last_message_bytes"]).map(Int.init)
            return .diagnostics(patch)
        case "transcript.metrics":
            var patch = BackendDiagnosticsPatch()
            patch.transcript = textValue ?? ""
            patch.transcriptAccepted = object["accepted"] as? Bool
            patch.confidence = number(object["confidence"])
            patch.noSpeechProbability = number(object["no_speech_probability"])
            patch.averageLogProbability = number(object["average_log_probability"])
            patch.compressionRatio = number(object["compression_ratio"])
            patch.sttMS = number(object["stt_ms"]).map(Int.init)
            patch.endpointToSTTMS = number(object["endpoint_to_stt_ms"]).map(Int.init)
            patch.lastRejection = object["rejection_reason"] as? String
            return .diagnostics(patch)
        case "response.metrics":
            var patch = BackendDiagnosticsPatch()
            patch.llmTTFTMS = number(object["llm_ttft_ms"]).map(Int.init)
            patch.endpointToFirstTokenMS = number(object["endpoint_to_first_token_ms"]).map(Int.init)
            patch.ttsMS = number(object["tts_ms"]).map(Int.init)
            patch.endpointToFirstAudioMS = number(object["endpoint_to_first_audio_ms"]).map(Int.init)
            patch.firstTokenToFirstAudioMS = number(object["first_token_to_first_audio_ms"]).map(Int.init)
            patch.tokensPerSecond = number(object["tokens_per_second"])
            patch.tokensEstimated = object["tokens_estimated"] as? Bool
            patch.estimatedTokens = number(object["estimated_tokens"]).map(Int.init)
            patch.inputTokens = number(object["input_tokens"]).map(Int.init)
            patch.inputTokensEstimated = object["input_tokens_estimated"] as? Bool
            patch.outputTokens = number(object["output_tokens"]).map(Int.init)
            patch.contextSize = number(object["context_size"]).map(Int.init)
            patch.promptProcessingTokensPerSecond = number(
                object["prompt_processing_tokens_per_second"]
            )
            patch.promptProcessingEstimated = object["prompt_processing_estimated"] as? Bool
            return .diagnostics(patch)
        case "context.metrics":
            var patch = BackendDiagnosticsPatch()
            patch.inputTokens = number(object["input_tokens"]).map(Int.init)
            patch.inputTokensEstimated = object["input_tokens_estimated"] as? Bool
            patch.outputTokens = number(object["output_tokens"]).map(Int.init)
            patch.contextSize = number(object["context_size"]).map(Int.init)
            patch.contextUsedTokens = number(object["context_used_tokens"]).map(Int.init)
            patch.contextRemainingTokens = number(
                object["context_remaining_tokens"]
            ).map(Int.init)
            patch.promptProcessingTokensPerSecond = number(
                object["prompt_processing_tokens_per_second"]
            )
            patch.promptProcessingEstimated = object["prompt_processing_estimated"] as? Bool
            patch.contextBreakdownEstimated = object["context_breakdown_estimated"] as? Bool
            let categories = object["categories"] as? [String: Any] ?? [:]
            patch.systemPromptTokens = number(categories["system_prompt"]).map(Int.init)
            patch.skillsTokens = number(categories["skills"]).map(Int.init)
            patch.toolsTokens = number(categories["tools"]).map(Int.init)
            patch.sessionTokens = number(categories["session"]).map(Int.init)
            return .diagnostics(patch)
        case "tool.call.started":
            return .toolStarted(
                name: (object["name"] as? String) ?? "tool",
                callID: object["tool_call_id"] as? String,
                arguments: toolArguments(object["arguments"])
            )
        case "tool.call.completed":
            return .toolCompleted(
                name: (object["name"] as? String) ?? "tool",
                callID: object["tool_call_id"] as? String
            )
        case "tool.call.failed":
            return .toolFailed(
                name: (object["name"] as? String) ?? "tool",
                callID: object["tool_call_id"] as? String,
                message: object["content"] as? String
            )
        case "error":
            let message = (object["message"] as? String)
                ?? ((object["error"] as? [String: Any])?["message"] as? String)
                ?? L10n.text("error.session.unknown")
            return .error(message)
        default:
            return .ignored(type)
        }
    }

    private static func number(_ value: Any?) -> Double? {
        if let value = value as? Double { return value }
        if let value = value as? Int { return Double(value) }
        if let value = value as? String { return Double(value) }
        return nil
    }

    private static func toolArguments(_ value: Any?) -> String {
        if let value = value as? String, !value.isEmpty { return value }
        if let value, JSONSerialization.isValidJSONObject(value),
           let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
           let encoded = String(data: data, encoding: .utf8) {
            return encoded
        }
        return "{}"
    }

    private static func responseID(_ object: [String: Any]) -> String {
        (object["response_id"] as? String) ?? "legacy"
    }
}
