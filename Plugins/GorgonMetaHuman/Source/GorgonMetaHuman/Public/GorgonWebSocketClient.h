// GorgonWebSocketClient.h — WebSocket transport for FaceContract frames.
//
// Binary messages = FaceContract v1.4.1 frames, one per message (the
// gorgon_ws_bridge.py data plane). Text messages = JSON control events —
// a BRIDGE EXTENSION, not part of the locked wire contract:
//   {"type": "trigger_reaction", "reaction": "..."}
//
// Deliberately NOT a LiveKit client: the LiveKit signal socket speaks the
// LiveKit RTC protocol (protobuf), it is not a raw binary pipe. LiveKit
// stays the audio/video plane; this client consumes the plain RFC-6455
// bridge directly (no SDK dependency).
#pragma once

#include "CoreMinimal.h"
#include "UObject/Object.h"
#include "IWebSocket.h"
#include "FaceContractDecoder.h"
#include "GorgonWebSocketClient.generated.h"

UCLASS(BlueprintType, Category="Gorgon")
class GORGONMETAHUMAN_API UGorgonWebSocketClient : public UObject
{
    GENERATED_BODY()

public:
    /** Connect (RFC-6455). Url example: ws://127.0.0.1:8765 (the bridge).
     *  BearerToken is optional — the local bridge needs none. */
    UFUNCTION(BlueprintCallable, Category="Gorgon")
    void Connect(const FString& Url, const FString& BearerToken = TEXT(""));

    UFUNCTION(BlueprintCallable, Category="Gorgon")
    void Disconnect();

    UFUNCTION(BlueprintPure, Category="Gorgon")
    bool IsConnected() const { return bConnected; }

    /** Broadcast per decoded binary message (one FaceContract chunk). */
    UPROPERTY(BlueprintAssignable, Category="Gorgon")
    FOnFrameReceived OnFrameReceived;

    /** JSON control events from the bridge (extension, non-wire). */
    UPROPERTY(BlueprintAssignable, Category="Gorgon")
    FOnTriggerReceived OnTriggerReceived;

    UPROPERTY(BlueprintAssignable, Category="Gorgon")
    FOnGorgonConnectionStateChanged OnConnectionStateChanged;

    virtual void BeginDestroy() override;

private:
    TSharedPtr<IWebSocket> WebSocket;
    bool bConnected = false;

    /** Partial-binary assembly: UE may deliver fragmented binary messages
     *  (bIsLast=false) — accumulate until the frame is complete. */
    TArray<uint8> BinaryBuffer;

    void HandleConnected();
    void HandleConnectionError(const FString& Error);
    void HandleClosed(int32 StatusCode, const FString& Reason, bool bWasClean);
    void HandleTextMessage(const FString& Message);
    void HandleBinaryMessage(const void* Data, int64 Size, bool bIsLast);
    void SetConnected(bool bNewState, const FString& Reason);
    void ProcessTrigger(const FString& Json);
};
