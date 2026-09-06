import Combine
import Foundation
import OSLog
import VoiceAgentCore

@MainActor
final class WebSocketClient: NSObject, ObservableObject, URLSessionWebSocketDelegate {
    enum Status: Equatable {
        case disconnected
        case connecting
        case connected
        case failed(String)
    }

    @Published private(set) var status: Status = .disconnected
    var onEvent: ((ServerEvent) -> Void)?

    private let logger = Logger(subsystem: "local.voice-agent", category: "websocket")
    private var session: URLSession?
    private var task: URLSessionWebSocketTask?
    private var intentionallyClosed = false
    private var audioAssembler = AudioBinaryAssembler()

    func connect(to url: URL, clientAccessKey: String) {
        disconnect()
        intentionallyClosed = false
        status = .connecting
        audioAssembler.reset()

        let session = URLSession(configuration: .default, delegate: self, delegateQueue: nil)
        let request = TransportSecurityPolicy.authorizedRequest(
            url: url, clientAccessKey: clientAccessKey
        )
        let task = session.webSocketTask(with: request)
        self.session = session
        self.task = task
        logger.info("Connecting to \(url.absoluteString, privacy: .public)")
        task.resume()
        receiveNext()
    }

    func send(_ command: ClientCommand) {
        guard let text = try? String(data: command.encoded(), encoding: .utf8) else { return }
        logger.debug("Sending control message: \(text, privacy: .public)")
        send(.string(text))
    }

    func send(audio data: Data) {
        send(.data(data))
    }

    func disconnect() {
        intentionallyClosed = true
        if task != nil { logger.info("Closing WebSocket intentionally") }
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        session?.invalidateAndCancel()
        session = nil
        audioAssembler.reset()
        status = .disconnected
    }

    private func send(_ message: URLSessionWebSocketTask.Message) {
        guard let task else { return }
        task.send(message) { [weak self] error in
            guard let error else { return }
            Task { @MainActor in self?.handleFailure(error) }
        }
    }

    private func receiveNext() {
        task?.receive { [weak self] result in
            Task { @MainActor in
                guard let self else { return }
                switch result {
                case .success(.string(let text)):
                    self.handleText(text)
                    self.receiveNext()
                case .success(.data(let data)):
                    self.handleBinary(data)
                    self.receiveNext()
                case .failure(let error):
                    self.handleFailure(error)
                @unknown default:
                    self.onEvent?(.error(L10n.text("error.websocket.unsupported_message")))
                    self.receiveNext()
                }
            }
        }
    }

    private func handleText(_ text: String) {
        do {
            let event = try ProtocolCodec.decode(text: text)
            if case .audioChunkDescriptor(let descriptor) = event {
                try audioAssembler.accept(descriptor: descriptor)
                logger.info(
                    "Received descriptor response=\(descriptor.responseID, privacy: .public) segment=\(descriptor.index) bytes=\(descriptor.byteLength)"
                )
                return
            }
            switch event {
            case .audioEnd(let responseID, _):
                if let pending = audioAssembler.pendingDescriptor {
                    protocolFailure(
                        L10n.text(
                            "error.websocket.missing_binary",
                            pending.responseID,
                            Int64(pending.index)
                        )
                    )
                    return
                }
                audioAssembler.reset(responseID: responseID)
            case .responseCancelled(let responseID):
                if let responseID { audioAssembler.reset(responseID: responseID) }
                else { audioAssembler.reset() }
            default:
                break
            }
            logger.debug("Received event: \(text, privacy: .public)")
            onEvent?(event)
        } catch {
            protocolFailure(L10n.text("error.websocket.invalid_message", error.localizedDescription))
        }
    }

    private func handleBinary(_ data: Data) {
        do {
            let segment = try audioAssembler.accept(binary: data)
            logger.info(
                "Received binary response=\(segment.descriptor.responseID, privacy: .public) segment=\(segment.descriptor.index) bytes=\(data.count)"
            )
            onEvent?(.audioSegment(segment))
        } catch AudioBinaryAssemblyError.byteLengthMismatch(let expected, let actual) {
            protocolFailure(L10n.text(
                "error.websocket.invalid_wav_size", Int64(expected), Int64(actual)
            ))
        } catch {
            protocolFailure(L10n.text(
                "error.websocket.binary_descriptor", String(describing: error)
            ))
        }
    }

    private func protocolFailure(_ message: String) {
        logger.error("Protocol error: \(message, privacy: .public)")
        onEvent?(.error(message))
    }

    private func handleFailure(_ error: Error) {
        guard !intentionallyClosed else { return }
        let message = error.localizedDescription
        logger.error("WebSocket failed: \(message, privacy: .public)")
        status = .failed(message)
        onEvent?(.error(L10n.text("error.websocket.interrupted", message)))
    }

    nonisolated func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didOpenWithProtocol protocol: String?
    ) {
        Task { @MainActor [weak self] in
            guard let self, self.task === webSocketTask else { return }
            self.status = .connected
            self.logger.info("WebSocket opened protocol=\(`protocol` ?? "none", privacy: .public)")
        }
    }

    nonisolated func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didCloseWith closeCode: URLSessionWebSocketTask.CloseCode,
        reason: Data?
    ) {
        let reasonText = reason.flatMap { String(data: $0, encoding: .utf8) } ?? "none"
        Task { @MainActor [weak self] in
            guard let self else { return }
            self.audioAssembler.reset()
            self.logger.info(
                "WebSocket closed code=\(closeCode.rawValue) reason=\(reasonText, privacy: .public) intentional=\(self.intentionallyClosed)"
            )
            if self.intentionallyClosed {
                self.status = .disconnected
            } else {
                let message = L10n.text(
                    "error.websocket.closed_detail", Int64(closeCode.rawValue), reasonText
                )
                self.status = .failed(message)
                self.onEvent?(.error(L10n.text("error.websocket.closed", message)))
            }
        }
    }
}
